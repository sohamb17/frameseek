"""End-to-end evaluation of the conversational layer through the public API.

    docker compose exec worker python -m frameseek.eval.conversation_eval

Runs the scripted dialogues in eval/conversation_tasks.json as a user would (Go API ->
LangGraph -> retriever -> Go search -> Python retrieval) and checks behaviour that has an
objective answer without human relevance labels:

  * reference resolution: "the second result" expands exactly the item displayed second
  * filter adherence: every result satisfies the requested type / video restriction
  * topic retention across refinements, and filters cleared on a new topic
  * clarification asked when a reference is ambiguous, and not otherwise
  * no invented results for impossible references
  * duplicate turn submission replays the stored answer; stale turns are rejected;
    another owner cannot read or write the conversation

Baselines: (1) stateless search of the follow-up text alone, (2) an explicit-filter search
with the same query and filters (the conversation should return the same list).
Whether the retrieved moments are *relevant* is measured by the labeled retrieval evaluation,
not here.
"""
from __future__ import annotations

import json
import os
import pathlib
import statistics
import sys
import time
import uuid
from collections import defaultdict
from datetime import datetime, timezone

import httpx

from ..config import settings
from ..storage import storage

API = os.environ.get("FRAMESEEK_API_URL", "http://api:8080")
ML = os.environ.get("FRAMESEEK_ML_URL", "http://ml:8000")
TASKS = pathlib.Path(os.environ.get("FRAMESEEK_EVAL_DIR", "/app/eval")) / "conversation_tasks.json"


class Client:
    def __init__(self) -> None:
        self.h = httpx.Client(base_url=API, timeout=60)

    def new_conversation(self) -> str:
        return self.h.post("/api/conversations", json={}).json()["id"]

    def say(self, conv: str, text: str, resume: bool = False, turn_id: str | None = None) -> tuple[dict, float, str]:
        tid = turn_id or str(uuid.uuid4())
        t0 = time.perf_counter()
        r = self.h.post(f"/api/conversations/{conv}/messages", json={"client_turn_id": tid, "text": text, "resume": resume})
        ms = (time.perf_counter() - t0) * 1000
        r.raise_for_status()
        return r.json(), ms, tid

    def search(self, **body) -> dict:
        body = {k: v for k, v in body.items() if v}
        r = self.h.post("/api/search", json=body)
        r.raise_for_status()
        return r.json()


def _ids(results: list[dict]) -> list[tuple]:
    return [(r["video_id"], r["segment_id"]) for r in results]


def check(expect: dict, resp: dict, ctx: dict) -> dict[str, bool]:
    """Evaluate every expectation of one turn; returns {check_name: passed}."""
    out: dict[str, bool] = {}
    res = resp.get("results") or []
    interp = resp.get("interpretation") or {}
    filters = (resp.get("state") or {}).get("filters") or {}
    if "status" in expect:
        out[f"status_{expect['status']}"] = resp.get("status") == expect["status"]
    if "min_results" in expect:
        out["min_results"] = len(res) >= expect["min_results"]
    if "multi_video" in expect:
        out["precondition_multi_video"] = len({r["video_id"] for r in res}) >= 2
    if "content_types" in expect:
        out["filter_content_type"] = bool(res) and all(r["content_type"] in expect["content_types"] for r in res)
    if expect.get("no_type_filter"):
        out["type_filter_cleared"] = not filters.get("content_types")
    if expect.get("topic_kept"):
        out["topic_kept"] = bool(ctx.get("topic")) and interp.get("query") == ctx["topic"]
    if expect.get("new_topic"):
        out["new_topic_clears_video_filters"] = not filters.get("video_ids") and not filters.get("exclude_video_ids")
    if expect.get("no_results"):
        out["no_invented_results"] = len(res) == 0
    if "expand_of" in expect:
        n = expect["expand_of"]
        prev = ctx.get("prev_list") or []
        target = (prev[n - 1] if n > 0 else prev[-1]) if prev and (n == -1 or n <= len(prev)) else None
        ok = bool(target) and len(res) == 1 and _ids(res) == _ids([target])
        if ok:
            r = res[0]
            ok = r["start_ms"] <= target["start_ms"] and r["end_ms"] >= target["end_ms"] and r["start_ms"] >= 0 \
                and r["end_ms"] <= (target.get("video_duration_ms") or r["end_ms"])
            if "expand_seconds" in expect:
                pad = expect["expand_seconds"] * 1000
                ok = ok and (r["start_ms"] == max(0, target["start_ms"] - pad))
        out["reference_resolution"] = ok
    for key, name in (("within_video_of", "filter_within_video"), ("excludes_video_of", "filter_excludes_video")):
        if key in expect:
            vid = ctx.get(expect[key])
            if key == "within_video_of":
                out[name] = bool(vid) and bool(res) and all(r["video_id"] == vid for r in res)
            else:
                out[name] = bool(vid) and bool(res) and all(r["video_id"] != vid for r in res)
    if "clarify_options_min" in expect:
        out["clarification_options"] = len(((resp.get("clarification") or {}).get("options")) or []) >= expect["clarify_options_min"]
    return out


def run_task(c: Client, task: dict) -> dict:
    conv = c.new_conversation()
    ctx: dict = {"topic": None, "prev_list": None, "selected": None}
    turns = []
    pending_options: list[dict] = []
    for spec in task["turns"]:
        if "answer_option" in spec:
            opt = pending_options[spec["answer_option"]]
            text, resume = opt["value"], True
            ctx["chosen_option"] = opt["value"]
        else:
            text, resume = spec["say"], False
        resp, ms, tid = c.say(conv, text, resume=resume)
        checks = check(spec["expect"], resp, ctx)
        action = (resp.get("interpretation") or {}).get("action")
        turns.append({"text": text, "resume": resume, "status": resp.get("status"), "action": action,
                      "latency_ms": round(ms, 1), "checks": checks, "n_results": len(resp.get("results") or []),
                      "message": resp.get("message"), "turn_id": tid, "expect": spec["expect"],
                      "interp_query": (resp.get("interpretation") or {}).get("query"),
                      "filters": (resp.get("state") or {}).get("filters"), "results": resp.get("results") or []})
        # update the dialogue context the way a user perceives it
        pending_options = (resp.get("clarification") or {}).get("options") or []
        if action == "retrieve":
            ctx["prev_list"] = resp.get("results") or []
            ctx["topic"] = (resp.get("interpretation") or {}).get("query")
        if action == "expand" and resp.get("results"):
            ctx["selected"] = resp["results"][0]["video_id"]
        if action == "reset":
            ctx.update(topic=None, prev_list=[], selected=None)
    return {"id": task["id"], "conversation_id": conv, "turns": turns}


def baselines(c: Client, runs: list[dict]) -> dict:
    """(1) stateless search of the follow-up text; (2) explicit-filter search equivalence."""
    stateless = defaultdict(list)
    explicit_same = []
    for run in runs:
        topic = None
        for t in run["turns"]:
            e = t["expect"]
            if t["action"] == "retrieve" and t["results"]:
                f = t["filters"] or {}
                ref = c.search(query=t["interp_query"], content_types=f.get("content_types"), video_ids=f.get("video_ids"),
                               exclude_video_ids=f.get("exclude_video_ids"), k=8)
                explicit_same.append(_ids(ref["results"]) == _ids(t["results"]))
            needs_state = any(k in e for k in ("topic_kept", "expand_of", "within_video_of", "excludes_video_of"))
            if needs_state and not t["resume"]:
                s = c.search(query=t["text"], k=8)
                res = s["results"]
                if e.get("topic_kept"):
                    stateless["topic_kept"].append(False)       # the follow-up alone carries no topic
                if "content_types" in e:
                    stateless["filter_content_type"].append(bool(res) and all(r["content_type"] in e["content_types"] for r in res))
                if "expand_of" in e:
                    stateless["reference_resolution"].append(False)  # nothing to refer to without state
            if t["action"] == "retrieve":
                topic = t["interp_query"]
        _ = topic
    return {"stateless_search": {k: round(sum(v) / len(v), 3) for k, v in stateless.items()},
            "stateless_counts": {k: len(v) for k, v in stateless.items()},
            "explicit_filter_equivalence": round(sum(explicit_same) / max(1, len(explicit_same)), 3),
            "explicit_filter_n": len(explicit_same)}


def safety_checks(c: Client, runs: list[dict]) -> dict:
    out = {}
    # duplicate submission: replaying the first turn's client_turn_id returns the stored answer
    first = runs[0]
    t0 = first["turns"][0]
    resp, _, _ = c.say(first["conversation_id"], t0["text"], turn_id=t0["turn_id"])
    out["duplicate_turn_replayed"] = bool(resp.get("duplicate")) and _ids(resp.get("results") or []) == _ids(t0["results"])
    conv = c.h.get(f"/api/conversations/{first['conversation_id']}").json()
    out["duplicate_turn_not_stored_twice"] = len(conv["turns"]) == len(first["turns"])
    # stale turn: an older sequence number arriving late must not overwrite newer state
    r = httpx.post(f"{ML}/conversation/turn", timeout=30, headers={"X-FrameSeek-Internal": settings().internal_token},
                   json={"thread_id": first["conversation_id"], "owner_id": "demo", "collection_id": conv.get("collection_id"),
                         "seq": 1, "text": "late duplicate from a slow client"}).json()
    out["stale_turn_rejected"] = r.get("status") == "stale"
    # isolation: another owner cannot read or post to this conversation
    hdr = {"X-FrameSeek-Internal": settings().internal_token, "X-FrameSeek-Owner": "intruder"}
    g = c.h.get(f"/api/conversations/{first['conversation_id']}", headers=hdr)
    p = c.h.post(f"/api/conversations/{first['conversation_id']}/messages", headers=hdr,
                 json={"client_turn_id": str(uuid.uuid4()), "text": "show more context around the first result"})
    out["other_owner_blocked"] = g.status_code == 404 and p.status_code == 404
    return out


def summarize(runs: list[dict]) -> dict:
    by_check: dict[str, list[bool]] = defaultdict(list)
    lat: dict[str, list[float]] = defaultdict(list)
    for run in runs:
        for t in run["turns"]:
            for k, v in t["checks"].items():
                by_check[k].append(v)
            lat[t["action"] or t["status"]].append(t["latency_ms"])
    turns_all = [t for r in runs for t in r["turns"]]
    tasks_passed = sum(all(all(t["checks"].values()) for t in r["turns"]) for r in runs)
    expected_clarify = [t for t in turns_all if t["expect"].get("status") == "clarify"]
    expected_ok = [t for t in turns_all if t["expect"].get("status") == "ok"]
    pct = lambda v, p: round(statistics.quantiles(v, n=100)[p - 1], 1) if len(v) >= 2 else (v[0] if v else None)  # noqa: E731
    return {
        "tasks": len(runs), "tasks_fully_passed": tasks_passed, "turns": len(turns_all),
        "checks": {k: {"passed": sum(v), "total": len(v), "rate": round(sum(v) / len(v), 3)} for k, v in sorted(by_check.items())},
        "clarification_recall": round(sum(t["status"] == "clarify" for t in expected_clarify) / max(1, len(expected_clarify)), 3),
        "unnecessary_clarification_rate": round(sum(t["status"] == "clarify" for t in expected_ok) / max(1, len(expected_ok)), 3),
        "latency_ms": {a: {"n": len(v), "p50": pct(v, 50), "p95": pct(v, 95)} for a, v in lat.items()},
        "llm_calls_per_turn": 0 if settings().llm_provider == "rules" else 1,
        "parser": "rules" if settings().llm_provider == "rules" else settings().llm_model,
    }


def markdown(rep: dict) -> str:
    s = rep["summary"]
    lines = [f"# Conversational search evaluation", "",
             f"{rep['created']} · parser: {s['parser']} · {s['tasks']} scripted tasks, {s['turns']} turns · "
             f"{s['tasks_fully_passed']}/{s['tasks']} tasks passed every check", "",
             "| Check | Passed | Rate |", "|---|---|---|"]
    for k, v in s["checks"].items():
        lines.append(f"| {k} | {v['passed']}/{v['total']} | {v['rate']:.0%} |")
    lines += ["", f"Clarification asked when expected: {s['clarification_recall']:.0%} · "
                  f"unnecessary clarification: {s['unnecessary_clarification_rate']:.0%}", "",
              "| Safety property | Result |", "|---|---|"]
    for k, v in rep["safety"].items():
        lines.append(f"| {k} | {'pass' if v else 'FAIL'} |")
    b = rep["baselines"]
    lines += ["", "**Baselines.** Stateless search of the follow-up text alone: " +
              ", ".join(f"{k} {v:.0%} (n={b['stateless_counts'][k]})" for k, v in b["stateless_search"].items()) +
              f". Conversation results identical to an explicit-filter search with the same query and filters: "
              f"{b['explicit_filter_equivalence']:.0%} (n={b['explicit_filter_n']}).", "",
              "| Turn type | n | p50 ms | p95 ms |", "|---|---|---|---|"]
    for a, v in s["latency_ms"].items():
        lines.append(f"| {a} | {v['n']} | {v['p50']} | {v['p95']} |")
    lines += ["", "These checks measure resolution and state handling, not whether the retrieved moments are relevant;",
              "relevance is measured by the labeled retrieval evaluation. Failures are listed in the JSON report."]
    return "\n".join(lines) + "\n"


def main() -> int:
    tasks = json.loads(TASKS.read_text())["tasks"]
    c = Client()
    runs = [run_task(c, t) for t in tasks]
    rep = {"created": datetime.now(timezone.utc).isoformat(timespec="seconds"), "summary": summarize(runs),
           "safety": safety_checks(c, runs), "baselines": baselines(c, runs),
           "failures": [{"task": r["id"], "turn": t["text"], "failed": [k for k, v in t["checks"].items() if not v],
                         "status": t["status"], "message": t["message"]}
                        for r in runs for t in r["turns"] if not all(t["checks"].values())],
           "runs": [{**r, "turns": [{k: v for k, v in t.items() if k != "results"} for t in r["turns"]]} for r in runs]}
    st = storage()
    st.write_bytes("eval/reports/conversation_latest.json", json.dumps(rep, indent=2, default=str).encode())
    md = markdown(rep)
    st.write_bytes("eval/reports/conversation_latest.md", md.encode())
    print(md)
    for f in rep["failures"]:
        print(f"FAILED {f['task']}: {f['turn']!r} -> {f['failed']} ({f['status']}: {f['message']})")
    return 0 if not rep["failures"] and all(rep["safety"].values()) else 1


if __name__ == "__main__":
    sys.exit(main())
