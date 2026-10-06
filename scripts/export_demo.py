#!/usr/bin/env python3
"""Export a recorded, static demo of FrameSeek for GitHub Pages.

Runs the real system (inside the worker container) on a fixed set of example queries,
for every retrieval arm, plus two recorded conversations, and writes plain JSON and
thumbnail files that the web UI's demo mode reads. Videos are not copied: the demo plays
them from their original public sources (FOSDEM / NASA), offset to the excerpt used.

    docker compose exec worker python /app/scripts/export_demo.py --out /data/demo
    docker compose cp worker:/data/demo/. web/public/demo/
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
import uuid
from datetime import datetime, timezone

import httpx
from PIL import Image

from frameseek.config import settings
from frameseek.db import connection
from frameseek.retrieval import search as S
from frameseek.storage import storage

QUERIES = [
    ("why does replication lag between regions", "speech"),
    ("tiup dm deploy command", "on-screen text"),
    ("circuit boards wired to a multimeter", "visual"),
    ("guess the number game running in the terminal", "mixed"),
    ("astronaut demonstrates a lever", "visual + speech"),
    ("what is sharding", "speech"),
    ("power supply, resistor and a bunch of cables", "speech + visual"),
    ("most popular languages in the chatbot repo", "on-screen text"),
    ("friction slows down a moving object", "speech"),
    ("check data consistency between upstream and downstream", "on-screen text"),
]
ARMS = ["E", "A", "B", "C", "D"]  # E first: it is what "auto" serves until a scorer is trained
CHATS = [
    ["where do they show circuit boards wired together", "only the demos",
     "show more context around the second result", "find the same topic in another video"],
    ["what is friction and how does it slow things down", "within this video", "@option:0",
     "show more context around the first result"],
]


def playable_url(src: str) -> str:
    # FOSDEM publishes each talk as AV1 WebM and H.264 MP4 from the same master; MP4 plays everywhere.
    return src.replace(".av1.webm", ".mp4")


class Thumbs:
    def __init__(self, out: pathlib.Path) -> None:
        self.dir = out / "thumbs"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.done: set[int] = set()

    def frame(self, frame_id: int | None) -> str | None:
        if frame_id is None:
            return None
        frame_id = int(frame_id)
        if frame_id not in self.done:
            with connection() as conn:
                row = conn.execute("SELECT thumb_key FROM frames WHERE id = %s", (frame_id,)).fetchone()
            if not row:
                return None
            with Image.open(storage().path(row["thumb_key"])) as im:
                w = 240
                im.convert("RGB").resize((w, int(im.height * w / im.width))).save(self.dir / f"{frame_id}.jpg", quality=70)
            self.done.add(frame_id)
        return f"demo/thumbs/{frame_id}.jpg"


def clean_result(r: dict, thumbs: Thumbs) -> dict:
    r = dict(r)
    r["thumb_url"] = thumbs.frame(r.get("thumb_frame_id"))
    ev = dict(r.get("evidence") or {})
    if ev.get("visual"):
        ev["visual"] = dict(ev["visual"], thumb_url=thumbs.frame(ev["visual"]["frame_id"]))
    r["evidence"] = ev
    for k in ("playback_url", "search_id"):
        r.pop(k, None)
    return r


def export_videos(out: pathlib.Path, thumbs: Thumbs) -> list[dict]:
    manifest = json.loads(storage().path("samples/manifest.json").read_text())
    by_hash = {v["sha256"]: v for v in manifest.values()}
    with connection() as conn:
        rows = conn.execute("""SELECT v.id::text AS id, v.title, v.content_type, v.duration_ms, v.content_hash,
                                      v.attribution, v.license,
                                      (SELECT s.thumb_frame_id FROM segments s WHERE s.video_id = v.id
                                         AND s.index_version = v.active_index_version
                                       ORDER BY abs(s.start_ms - v.duration_ms * 0.15) LIMIT 1) AS poster_frame
                               FROM videos v WHERE v.active_index_version IS NOT NULL ORDER BY v.title""").fetchall()
    vids = []
    for v in rows:
        m = by_hash.get(v["content_hash"])
        if not m:
            print(f"skip {v['title']}: not in the sample manifest (only permitted public samples are exported)")
            continue
        vids.append({
            "id": v["id"], "title": v["title"], "content_type": v["content_type"], "duration_ms": v["duration_ms"],
            "status": "ready", "active_index_version": 1,
            "source_url": playable_url(m["source_url"]), "offset_ms": int((m["excerpt"][0] or 0) * 1000),
            "source_page": m["source_page"], "license": m["license"], "attribution": m["attribution"],
            "poster_url": thumbs.frame(v["poster_frame"]),
        })
    return vids


def export_searches(out: pathlib.Path, thumbs: Thumbs, owner: str, allowed: set[str]) -> list[dict]:
    index = []
    for i, (q, kind) in enumerate(QUERIES):
        entry = {"id": f"q{i:02d}", "query": q, "kind": kind, "arms": {}}
        for arm in ARMS:
            resp = S.search(S.SearchRequest(query=q, owner_id=owner, ranker=arm, k=8, record=False))
            resp["results"] = [clean_result(r, thumbs) for r in resp["results"] if r["video_id"] in allowed]
            resp["search_id"] = f"demo-{entry['id']}-{arm}"
            path = out / "search" / f"{entry['id']}-{arm}.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(resp))
            entry["arms"][arm] = f"demo/search/{entry['id']}-{arm}.json"
        index.append(entry)
        print(f"  {q}")
    return index


def export_chats(thumbs: Thumbs) -> list[dict]:
    api = httpx.Client(base_url=settings().api_url, timeout=60)
    chats = []
    for script in CHATS:
        conv = api.post("/api/conversations", json={}).json()["id"]
        turns, options = [], []
        for text in script:
            resume = text.startswith("@option:")
            if resume:
                opt = options[int(text.split(":")[1])]
                text, shown = opt["value"], opt["label"]
            else:
                shown = text
            r = api.post(f"/api/conversations/{conv}/messages",
                         json={"client_turn_id": str(uuid.uuid4()), "text": text, "resume": resume}).json()
            options = (r.get("clarification") or {}).get("options") or []
            r["results"] = [clean_result(x, thumbs) for x in r.get("results") or []]
            r.pop("search", None)
            turns.append({"user": shown, "response": r})
        chats.append({"title": script[0], "turns": turns})
    return chats


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="/data/demo")
    ap.add_argument("--owner", default="demo")
    a = ap.parse_args()
    out = pathlib.Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    thumbs = Thumbs(out)
    videos = export_videos(out, thumbs)
    allowed = {v["id"] for v in videos}
    print(f"{len(videos)} videos")
    searches = export_searches(out, thumbs, a.owner, allowed)
    chats = export_chats(thumbs)
    meta = {"created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "repo_url": "https://github.com/sohamb17/frameseek", "demo_video_url": "",
            "note": "Recorded demo: every result below was produced by the real FrameSeek pipeline on these sample "
                    "videos and saved as JSON. Live search over your own videos needs the Docker stack."}
    (out / "data.json").write_text(json.dumps({"meta": meta, "videos": videos, "searches": searches, "chats": chats}))
    size = sum(f.stat().st_size for f in out.rglob("*") if f.is_file())
    print(f"wrote {out} ({size / 1e6:.1f} MB, {len(thumbs.done)} thumbnails)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
