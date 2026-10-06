#!/usr/bin/env python3
"""Upload the fetched sample library to a running FrameSeek API (stdlib only).

    python scripts/seed_demo.py --api http://localhost:8080 --samples ./data/samples
    # or, inside the worker container:
    python /app/scripts/seed_demo.py --api http://api:8080 --samples /data/samples

Uploads stream from disk (no full-file buffering) and are idempotent: the API recognises
identical bytes by SHA-256 and returns the existing video instead of a duplicate.
"""
from __future__ import annotations

import argparse
import http.client
import json
import os
import pathlib
import sys
import urllib.parse
import uuid


def upload(api: str, path: pathlib.Path, fields: dict[str, str], token: str | None) -> dict:
    u = urllib.parse.urlparse(api)
    boundary = uuid.uuid4().hex
    pre = b""
    for k, v in fields.items():
        pre += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n").encode()
    pre += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{path.name}\"\r\n"
            f"Content-Type: application/octet-stream\r\n\r\n").encode()
    post = f"\r\n--{boundary}--\r\n".encode()
    size = len(pre) + path.stat().st_size + len(post)
    conn_cls = http.client.HTTPSConnection if u.scheme == "https" else http.client.HTTPConnection
    conn = conn_cls(u.hostname, u.port or (443 if u.scheme == "https" else 80), timeout=600)
    conn.putrequest("POST", "/api/videos")
    conn.putheader("Content-Type", f"multipart/form-data; boundary={boundary}")
    conn.putheader("Content-Length", str(size))
    if token:
        conn.putheader("Authorization", f"Bearer {token}")
    conn.endheaders()
    conn.send(pre)
    with path.open("rb") as f:
        while chunk := f.read(1 << 20):
            conn.send(chunk)
    conn.send(post)
    resp = conn.getresponse()
    body = resp.read().decode()
    if resp.status >= 300:
        raise RuntimeError(f"{path.name}: HTTP {resp.status}: {body}")
    return json.loads(body)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--api", default=os.environ.get("FRAMESEEK_API_URL", "http://localhost:8080"))
    ap.add_argument("--samples", default="./data/samples")
    ap.add_argument("--token", default=os.environ.get("FRAMESEEK_TOKEN"))
    ap.add_argument("--only", default="")
    args = ap.parse_args()
    root = pathlib.Path(args.samples)
    manifest = json.loads((root / "manifest.json").read_text())
    only = {s for s in args.only.split(",") if s}
    for slug, m in manifest.items():
        if only and slug not in only:
            continue
        res = upload(args.api, root / m["file"], {
            "title": m["title"], "content_type": m["content_type"], "license": m["license"],
            "attribution": m["attribution"], "source_url": m["source_page"],
        }, args.token)
        v = res["video"]
        state = "already present" if res.get("deduplicated") else f"queued job {res.get('job', {}).get('id', '-')}"
        print(f"{slug:24s} -> video {v['id']} ({state})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
