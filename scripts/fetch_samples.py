#!/usr/bin/env python3
"""Download the permitted sample library described in samples.json.

Each entry is downloaded from a direct HTTPS media URL (resumable, with retries),
optionally trimmed to an excerpt locally with FFmpeg, and written to <out>/<slug>.<ext>. A sidecar manifest
(<out>/manifest.json) records the source URL, license, attribution, excerpt
window and SHA-256 of every produced file so the demo corpus is reproducible.

Usage (inside the worker container, which has FFmpeg):
    python /app/scripts/fetch_samples.py --out /data/samples
    python /app/scripts/fetch_samples.py --out /data/samples --only tidb-replication,nasa-friction
"""
from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import subprocess
import sys
import time
import urllib.error
import urllib.request

HERE = pathlib.Path(__file__).resolve().parent


def sha256_file(path: pathlib.Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def download(url: str, dest: pathlib.Path, attempts: int = 4) -> None:
    """Download with Python's HTTP client, resuming a partial file after a dropped connection."""
    tmp = dest.with_suffix(dest.suffix + ".part")
    print(f"  GET {url}", flush=True)
    for attempt in range(1, attempts + 1):
        have = tmp.stat().st_size if tmp.exists() else 0
        headers = {"User-Agent": "FrameSeek-sample-fetcher/0.2"}
        if have:
            headers["Range"] = f"bytes={have}-"
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=60) as resp:
                if have and resp.status != 206:  # server ignored Range: start over
                    have = 0
                total = have + int(resp.headers.get("Content-Length") or 0)
                done, shown = have, -1
                with tmp.open("ab" if have else "wb") as out:
                    while True:
                        chunk = resp.read(1 << 20)
                        if not chunk:
                            break
                        out.write(chunk)
                        done += len(chunk)
                        pct = int(100 * done / total) if total else -1
                        if pct // 10 != shown // 10:
                            shown = pct
                            print(f"  {done / 1e6:7.1f} / {total / 1e6:7.1f} MB", flush=True)
            tmp.replace(dest)
            return
        except (OSError, urllib.error.URLError) as e:
            if attempt == attempts:
                raise
            wait = 5 * attempt
            print(f"  download interrupted ({e}); retrying in {wait} s", flush=True)
            time.sleep(wait)


def trim(src: str, dest: pathlib.Path, start: float, end: float | None, reencode: bool) -> None:
    cmd = ["ffmpeg", "-v", "error", "-y", "-ss", str(start)]
    if end is not None:
        cmd += ["-to", str(end)]
    cmd += ["-i", src]
    if reencode:
        cmd += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "24", "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart"]
    else:
        cmd += ["-c", "copy"]
    tmp = dest.with_name(dest.stem + ".tmp" + dest.suffix)
    cmd += [str(tmp)]
    subprocess.run(cmd, check=True)
    tmp.replace(dest)


def probe_ok(path: pathlib.Path) -> bool:
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                       capture_output=True, text=True)
    try:
        return r.returncode == 0 and float(r.stdout.strip()) > 0
    except ValueError:
        return False


def entry(s: dict, dest: pathlib.Path) -> dict:
    return {
        "file": dest.name,
        "title": s["title"],
        "content_type": s["content_type"],
        "source_url": s["url"],
        "source_page": s["source_page"],
        "excerpt": [s["start_s"], s["end_s"]],
        "license": s["license"],
        "attribution": s["attribution"],
        "sha256": sha256_file(dest),
        "bytes": dest.stat().st_size,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="/data/samples")
    ap.add_argument("--only", default="", help="comma-separated slugs")
    ap.add_argument("--adopt-existing", action="store_true",
                    help="record files you placed in --out yourself (e.g. downloaded in a browser) instead of "
                         "downloading them, after checking that FFmpeg can read them")
    args = ap.parse_args()

    spec = json.loads((HERE / "samples.json").read_text())
    only = {s for s in args.only.split(",") if s}
    out = pathlib.Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    manifest_path = out / "manifest.json"
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}

    failed = []
    for s in spec["samples"]:
        if only and s["slug"] not in only:
            continue
        # Re-encoded excerpts are always H.264/AAC MP4; stream-copied ones keep the source container.
        ext = ".webm" if s["url"].endswith(".webm") and not s["reencode"] else ".mp4"
        dest = out / f"{s['slug']}{ext}"
        if dest.exists() and manifest.get(s["slug"], {}).get("sha256") == sha256_file(dest):
            print(f"= {s['slug']} already present")
            continue
        if dest.exists() and args.adopt_existing:
            if probe_ok(dest):
                print(f"~ {s['slug']} adopted from existing file")
                manifest[s["slug"]] = entry(s, dest)
                manifest_path.write_text(json.dumps(manifest, indent=2))
                continue
            print(f"! {s['slug']}: existing file is not readable media; downloading instead", file=sys.stderr)
        print(f"+ {s['slug']}")
        try:
            whole = s["start_s"] == 0 and s["end_s"] is None
            if whole:
                download(s["url"], dest)
            else:
                # Download the full recording with Python (robust to flaky networks and proxies,
                # resumable), cut the excerpt locally with FFmpeg, then delete the full file.
                cache = out / ".cache" / s["url"].rsplit("/", 1)[-1]
                cache.parent.mkdir(exist_ok=True)
                if not cache.exists():
                    download(s["url"], cache)
                trim(str(cache), dest, s["start_s"], s["end_s"], s["reencode"])
                cache.unlink()
        except Exception as e:  # keep going: one unreachable source should not block the rest
            print(f"! {s['slug']} failed: {e}", file=sys.stderr)
            failed.append(s["slug"])
            continue
        manifest[s["slug"]] = entry(s, dest)
        manifest_path.write_text(json.dumps(manifest, indent=2))
    print(f"manifest: {manifest_path}")
    if failed:
        print(f"failed: {', '.join(failed)} - run the same command again to retry just those", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
