"""Ingestion worker: claims jobs from Postgres, runs stages, publishes index versions.

Reliability model (at-least-once execution, idempotent effects):
  * Claim     - `FOR UPDATE SKIP LOCKED` on queued jobs or running jobs whose lease
                expired; claiming increments `attempt` (the attempt generation).
  * Heartbeat - a background thread extends the lease while `attempt` still matches.
                If the update touches 0 rows another attempt owns the job, so this
                worker sets `lost` and stops at the next check.
  * Resume    - after every stage a manifest is upserted; a retry skips stages whose
                manifest matches the config/code hashes and whose outputs still verify.
  * Publish   - one transaction: lock the job row, verify attempt, replace this
                version's rows, flip videos.active_index_version, mark the job done.
                A stale attempt fails the check and writes nothing.

Run: python -m frameseek.worker
"""
from __future__ import annotations

import json
import signal
import sys
import threading
import time
import traceback
from datetime import datetime, timezone

import numpy as np

from .config import CODE_VERSION, index_config, settings, tool_versions
from .db import connection, pool
from .pipeline.stages import (STAGE_FUNCS, STAGES, UI_STAGE, JobCtx, LeaseLost, PermanentError,
                              StageResult, _read_json)
from .storage import storage


class StaleAttempt(LeaseLost):
    pass


def log(msg: str, **kw) -> None:
    extra = " ".join(f"{k}={v}" for k, v in kw.items())
    print(f"{datetime.now(timezone.utc).isoformat(timespec='seconds')} [worker] {msg} {extra}", flush=True)


# --------------------------------------------------------------------------- claiming
CLAIM_SQL = """
UPDATE jobs SET status = 'running', attempt = attempt + 1, lease_owner = %(w)s,
       lease_expires_at = now() + make_interval(secs => %(lease)s), heartbeat_at = now(),
       started_at = coalesce(started_at, now()), updated_at = now(), error = NULL
WHERE id = (
    SELECT id FROM jobs
    WHERE (status = 'queued' AND not_before <= now())
       OR (status = 'running' AND lease_expires_at < now())
    ORDER BY created_at
    FOR UPDATE SKIP LOCKED
    LIMIT 1)
RETURNING *
"""


def claim() -> dict | None:
    s = settings()
    with connection() as conn:
        job = conn.execute(CLAIM_SQL, {"w": s.worker_id, "lease": s.lease_seconds}).fetchone()
        if job:
            conn.execute("UPDATE videos SET status = 'processing', updated_at = now() WHERE id = %s",
                         (job["video_id"],))
        conn.commit()
        return job


class Heartbeat(threading.Thread):
    def __init__(self, job_id: str, attempt: int, lost: threading.Event) -> None:
        super().__init__(daemon=True)
        self.job_id, self.attempt, self.lost = job_id, attempt, lost
        self.stop = threading.Event()

    def run(self) -> None:
        s = settings()
        while not self.stop.wait(s.heartbeat_seconds):
            try:
                with connection() as conn:
                    cur = conn.execute(
                        "UPDATE jobs SET lease_expires_at = now() + make_interval(secs => %s), heartbeat_at = now() "
                        "WHERE id = %s AND attempt = %s AND status = 'running'",
                        (s.lease_seconds, self.job_id, self.attempt))
                    conn.commit()
                    if cur.rowcount == 0:
                        log("lease lost", job=self.job_id, attempt=self.attempt)
                        self.lost.set()
                        return
            except Exception as e:  # transient DB error: keep trying until the lease runs out
                log(f"heartbeat error: {e}")


class Reporter:
    """Throttled, attempt-guarded progress updates."""

    def __init__(self, job_id: str, attempt: int) -> None:
        self.job_id, self.attempt = job_id, attempt
        self.last = 0.0

    def __call__(self, stage: str, frac: float, force: bool = False) -> None:
        now = time.time()
        if not force and now - self.last < 1.0:
            return
        self.last = now
        with connection() as conn:
            conn.execute("UPDATE jobs SET stage = %s, stage_progress = %s, updated_at = now() "
                         "WHERE id = %s AND attempt = %s AND status = 'running'",
                         (stage, round(float(frac), 3), self.job_id, self.attempt))
            conn.commit()


# --------------------------------------------------------------------------- manifests
def manifest_valid(ctx: JobCtx, stage: str) -> dict | None:
    with connection() as conn:
        m = conn.execute("SELECT * FROM stage_manifests WHERE video_id = %s AND index_version = %s AND stage = %s",
                         (ctx.video_id, ctx.index_version, stage)).fetchone()
    if not m or m["config_hash"] != ctx.config_hash or m["code_version"] != CODE_VERSION:
        return None
    st = storage()
    for key, sha in m["outputs"].items():
        if not st.exists(key) or st.sha256(key) != sha:
            return None
    return m


def write_manifest(ctx: JobCtx, stage: str, res: StageResult, started: float) -> None:
    versions = dict(tool_versions())
    cfg = ctx.config
    versions.update({
        "asr": f"{cfg['asr']['model']}@{cfg['asr']['revision'][:12]}",
        "text_encoder": f"{cfg['text_encoder']['model']}@{cfg['text_encoder']['revision'][:12]}",
        "visual_encoder": f"{cfg['visual_encoder']['model']}/{cfg['visual_encoder']['pretrained']}"
                          f"@{cfg['visual_encoder']['hf_revision'][:12]}",
    })
    metrics = dict(res.metrics)
    metrics["wall_seconds"] = round(time.time() - started, 3)
    with connection() as conn:
        cur = conn.execute("SELECT attempt FROM jobs WHERE id = %s FOR UPDATE", (ctx.job_id,)).fetchone()
        if cur["attempt"] != ctx.attempt:
            conn.rollback()
            raise StaleAttempt(f"attempt {ctx.attempt} superseded by {cur['attempt']}")
        conn.execute("""
            INSERT INTO stage_manifests (video_id, index_version, stage, job_id, attempt, input_hash, config_hash,
                                         code_version, versions, outputs, metrics, started_at, finished_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,to_timestamp(%s), now())
            ON CONFLICT (video_id, index_version, stage) DO UPDATE SET
              job_id = EXCLUDED.job_id, attempt = EXCLUDED.attempt, input_hash = EXCLUDED.input_hash,
              config_hash = EXCLUDED.config_hash, code_version = EXCLUDED.code_version,
              versions = EXCLUDED.versions, outputs = EXCLUDED.outputs, metrics = EXCLUDED.metrics,
              started_at = EXCLUDED.started_at, finished_at = now()
        """, (ctx.video_id, ctx.index_version, stage, ctx.job_id, ctx.attempt, res.input_hash, ctx.config_hash,
              CODE_VERSION, json.dumps(versions), json.dumps(res.outputs), json.dumps(metrics), started))
        conn.commit()


# --------------------------------------------------------------------------- index + publish
def index_and_publish(ctx: JobCtx) -> dict:
    """Validate all outputs, write the version's rows and flip the active pointer atomically."""
    st = storage()
    pm = _read_json(f"videos/{ctx.video_id}/playback.json")
    fr = _read_json(f"{ctx.key}/frames.json")
    frames = fr["frames"]
    asr_data = _read_json(f"{ctx.key}/asr.json")
    ocr_by_idx = {r["idx"]: r for r in _read_json(f"{ctx.key}/ocr.json")["frames"]}
    segs = _read_json(f"{ctx.key}/segments.json")["segments"]
    emb = np.load(st.path(f"{ctx.key}/embeddings.npz"))
    cfg = ctx.config
    tdim, vdim = cfg["text_encoder"]["dim"], cfg["visual_encoder"]["dim"]

    # ---- validation: never publish a partial or inconsistent version
    problems = []
    if not segs:
        problems.append("no segments")
    if emb["v_emb"].shape != (len(frames), vdim):
        problems.append(f"frame vectors {emb['v_emb'].shape} != ({len(frames)}, {vdim})")
    if emb["t_emb"].shape[0] != len(emb["t_idx"]) or (len(emb["t_idx"]) and emb["t_emb"].shape[1] != tdim):
        problems.append("transcript vectors inconsistent")
    if emb["o_emb"].shape[0] != len(emb["o_idx"]) or (len(emb["o_idx"]) and emb["o_emb"].shape[1] != tdim):
        problems.append("ocr vectors inconsistent")
    if set(ocr_by_idx) != {f["idx"] for f in frames}:
        problems.append("ocr does not cover every frame")
    if problems:
        raise RuntimeError("validation failed: " + "; ".join(problems))

    text_model = cfg["text_encoder"]["model"]
    vis_model = f"{cfg['visual_encoder']['model']}/{cfg['visual_encoder']['pretrained']}"
    with connection() as conn:
        with conn.transaction():
            row = conn.execute("SELECT attempt, status FROM jobs WHERE id = %s FOR UPDATE", (ctx.job_id,)).fetchone()
            if row["attempt"] != ctx.attempt or row["status"] != "running":
                raise StaleAttempt(f"attempt {ctx.attempt} is stale (current {row['attempt']}, {row['status']})")
            v, vid = ctx.index_version, ctx.video_id
            # Idempotent re-run of this version: clear any rows a previous attempt left behind.
            conn.execute("DELETE FROM segments WHERE video_id = %s AND index_version = %s", (vid, v))
            conn.execute("DELETE FROM frames WHERE video_id = %s AND index_version = %s", (vid, v))
            conn.execute("DELETE FROM asr_segments WHERE video_id = %s AND index_version = %s", (vid, v))

            with conn.cursor() as cur:
                cur.executemany(
                    "INSERT INTO asr_segments (video_id, index_version, idx, start_ms, end_ms, text, avg_logprob, "
                    "no_speech_prob, words) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    [(vid, v, i, int(s["start"] * 1000), int(s["end"] * 1000), s["text"], s["avg_logprob"],
                      s["no_speech_prob"], json.dumps(s["words"])) for i, s in enumerate(asr_data["segments"])])

            frame_ids: dict[int, int] = {}
            for f in frames:
                o = ocr_by_idx[f["idx"]]
                fid = conn.execute(
                    "INSERT INTO frames (video_id, index_version, idx, ts_ms, image_key, thumb_key, is_transition, "
                    "diff_score, ocr_text, ocr_conf, ocr_lines, ocr_reused) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                    "RETURNING id",
                    (vid, v, f["idx"], f["ts_ms"], f["image_key"], f["thumb_key"], f["is_transition"], f["diff"],
                     o["text"], o["conf"], json.dumps(o["lines"]), o["reused"])).fetchone()["id"]
                frame_ids[f["idx"]] = fid
            with conn.cursor() as cur:
                cur.executemany("INSERT INTO frame_embeddings (frame_id, model, embedding) VALUES (%s,%s,%s)",
                                [(frame_ids[f["idx"]], vis_model, emb["v_emb"][i]) for i, f in enumerate(frames)])

            seg_ids: list[int] = []
            for s in segs:
                sid = conn.execute(
                    "INSERT INTO segments (video_id, index_version, idx, start_ms, end_ms, transcript, ocr_text, "
                    "speech_coverage, ocr_coverage, ocr_stability, n_frames, thumb_frame_id) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",
                    (vid, v, s["idx"], s["start_ms"], s["end_ms"], s["transcript"], s["ocr_text"],
                     s["speech_coverage"], s["ocr_coverage"], s["ocr_stability"], len(s["frame_idxs"]),
                     frame_ids.get(s["thumb_frame_idx"]) if s["thumb_frame_idx"] is not None else None)
                ).fetchone()["id"]
                seg_ids.append(sid)
            rows = [(seg_ids[i], "transcript", text_model, emb["t_emb"][j]) for j, i in enumerate(emb["t_idx"])]
            rows += [(seg_ids[i], "ocr", text_model, emb["o_emb"][j]) for j, i in enumerate(emb["o_idx"])]
            with conn.cursor() as cur:
                cur.executemany("INSERT INTO segment_embeddings (segment_id, kind, model, embedding) "
                                "VALUES (%s,%s,%s,%s)", rows)

            # ---- publish: the only moment searches can start seeing version v
            conn.execute("UPDATE index_versions SET status = 'superseded' "
                         "WHERE video_id = %s AND status = 'ready' AND version <> %s", (vid, v))
            conn.execute("UPDATE index_versions SET status = 'ready', published_at = now() "
                         "WHERE video_id = %s AND version = %s", (vid, v))
            conn.execute("""
                UPDATE videos SET active_index_version = %s, status = 'ready', error = NULL,
                       playback_key = %s, poster_key = %s, duration_ms = %s, width = %s, height = %s,
                       has_audio = %s, probe = %s, updated_at = now()
                WHERE id = %s""",
                (v, pm["playback_key"], fr["poster"], pm["duration_ms"], pm["width"], pm["height"],
                 pm["has_audio"], json.dumps({"original": pm["original"], "playback": pm["playback"],
                                             "derived_playback": pm["derived"]}), vid))
            conn.execute("UPDATE jobs SET status = 'succeeded', stage = 'ready', stage_progress = 1, "
                         "finished_at = now(), updated_at = now(), lease_owner = NULL WHERE id = %s", (ctx.job_id,))
    return {"segments": len(segs), "frames": len(frames), "asr_segments": len(asr_data["segments"])}


# --------------------------------------------------------------------------- failure handling
def fail_or_retry(job: dict, err: str, permanent: bool) -> None:
    with connection() as conn:
        with conn.transaction():
            row = conn.execute("SELECT attempt, max_attempts, status FROM jobs WHERE id = %s FOR UPDATE",
                               (job["id"],)).fetchone()
            if row["attempt"] != job["attempt"] or row["status"] != "running":
                return  # stale: a newer attempt owns the job
            if not permanent and row["attempt"] < row["max_attempts"]:
                backoff = 5 * 2 ** (row["attempt"] - 1)
                conn.execute("UPDATE jobs SET status = 'queued', lease_owner = NULL, error = %s, "
                             "not_before = now() + make_interval(secs => %s), updated_at = now() WHERE id = %s",
                             (err[:2000], backoff, job["id"]))
                conn.execute("UPDATE videos SET status = 'queued', updated_at = now() WHERE id = %s", (job["video_id"],))
                log("retry scheduled", job=job["id"], attempt=row["attempt"], backoff_s=backoff)
                return
            conn.execute("UPDATE jobs SET status = 'failed', error = %s, finished_at = now(), lease_owner = NULL, "
                         "updated_at = now() WHERE id = %s", (err[:2000], job["id"]))
            conn.execute("UPDATE index_versions SET status = 'failed' WHERE video_id = %s AND version = %s",
                         (job["video_id"], job["index_version"]))
            # A failed reindex keeps the previous version searchable.
            conn.execute("""UPDATE videos SET error = %s, updated_at = now(),
                              status = CASE WHEN active_index_version IS NULL THEN 'failed' ELSE 'ready' END
                            WHERE id = %s""", (err[:2000], job["video_id"]))
            log("job failed", job=job["id"], error=err[:200])


# --------------------------------------------------------------------------- run one job
def run_job(job: dict) -> None:
    cfg, cfg_hash = index_config()
    with connection() as conn:
        video = conn.execute("SELECT * FROM videos WHERE id = %s", (job["video_id"],)).fetchone()
        iv = conn.execute("SELECT config_hash FROM index_versions WHERE video_id = %s AND version = %s",
                          (job["video_id"], job["index_version"])).fetchone()
    if iv and iv["config_hash"] != cfg_hash:
        # The job was created for a different config file than this worker has loaded.
        fail_or_retry(job, f"config mismatch: job expects {iv['config_hash'][:12]}, worker has {cfg_hash[:12]}", True)
        return
    lost = threading.Event()
    reporter = Reporter(str(job["id"]), job["attempt"])
    ctx = JobCtx(job_id=str(job["id"]), video_id=str(job["video_id"]), owner_id=job["owner_id"],
                 index_version=job["index_version"], attempt=job["attempt"], config=cfg, config_hash=cfg_hash,
                 video=video, lost=lost, report=lambda st, f: reporter(UI_STAGE.get(st, st), f))
    hb = Heartbeat(ctx.job_id, ctx.attempt, lost)
    hb.start()
    log("claimed", job=ctx.job_id, video=ctx.video_id, version=ctx.index_version, attempt=ctx.attempt)
    try:
        for stage in STAGES:
            ctx.check()
            reporter(UI_STAGE[stage], 0.0, force=True)
            if stage == "index":
                counts = index_and_publish(ctx)
                log("published", job=ctx.job_id, version=ctx.index_version, **counts)
                break
            if manifest_valid(ctx, stage):
                log("stage skipped (manifest valid)", job=ctx.job_id, stage=stage)
                continue
            started = time.time()
            res = STAGE_FUNCS[stage](ctx)
            ctx.check()
            write_manifest(ctx, stage, res, started)
            log("stage done", job=ctx.job_id, stage=stage, seconds=round(time.time() - started, 1))
    except LeaseLost as e:
        log(f"abandoning job: {e}", job=ctx.job_id)
    except PermanentError as e:
        fail_or_retry(job, str(e), permanent=True)
    except Exception as e:
        traceback.print_exc()
        fail_or_retry(job, f"{type(e).__name__}: {e}", permanent=False)
    finally:
        hb.stop.set()


def main() -> int:
    s = settings()
    stopping = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stopping.set())
    pool()  # waits for the schema
    log("started", worker=s.worker_id, lease_s=s.lease_seconds)
    while not stopping.is_set():
        try:
            job = claim()
        except Exception as e:
            log(f"claim error: {e}")
            time.sleep(s.poll_seconds)
            continue
        if job is None:
            stopping.wait(s.poll_seconds)
            continue
        if job["attempt"] > job["max_attempts"]:
            fail_or_retry(job, "exceeded max attempts (lease expired repeatedly)", permanent=True)
            continue
        run_job(job)
    log("stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
