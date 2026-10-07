"""Label provenance: unreviewed assistant drafts never reach training or evaluation."""
import json
import os
import pathlib
import uuid

import psycopg
import pytest

from conftest import apply_schema, needs_db

pytestmark = [pytest.mark.db, needs_db]
SCHEMA_DIR = pathlib.Path(os.environ.get("FRAMESEEK_SCHEMA_DIR", pathlib.Path(__file__).parents[2] / "api/internal/migrate/sql"))
OWNER = "labels-test"


@pytest.fixture()
def db(tmp_path, monkeypatch):
    url = os.environ["FRAMESEEK_TEST_DATABASE_URL"]
    monkeypatch.setenv("FRAMESEEK_DATABASE_URL", url)
    from frameseek import config
    from frameseek.eval import labels
    config.settings.cache_clear()
    monkeypatch.setattr(labels, "SPLITS", tmp_path / "splits.json")
    with psycopg.connect(url, autocommit=True) as c:
        apply_schema(c, SCHEMA_DIR)
        c.execute("DELETE FROM eval_queries WHERE owner_id = %s", (OWNER,))
        c.execute("DELETE FROM videos WHERE owner_id = %s", (OWNER,))
        coll = c.execute("INSERT INTO collections (owner_id, name) VALUES (%s, %s) RETURNING id", (OWNER, str(uuid.uuid4()))).fetchone()[0]
        h = uuid.uuid4().hex
        vid = c.execute("INSERT INTO videos (owner_id, collection_id, title, source_kind, content_hash, status) "
                        "VALUES (%s, %s, 'Talk: one', 'upload', %s, 'ready') RETURNING id::text", (OWNER, coll, h)).fetchone()[0]
    yield url, vid, h, tmp_path
    with psycopg.connect(url, autocommit=True) as c:
        c.execute("DELETE FROM eval_queries WHERE owner_id = %s", (OWNER,))
        c.execute("DELETE FROM videos WHERE owner_id = %s", (OWNER,))


def add(url, vid, query, author, reviewed, spot_check=False, verdict=None):
    with psycopg.connect(url, autocommit=True) as c:
        qid = c.execute("INSERT INTO eval_queries (owner_id, query, query_type, author, reviewed_at, spot_check, review_verdict) "
                        "VALUES (%s, %s, 'speech', %s, CASE WHEN %s THEN now() END, %s, %s) RETURNING id::text",
                        (OWNER, query, author, reviewed, spot_check, verdict)).fetchone()[0]
        c.execute("INSERT INTO eval_answers (query_id, video_id, start_ms, end_ms) VALUES (%s, %s, 0, 5000)", (qid, vid))


def test_unreviewed_drafts_are_excluded(db):
    from frameseek.eval import labels
    url, vid, _, _ = db
    add(url, vid, "human", "owner", False)
    add(url, vid, "draft reviewed", "assistant-draft", True)
    add(url, vid, "draft pending", "assistant-draft", False)
    used = {q.query for q in labels.load_queries(OWNER)}
    assert used == {"human", "draft reviewed"}
    p = labels.provenance(labels.load_queries(OWNER, include_unreviewed=True))
    assert (p["owner"], p["assistant_draft_reviewed"], p["assistant_draft_unreviewed"]) == (1, 1, 1)


def test_assistant_labels_spot_check(db):
    from frameseek.eval import labels
    url, vid, _, _ = db
    add(url, vid, "plain", "assistant", False)
    add(url, vid, "checked ok", "assistant", True, True, "accepted")
    add(url, vid, "checked fixed", "assistant", True, True, "corrected")
    add(url, vid, "checked wrong", "assistant", True, True, "rejected")
    add(url, vid, "not checked yet", "assistant", False, True)
    assert labels.spot_check_pending(OWNER) == 1
    used = {q.query for q in labels.load_queries(OWNER)}
    assert used == {"plain", "checked ok", "checked fixed", "not checked yet"}  # rejected never used
    p = labels.provenance(labels.load_queries(OWNER, include_unreviewed=True))
    assert p["assistant"] == 4 and p["assistant_rejected"] == 1
    assert p["spot_check"] == {"sampled": 4, "reviewed": 3, "accepted": 1, "corrected": 1, "rejected": 1}
    line = labels.provenance_line(p)
    assert "spot-checked by the author: 1 accepted as is, 1 corrected, 1 rejected" in line


def test_unknown_author_rejected(db):
    url, vid, _, _ = db
    with pytest.raises(psycopg.errors.CheckViolation):
        add(url, vid, "x", "someone", False)


def test_import_matches_by_title_when_hash_differs(db, monkeypatch):
    from frameseek.eval import labels
    url, vid, h, tmp = db
    f = tmp / "draft.jsonl"
    rows = [
        {"id": str(uuid.uuid4()), "query": "by hash", "query_type": "speech", "author": "assistant-draft",
         "answers": [{"content_hash": h, "video_title": "Talk: one", "start_ms": 0, "end_ms": 4000}]},
        {"id": str(uuid.uuid4()), "query": "by title", "query_type": "speech", "author": "assistant-draft",
         "answers": [{"content_hash": "0" * 64, "video_title": "Talk: one", "start_ms": 0, "end_ms": 4000}]},
        {"id": str(uuid.uuid4()), "query": "missing", "query_type": "speech", "author": "assistant-draft",
         "answers": [{"content_hash": "1" * 64, "video_title": "Not here", "start_ms": 0, "end_ms": 4000}]},
    ]
    f.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    added, skipped = labels.import_labels(OWNER, f)
    assert (added, skipped) == (2, 1)
    assert labels.load_queries(OWNER) == []  # imported drafts wait for review
    assert len(labels.load_queries(OWNER, include_unreviewed=True)) == 2
