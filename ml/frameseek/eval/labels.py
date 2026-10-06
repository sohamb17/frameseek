"""Relevance labels and frozen splits.

Labels are written in the web UI (Label tab) into Postgres. This module moves them to and
from a portable JSONL file that references videos by content SHA-256 (not database ids), so
the label set can be committed to Git and re-imported after re-ingesting the same media.

Splits are assigned per *group* of videos (a series or a single recording), never per query:
queries about one recording, or one series, can never land on both sides of a split.

    python -m frameseek.eval.labels status
    python -m frameseek.eval.labels export            # DB -> eval/labels.jsonl
    python -m frameseek.eval.labels import            # eval/labels.jsonl -> DB
    python -m frameseek.eval.labels freeze-splits     # write eval/splits.json once
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone

from ..db import connection
from .metrics import Interval

EVAL_DIR = pathlib.Path(os.environ.get("FRAMESEEK_EVAL_DIR", "/app/eval"))
LABELS = EVAL_DIR / "labels.jsonl"
SPLITS = EVAL_DIR / "splits.json"


@dataclass
class LabeledQuery:
    id: str
    query: str
    query_type: str
    answers: list[Interval]
    notes: str = ""
    split: str = "unassigned"
    groups: list[str] = field(default_factory=list)


def default_group(title: str, content_hash: str) -> str:
    """Series heuristic: "STEMonstrations: Friction" and "STEMonstrations: Simple Machines" share a group."""
    if ":" in title:
        return title.split(":", 1)[0].strip().lower()
    return content_hash[:12]


def _videos(conn, owner: str) -> list[dict]:
    return conn.execute("SELECT id::text, title, content_hash FROM videos WHERE owner_id = %s AND content_hash IS NOT NULL",
                        (owner,)).fetchall()


def load_splits() -> dict | None:
    return json.loads(SPLITS.read_text()) if SPLITS.exists() else None


def video_split_map(owner: str) -> tuple[dict[str, str], dict[str, str]]:
    """Return (video_id -> split, video_id -> group) using the frozen splits file."""
    splits = load_splits()
    with connection() as conn:
        vids = _videos(conn, owner)
    by_hash = {}
    if splits:
        for g, info in splits["groups"].items():
            for v in info["videos"]:
                by_hash[v["content_hash"]] = (info["split"], g)
    split_of, group_of = {}, {}
    for v in vids:
        s, g = by_hash.get(v["content_hash"], ("unassigned", default_group(v["title"], v["content_hash"])))
        split_of[v["id"]], group_of[v["id"]] = s, g
    return split_of, group_of


def load_queries(owner: str) -> list[LabeledQuery]:
    split_of, group_of = video_split_map(owner)
    splits = load_splits() or {}
    with connection() as conn:
        rows = conn.execute("""
            SELECT q.id::text, q.query, q.query_type, q.notes,
                   coalesce(json_agg(json_build_object('video_id', a.video_id, 'start_ms', a.start_ms, 'end_ms', a.end_ms))
                            FILTER (WHERE a.id IS NOT NULL), '[]') AS answers
            FROM eval_queries q LEFT JOIN eval_answers a ON a.query_id = q.id
            WHERE q.owner_id = %s GROUP BY q.id ORDER BY q.created_at""", (owner,)).fetchall()
    out = []
    for r in rows:
        answers = [Interval(a["video_id"], a["start_ms"], a["end_ms"]) for a in r["answers"]]
        if answers:
            ss = {split_of.get(a.video_id, "unassigned") for a in answers}
            split = ss.pop() if len(ss) == 1 else "crosses-splits"
            groups = sorted({group_of.get(a.video_id, "?") for a in answers})
        else:  # no-answer queries: deterministic assignment by query id with the frozen ratios
            h = int(hashlib.sha256(r["id"].encode()).hexdigest(), 16) % 100
            ratios = splits.get("ratios", {"test": 0.2, "dev": 0.2})
            split = "test" if h < ratios["test"] * 100 else "dev" if h < (ratios["test"] + ratios["dev"]) * 100 else "train"
            groups = [f"no-answer:{r['id'][:8]}"]
        out.append(LabeledQuery(r["id"], r["query"], r["query_type"], answers, r["notes"], split, groups))
    return out


def export(owner: str) -> int:
    with connection() as conn:
        hashes = {v["id"]: v for v in _videos(conn, owner)}
    n = 0
    EVAL_DIR.mkdir(parents=True, exist_ok=True)
    with LABELS.open("w") as f:
        for q in load_queries(owner):
            f.write(json.dumps({
                "id": q.id, "query": q.query, "query_type": q.query_type, "notes": q.notes,
                "answers": [{"content_hash": hashes[a.video_id]["content_hash"], "video_title": hashes[a.video_id]["title"],
                             "start_ms": a.start_ms, "end_ms": a.end_ms} for a in q.answers],
            }) + "\n")
            n += 1
    return n


def import_labels(owner: str) -> tuple[int, int]:
    added = skipped = 0
    with connection() as conn:
        by_hash = {v["content_hash"]: v["id"] for v in _videos(conn, owner)}
        for line in LABELS.read_text().splitlines():
            if not line.strip():
                continue
            d = json.loads(line)
            if conn.execute("SELECT 1 FROM eval_queries WHERE id = %s", (d["id"],)).fetchone():
                skipped += 1
                continue
            if any(a["content_hash"] not in by_hash for a in d["answers"]):
                print(f"skip {d['query']!r}: a referenced video is not ingested", file=sys.stderr)
                skipped += 1
                continue
            conn.execute("INSERT INTO eval_queries (id, owner_id, query, query_type, notes) VALUES (%s,%s,%s,%s,%s)",
                         (d["id"], owner, d["query"], d["query_type"], d.get("notes", "")))
            for a in d["answers"]:
                conn.execute("INSERT INTO eval_answers (query_id, video_id, start_ms, end_ms) VALUES (%s,%s,%s,%s)",
                             (d["id"], by_hash[a["content_hash"]], a["start_ms"], a["end_ms"]))
            added += 1
        conn.commit()
    return added, skipped


def freeze_splits(owner: str, dev: float, test: float, seed: int, force: bool) -> dict:
    if SPLITS.exists() and not force:
        raise SystemExit(f"{SPLITS} already exists; splits are frozen (use --force only before any model selection)")
    with connection() as conn:
        vids = _videos(conn, owner)
    groups: dict[str, list[dict]] = {}
    for v in vids:
        groups.setdefault(default_group(v["title"], v["content_hash"]), []).append(
            {"content_hash": v["content_hash"], "title": v["title"]})
    keys = sorted(groups, key=lambda g: hashlib.sha256(f"{seed}:{g}".encode()).hexdigest())
    n = len(keys)
    n_test = max(1, round(n * test)) if n >= 3 else 0
    n_dev = max(1, round(n * dev)) if n >= 3 else 0
    assign = {}
    for i, g in enumerate(keys):
        assign[g] = "test" if i < n_test else "dev" if i < n_test + n_dev else "train"
    out = {
        "version": 1, "created": datetime.now(timezone.utc).isoformat(timespec="seconds"), "seed": seed,
        "ratios": {"dev": dev, "test": test},
        "note": "Split by video group (series or single recording). Edit groups before the first model selection only.",
        "groups": {g: {"split": assign[g], "videos": groups[g]} for g in keys},
    }
    EVAL_DIR.mkdir(parents=True, exist_ok=True)
    SPLITS.write_text(json.dumps(out, indent=2))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(prog="frameseek.eval.labels")
    ap.add_argument("command", choices=["status", "export", "import", "freeze-splits"])
    ap.add_argument("--owner", default="demo")
    ap.add_argument("--dev", type=float, default=0.2)
    ap.add_argument("--test", type=float, default=0.2)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()
    if a.command == "export":
        print(f"exported {export(a.owner)} queries to {LABELS}")
    elif a.command == "import":
        added, skipped = import_labels(a.owner)
        print(f"imported {added}, skipped {skipped}")
    elif a.command == "freeze-splits":
        out = freeze_splits(a.owner, a.dev, a.test, a.seed, a.force)
        for g, info in out["groups"].items():
            print(f"{info['split']:6s} {g:32s} {len(info['videos'])} video(s)")
    else:
        qs = load_queries(a.owner)
        counts: dict[tuple[str, str], int] = {}
        for q in qs:
            counts[(q.split, q.query_type)] = counts.get((q.split, q.query_type), 0) + 1
        print(f"{len(qs)} labeled queries; splits file: {'yes' if SPLITS.exists() else 'NOT FROZEN'}")
        for (s, t), c in sorted(counts.items()):
            print(f"  {s:15s} {t:10s} {c}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
