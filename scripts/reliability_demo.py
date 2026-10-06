#!/usr/bin/env python3
"""Live reliability demonstrations against a running `docker compose` stack (stdlib only).

    python scripts/reliability_demo.py duplicate      # duplicate submission -> same job
    python scripts/reliability_demo.py kill-worker    # worker dies mid-stage -> lease expires -> resumes from manifests
    python scripts/reliability_demo.py failed-reindex # reindex fails -> previous version stays searchable

The stale-completion case (an old attempt finishing after a newer one) is timing-sensitive, so
it is demonstrated deterministically in ml/tests/test_worker_reliability.py instead.
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
import urllib.request

API = "http://localhost:8080"


def call(method: str, path: str, body: dict | None = None) -> dict:
    req = urllib.request.Request(API + path, method=method, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read() or b"{}")


def compose(*args: str, check: bool = True) -> None:
    print("  $ docker compose", " ".join(args))
    subprocess.run(["docker", "compose", *args], check=check)


def pick_video() -> dict:
    vids = [v for v in call("GET", "/api/videos") if v["active_index_version"]]
    if not vids:
        sys.exit("no ready videos; seed the demo library first (see README)")
    return min(vids, key=lambda v: v["duration_ms"] or 1e12)  # shortest video = fastest demo


def wait_job(job_id: str, until, timeout: float = 900) -> dict:
    t0 = time.time()
    last = None
    while time.time() - t0 < timeout:
        j = call("GET", f"/api/jobs/{job_id}")
        line = f"    job {job_id[:8]} status={j['status']} stage={j['stage']} attempt={j['attempt']} progress={j['stage_progress']}"
        if line != last:
            print(line)
            last = line
        if until(j):
            return j
        time.sleep(2)
    sys.exit("timed out")


def duplicate() -> None:
    v = pick_video()
    print(f"Video: {v['title']} (active index v{v['active_index_version']})")
    a = call("POST", f"/api/videos/{v['id']}/process", {})
    b = call("POST", f"/api/videos/{v['id']}/process", {})
    print(f"  first  -> job {a['job']['id']} deduplicated={a['deduplicated']}")
    print(f"  second -> job {b['job']['id']} deduplicated={b['deduplicated']}")
    print("Same content hash + same index config = same logical job; nothing is reprocessed.")


def kill_worker() -> None:
    v = pick_video()
    print(f"Video: {v['title']} - forcing a reindex (new version v{v['active_index_version'] + 1})")
    job = call("POST", f"/api/videos/{v['id']}/process", {"force": True})["job"]
    wait_job(job["id"], lambda j: j["stage"] in ("transcribing", "embedding"))
    print("Killing the worker mid-stage (no graceful shutdown):")
    compose("kill", "worker")
    j = call("GET", f"/api/jobs/{job['id']}")
    print(f"    job still says running with lease until {j['lease_expires_at']} (owner {j['lease_owner']})")
    print(f"    searches keep using v{v['active_index_version']} meanwhile")
    compose("start", "worker")
    print("The restarted worker can only claim the job after the lease expires (attempt generation +1):")
    done = wait_job(job["id"], lambda j: j["status"] in ("succeeded", "failed"))
    print(f"Result: {done['status']} after {done['attempt']} attempts. In the worker log, completed stages were skipped:")
    compose("logs", "--tail", "40", "worker", check=False)


def failed_reindex() -> None:
    v = pick_video()
    print(f"Video: {v['title']} (active v{v['active_index_version']})")
    print("Replacing the worker with one that fails in the embed stage (FRAMESEEK_FAIL_STAGE=embed):")
    compose("stop", "worker")
    proc = subprocess.Popen(["docker", "compose", "run", "--rm", "--name", "frameseek-faulty-worker",
                             "-e", "FRAMESEEK_FAIL_STAGE=embed", "worker"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        job = call("POST", f"/api/videos/{v['id']}/process", {"force": True})["job"]
        done = wait_job(job["id"], lambda j: j["status"] == "failed")
        print(f"    job failed after {done['attempt']} attempts: {done['error']}")
        after = call("GET", f"/api/videos/{v['id']}")
        print(f"    video status={after['status']} active_index_version=v{after['active_index_version']} (unchanged)")
        r = call("POST", "/api/search", {"query": "demo", "video_ids": [v["id"]], "k": 3})
        print(f"    search still returns {len(r['results'])} results from v{r['results'][0]['index_version'] if r['results'] else '-'}")
    finally:
        subprocess.run(["docker", "rm", "-f", "frameseek-faulty-worker"], capture_output=True)
        proc.wait(timeout=30)
        compose("start", "worker")


if __name__ == "__main__":
    {"duplicate": duplicate, "kill-worker": kill_worker, "failed-reindex": failed_reindex}[sys.argv[1] if len(sys.argv) > 1 else "duplicate"]()
