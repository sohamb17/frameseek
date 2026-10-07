"""Reliability properties of the ingestion worker, against a real (throwaway) Postgres.

    FRAMESEEK_TEST_DATABASE_URL=postgresql://frameseek:frameseek@localhost:5432/frameseek_test pytest -m db

Never point this at a database with real jobs: claim() takes any queued job.
"""
import json
import os
import pathlib
import threading
import uuid

import numpy as np
import psycopg
import pytest

from conftest import apply_schema, needs_db

pytestmark = [pytest.mark.db, needs_db]
SCHEMA_DIR = pathlib.Path(os.environ.get("FRAMESEEK_SCHEMA_DIR", pathlib.Path(__file__).parents[2] / "api/internal/migrate/sql"))


@pytest.fixture(scope="module")
def env(tmp_path_factory):
    url = os.environ["FRAMESEEK_TEST_DATABASE_URL"]
    os.environ["FRAMESEEK_DATABASE_URL"] = url
    with psycopg.connect(url, autocommit=True) as c:
        apply_schema(c, SCHEMA_DIR)
        c.execute("TRUNCATE jobs, videos, collections CASCADE")
    from frameseek import config, storage
    config.settings.cache_clear()
    storage._storage = storage.LocalStorage(tmp_path_factory.mktemp("data"))
    return url


def make_video(url, version=1):
    with psycopg.connect(url, autocommit=True) as c:
        coll = c.execute("INSERT INTO collections (owner_id, name) VALUES ('t', %s) RETURNING id", (str(uuid.uuid4()),)).fetchone()[0]
        vid = c.execute("INSERT INTO videos (owner_id, collection_id, title, source_kind, content_hash, status) "
                        "VALUES ('t', %s, 'v', 'upload', %s, 'queued') RETURNING id", (coll, uuid.uuid4().hex)).fetchone()[0]
        return str(vid)


def add_job(url, vid, version):
    with psycopg.connect(url, autocommit=True) as c:
        c.execute("INSERT INTO index_versions (video_id, version, config_hash, config) VALUES (%s,%s,'h','{}')", (vid, version))
        return str(c.execute("INSERT INTO jobs (video_id, owner_id, idempotency_key, index_version) VALUES (%s,'t',%s,%s) "
                             "RETURNING id", (vid, uuid.uuid4().hex, version)).fetchone()[0])


def write_artifacts(vid, version, n_frames=3):
    from frameseek.storage import storage
    st = storage()
    st.write_bytes(f"videos/{vid}/playback.json", json.dumps({
        "playback_key": "x.mp4", "derived": False, "duration_ms": 30000, "width": 1280, "height": 720,
        "has_audio": True, "original": {}, "playback": {}}).encode())
    k = f"videos/{vid}/v{version}"
    frames = [{"idx": i, "ts_ms": i * 10000, "image_key": f"{k}/frames/{i}.jpg", "thumb_key": f"{k}/thumbs/{i}.jpg",
               "diff": 0.5, "is_transition": True} for i in range(n_frames)]
    st.write_bytes(f"{k}/frames.json", json.dumps({"frames": frames, "poster": frames[0]["thumb_key"]}).encode())
    st.write_bytes(f"{k}/asr.json", json.dumps({"state": "ok", "segments": [
        {"start": 0, "end": 2, "text": "hello", "avg_logprob": -0.1, "no_speech_prob": 0.0, "words": []}]}).encode())
    st.write_bytes(f"{k}/ocr.json", json.dumps({"frames": [
        {"idx": i, "text": "", "lines": [], "n_words": 0, "conf": None, "reused": False} for i in range(n_frames)]}).encode())
    segs = [{"idx": i, "start_ms": i * 10000, "end_ms": i * 10000 + 20000, "transcript": "hello", "ocr_text": "",
             "speech_coverage": 0.1, "ocr_coverage": 0, "ocr_stability": 0, "frame_idxs": [i], "thumb_frame_idx": i}
            for i in range(2)]
    st.write_bytes(f"{k}/segments.json", json.dumps({"segments": segs}).encode())
    p = st.ensure_parent(f"{k}/embeddings.npz")
    np.savez(p, t_idx=np.array([0, 1]), t_emb=np.ones((2, 384), np.float32) / 20, o_idx=np.array([], dtype=np.int32),
             o_emb=np.zeros((0, 384), np.float32), v_emb=np.ones((n_frames, 512), np.float32) / 23)


def ctx_for(job, vid, version, attempt):
    from frameseek.pipeline.stages import JobCtx
    cfg = {"text_encoder": {"model": "m", "dim": 384}, "visual_encoder": {"model": "c", "pretrained": "p", "dim": 512}}
    return JobCtx(job_id=job, video_id=vid, owner_id="t", index_version=version, attempt=attempt, config=cfg,
                  config_hash="h", video={}, lost=threading.Event(), report=lambda *a: None)


def state(url, vid):
    with psycopg.connect(url) as c:
        return c.execute("SELECT active_index_version, status FROM videos WHERE id = %s", (vid,)).fetchone()


def test_claim_lease_and_reclaim(env):
    from frameseek import worker
    vid = make_video(env)
    job = add_job(env, vid, 1)
    first = worker.claim()
    assert str(first["id"]) == job and first["attempt"] == 1
    assert worker.claim() is None                       # still leased: nobody else may take it
    with psycopg.connect(env, autocommit=True) as c:    # simulate a dead worker: lease expires
        c.execute("UPDATE jobs SET lease_expires_at = now() - interval '1 second' WHERE id = %s", (job,))
    again = worker.claim()
    assert str(again["id"]) == job and again["attempt"] == 2


def test_stale_attempt_cannot_publish_or_fail_the_job(env):
    from frameseek import worker
    vid = make_video(env)
    job = add_job(env, vid, 1)
    with psycopg.connect(env, autocommit=True) as c:
        c.execute("UPDATE jobs SET status = 'running', attempt = 2 WHERE id = %s", (job,))
    write_artifacts(vid, 1)
    with pytest.raises(worker.StaleAttempt):
        worker.index_and_publish(ctx_for(job, vid, 1, attempt=1))
    worker.fail_or_retry({"id": job, "attempt": 1, "video_id": vid, "index_version": 1}, "boom", permanent=True)
    with psycopg.connect(env) as c:
        assert c.execute("SELECT status FROM jobs WHERE id = %s", (job,)).fetchone()[0] == "running"
        assert c.execute("SELECT count(*) FROM segments WHERE video_id = %s", (vid,)).fetchone()[0] == 0
    assert state(env, vid)[0] is None


def test_publish_is_atomic_versioned_and_failed_reindex_keeps_old_version(env):
    from frameseek import worker
    vid = make_video(env)
    j1 = add_job(env, vid, 1)
    with psycopg.connect(env, autocommit=True) as c:
        c.execute("UPDATE jobs SET status = 'running', attempt = 1 WHERE id = %s", (j1,))
    write_artifacts(vid, 1)
    worker.index_and_publish(ctx_for(j1, vid, 1, 1))
    assert state(env, vid) == (1, "ready")

    j2 = add_job(env, vid, 2)
    with psycopg.connect(env, autocommit=True) as c:
        c.execute("UPDATE jobs SET status = 'running', attempt = 1 WHERE id = %s", (j2,))
    worker.fail_or_retry({"id": j2, "attempt": 1, "video_id": vid, "index_version": 2}, "injected", permanent=True)
    assert state(env, vid) == (1, "ready")             # still searchable on v1
    with psycopg.connect(env) as c:
        assert c.execute("SELECT status FROM index_versions WHERE video_id = %s AND version = 2", (vid,)).fetchone()[0] == "failed"

    j3 = add_job(env, vid, 3)
    with psycopg.connect(env, autocommit=True) as c:
        c.execute("UPDATE jobs SET status = 'running', attempt = 1 WHERE id = %s", (j3,))
    write_artifacts(vid, 3)
    worker.index_and_publish(ctx_for(j3, vid, 3, 1))
    assert state(env, vid) == (3, "ready")
    with psycopg.connect(env) as c:
        rows = dict(c.execute("SELECT version, status FROM index_versions WHERE video_id = %s", (vid,)).fetchall())
        assert rows == {1: "superseded", 2: "failed", 3: "ready"}
        # old version rows are retained for in-flight searches and bookmarks
        assert c.execute("SELECT count(*) FROM segments WHERE video_id = %s AND index_version = 1", (vid,)).fetchone()[0] == 2


def test_validation_refuses_inconsistent_outputs(env):
    from frameseek import worker
    from frameseek.storage import storage
    vid = make_video(env)
    job = add_job(env, vid, 1)
    with psycopg.connect(env, autocommit=True) as c:
        c.execute("UPDATE jobs SET status = 'running', attempt = 1 WHERE id = %s", (job,))
    write_artifacts(vid, 1, n_frames=3)
    p = storage().path(f"videos/{vid}/v1/embeddings.npz")
    d = dict(np.load(p))
    d["v_emb"] = d["v_emb"][:2]                         # one frame vector missing
    np.savez(p, **d)
    with pytest.raises(RuntimeError, match="validation failed"):
        worker.index_and_publish(ctx_for(job, vid, 1, 1))
    assert state(env, vid)[0] is None
