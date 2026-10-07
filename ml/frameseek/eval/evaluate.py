"""Evaluate retrieval arms A-F on labeled queries and write a report.

    python -m frameseek.eval.evaluate --split dev          # development (iterate here)
    python -m frameseek.eval.evaluate --split test         # held-out (run rarely; do not tune on it)
    python -m frameseek.eval.evaluate --split dev --tune   # also grid-search fusion weights on dev
    python -m frameseek.eval.evaluate --cv                 # leave-one-group-out E vs F on train+dev

Every arm sees the same extracted index; arms A-D use one or more channels' candidates, E and F
share the identical multimodal candidate pool, so E vs F isolates the learned combination.
Reports go to /data/eval/reports/<timestamp>.json, latest.json and latest.md (shown in the UI).
"""
from __future__ import annotations

import argparse
import itertools
import json
import statistics
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone

import numpy as np

from ..config import index_config, retrieval_config
from ..db import connection
from ..retrieval import search as S
from ..retrieval.ranker import active_model
from ..storage import storage
from . import train as T
from .labels import LabeledQuery, load_queries, load_splits, provenance, provenance_line, spot_check_pending
from .metrics import Interval, best_iou, first_hit_rank, grouped_bootstrap, localization, percentile

KS = [1, 5, 10]
TAUS = [0.3, 0.5]


def _ivs(ranked: list[dict]) -> list[Interval]:
    return [Interval(r["video_id"], r["start_ms"], r["end_ms"]) for r in ranked]


def evaluate_arm(items: list[T.Prepped], arm: str, model=None, weights=None) -> dict:
    per_query = []
    for it in items:
        q = it.q
        ranked = S.rank(it.prep, arm, max(KS), model=model, weights=weights)
        ivs = _ivs(ranked)
        rec: dict = {"id": q.id, "query": q.query, "type": q.query_type, "groups": q.groups,
                     "top": [{"video_id": r["video_id"], "video_title": S_title(it.prep, r["video_id"]),
                              "start_ms": r["start_ms"], "end_ms": r["end_ms"], "score": r["score"]} for r in ranked[:3]]}
        if q.answers:
            for tau in TAUS:
                fh = first_hit_rank(ivs, q.answers, tau)
                rec[f"first_hit@{tau}"] = fh
            # candidate recall: the arm's pool before ranking contains an acceptable interval
            pool, _ = S.score(it.prep, arm, weights, model)
            rec["candidate_recall@0.3"] = any(best_iou(Interval(c["video_id"], c["start_ms"], c["end_ms"]), q.answers) >= 0.3
                                              for c in pool)
            rec["candidate_overlap"] = any(best_iou(Interval(c["video_id"], c["start_ms"], c["end_ms"]), q.answers) > 0
                                           for c in pool)
            rec["loc_top1"] = localization(ivs[0], q.answers) if ivs else None
            rec["content_type"] = it.prep.snapshot.get(q.answers[0].video_id, {}).get("content_type", "?")
        else:
            rec["top_score"] = ranked[0]["score"] if ranked else None
            rec["content_type"] = "none"
        per_query.append(rec)
    return {"per_query": per_query}


def S_title(prep: S.Prepared, vid: str) -> str:
    return prep.snapshot.get(vid, {}).get("title", vid)


def summarize(per_query: list[dict], threshold: float | None = None) -> dict:
    ans = [r for r in per_query if r["type"] != "no_answer"]
    out: dict = {"n_answerable": len(ans), "n_no_answer": len(per_query) - len(ans)}
    if ans:
        for tau in TAUS:
            for k in KS:
                hits = [r[f"first_hit@{tau}"] is not None and r[f"first_hit@{tau}"] <= k for r in ans]
                out[f"success@{k}_iou{tau}"] = round(float(np.mean(hits)), 4)
                out[f"hits@{k}_iou{tau}"] = int(sum(hits))
            rr = [1.0 / r[f"first_hit@{tau}"] if r[f"first_hit@{tau}"] else 0.0 for r in ans]
            out[f"mrr_iou{tau}"] = round(float(np.mean(rr)), 4)
        out["candidate_recall_iou0.3"] = round(float(np.mean([r["candidate_recall@0.3"] for r in ans])), 4)
        locs = [r["loc_top1"] for r in ans if r["loc_top1"]]
        out["localization_top1_when_overlapping"] = {
            "n": len(locs),
            "mean_iou": round(statistics.mean(l["iou"] for l in locs), 3) if locs else None,
            "mean_start_err_s": round(statistics.mean(l["start_err_s"] for l in locs), 2) if locs else None,
            "mean_end_err_s": round(statistics.mean(l["end_err_s"] for l in locs), 2) if locs else None,
            "mean_excess_s": round(statistics.mean(l["excess_s"] for l in locs), 2) if locs else None,
        }
        out["end_to_end_top1_mean_iou"] = round(statistics.mean((r["loc_top1"] or {}).get("iou", 0.0) for r in ans), 3)
        ci = grouped_bootstrap([(r["groups"][0] if r["groups"] else "?",
                                 float(r["first_hit@0.3"] is not None and r["first_hit@0.3"] <= 5)) for r in ans])
        out["success@5_iou0.3_ci95_grouped"] = [round(ci[0], 3), round(ci[1], 3)] if ci else None
    na = [r for r in per_query if r["type"] == "no_answer"]
    if na:
        if threshold is None:
            out["no_answer_false_positive_rate"] = 1.0
            out["no_answer_note"] = "this arm has no abstention threshold, so it always returns results"
        else:
            out["no_answer_false_positive_rate"] = round(float(np.mean(
                [(r["top_score"] is not None and r["top_score"] >= threshold) for r in na])), 4)
    return out


def breakdown(per_query: list[dict], key: str, threshold=None) -> dict:
    groups = defaultdict(list)
    for r in per_query:
        groups[r[key]].append(r)
    return {g: summarize(rs, threshold) for g, rs in sorted(groups.items())}


def failures(per_query: list[dict], items: list[T.Prepped], k: int = 5, tau: float = 0.3) -> list[dict]:
    """Heuristic failure categories for answerable queries missed at success@k (IoU tau)."""
    by_id = {it.q.id: it for it in items}
    out = []
    for r in per_query:
        if r["type"] == "no_answer" or (r[f"first_hit@{tau}"] is not None and r[f"first_hit@{tau}"] <= k):
            continue
        it = by_id[r["id"]]
        # evidence available inside the labeled answer region?
        a = it.q.answers[0]
        with connection() as conn:
            seg = conn.execute(
                "SELECT bool_or(transcript <> '') AS speech, bool_or(ocr_text <> '') AS ocr FROM segments s "
                "JOIN videos v ON v.id = s.video_id AND v.active_index_version = s.index_version "
                "WHERE s.video_id = %s AND s.start_ms < %s AND s.end_ms > %s", (a.video_id, a.end_ms, a.start_ms)).fetchone()
        if r["candidate_recall@0.3"]:
            cat = "ranking_error"            # an acceptable window was in the pool but ranked below k
        elif r["candidate_overlap"]:
            cat = "boundary_error"           # retrieved the right place, but windows do not reach IoU tau
        elif r["type"] == "speech" and not (seg and seg["speech"]):
            cat = "asr_missing"
        elif r["type"] == "ocr" and not (seg and seg["ocr"]):
            cat = "ocr_missing"
        elif r["type"] == "visual":
            cat = "missed_visual_event"
        else:
            cat = "candidate_omission"       # no channel retrieved the answer region
        out.append({"id": r["id"], "query": r["query"], "type": r["type"], "category": cat, "top": r["top"][:1]})
    return out


def latency(items: list[T.Prepped], owner: str, arms: list[str], model) -> dict:
    """End-to-end search latency (query embedding + candidates + features + ranking), cold query cache."""
    out = {}
    for arm in arms:
        if arm == "F" and model is None:
            continue
        totals, embed, cand = [], [], []
        for it in items:
            S._encode_query.cache_clear()
            t0 = time.perf_counter()
            prep = S.prepare(S.SearchRequest(query=it.q.query, owner_id=owner, record=False), S.ARMS[arm][0])
            ranked = S.rank(prep, arm, 10, model=model)
            S.assemble(prep, ranked, arm, model)
            totals.append((time.perf_counter() - t0) * 1000)
            embed.append(prep.timings["embed_ms"])
            cand.append(prep.timings.get("candidates_ms", 0) + prep.timings.get("features_ms", 0))
        out[arm] = {"n": len(totals), "p50_ms": round(percentile(totals, 50), 1), "p95_ms": round(percentile(totals, 95), 1),
                    "embed_p50_ms": round(percentile(embed, 50), 1), "retrieval_p50_ms": round(percentile(cand, 50), 1)}
    return out


def tune_weights(items: list[T.Prepped]) -> dict:
    grid = [0.5, 1.0, 2.0]
    best, best_w = (-1.0, -1.0), None
    for combo in itertools.product(grid, repeat=5):
        w = dict(zip(S.CHANNELS, combo))
        res = summarize(evaluate_arm(items, "E", weights=w)["per_query"])
        key = (res.get("success@5_iou0.3", 0), res.get("mrr_iou0.3", 0))
        if key > best:
            best, best_w = key, w
    return {"weights": best_w, "dev_success@5_iou0.3": best[0], "dev_mrr_iou0.3": best[1]}


def corpus_stats() -> dict:
    with connection() as conn:
        r = conn.execute("""SELECT count(*) AS videos, coalesce(sum(duration_ms), 0) / 1000.0 AS seconds,
                   (SELECT count(*) FROM segments s JOIN videos v ON v.id = s.video_id
                      AND v.active_index_version = s.index_version) AS segments
                   FROM videos WHERE active_index_version IS NOT NULL""").fetchone()
        stage = conn.execute("""SELECT m.stage, sum((m.metrics->>'wall_seconds')::float) AS seconds
                   FROM stage_manifests m JOIN videos v ON v.id = m.video_id AND v.active_index_version = m.index_version
                   GROUP BY m.stage""").fetchall()
    hours = float(r["seconds"]) / 3600 or 1e-9
    return {"videos": r["videos"], "hours": round(float(r["seconds"]) / 3600, 3), "segments": r["segments"],
            "indexing_wall_seconds_per_video_hour": {s["stage"]: round(s["seconds"] / hours, 1) for s in stage}}


def write_report(report: dict) -> str:
    st = storage()
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    data = json.dumps(report, indent=2, default=str).encode()
    st.write_bytes(f"eval/reports/{ts}.json", data)
    st.write_bytes("eval/reports/latest.json", data)
    st.write_bytes("eval/reports/latest.md", markdown(report).encode())
    return f"eval/reports/{ts}.json"


def markdown(rep: dict) -> str:
    lines = [f"# FrameSeek evaluation ({rep['split']} split)", "",
             f"Created {rep['created']}. {rep['n_queries']} labeled queries. Corpus: {rep['corpus']['videos']} videos, "
             f"{rep['corpus']['hours']} h. Index config {rep['index_config_hash'][:12]}. Model: {rep.get('model_id') or 'none'}.",
             "", provenance_line(rep.get("label_provenance")), "", "| Arm | System | success@1 | success@5 | success@10 | MRR | cand. recall | n |",
             "|---|---|---|---|---|---|---|---|"]
    for arm, a in rep["arms"].items():
        o = a["overall"]
        lines.append(f"| {arm} | {a['label']} | {o.get('success@1_iou0.3', '-')} | {o.get('success@5_iou0.3', '-')} | "
                     f"{o.get('success@10_iou0.3', '-')} | {o.get('mrr_iou0.3', '-')} | {o.get('candidate_recall_iou0.3', '-')} | "
                     f"{o.get('n_answerable', 0)} |")
    lines += ["", "Success uses temporal IoU >= 0.3 with a labeled interval in the correct video.",
              "Small pilot sets give wide intervals; see the grouped bootstrap CI in the JSON report."]
    if rep.get("failures"):
        lines += ["", "## Failures (primary arm, success@5)", ""]
        for f in rep["failures"]:
            lines.append(f"- [{f['category']}] ({f['type']}) {f['query']}")
    return "\n".join(lines) + "\n"


def cross_validate(owner: str) -> dict:
    qs = [q for q in load_queries(owner) if q.split in ("train", "dev") and q.answers]
    items = T.prepare_all(qs, owner)
    groups = sorted({g for it in items for g in it.q.groups})
    rows = {"E": [], "F": []}
    for g in groups:
        held = [it for it in items if g in it.q.groups]
        rest = [it for it in items if g not in it.q.groups]
        try:
            model = T.as_ranker(T.fit(rest, 1.0))
        except ValueError:
            continue
        for arm, m in (("E", None), ("F", model)):
            for r in evaluate_arm(held, arm, m)["per_query"]:
                rows[arm].append(r)
    return {arm: summarize(rs) for arm, rs in rows.items()} | {"folds": len(groups), "label_provenance": provenance(qs),
                                                               "protocol": "leave-one-video-group-out, C=1.0"}


def main() -> int:
    ap = argparse.ArgumentParser(prog="frameseek.eval.evaluate")
    ap.add_argument("--split", default="dev", choices=["train", "dev", "test", "all"])
    ap.add_argument("--owner", default="demo")
    ap.add_argument("--tune", action="store_true", help="grid-search fusion weights (dev split only)")
    ap.add_argument("--cv", action="store_true", help="leave-one-group-out E vs F on train+dev")
    ap.add_argument("--no-latency", action="store_true")
    a = ap.parse_args()
    pending = spot_check_pending(a.owner)
    if pending:
        print(f"refusing to evaluate: {pending} spot-check labels are not reviewed yet (Label tab, 'Start review')")
        return 2
    if a.cv:
        print(json.dumps(cross_validate(a.owner), indent=2))
        return 0
    if a.split == "test" and a.tune:
        print("refusing to tune on the test split")
        return 2
    qs = load_queries(a.owner, include_unreviewed=True)
    if a.split != "all":
        if load_splits() is None:
            print("splits are not frozen: run `python -m frameseek.eval.labels freeze-splits` or use --split all")
            return 2
        qs = [q for q in qs if q.split == a.split]
    label_prov = provenance(qs)
    qs = [q for q in qs if q.usable]
    if not qs:
        print("no usable labeled queries for this split; add or review some in the UI (Label tab)")
        return 2
    print(f"evaluating {len(qs)} queries on split={a.split}")
    print(provenance_line(label_prov))
    items = T.prepare_all(qs, a.owner)
    model = active_model(ttl_s=0)
    threshold = model.no_answer_threshold if model else None
    arms = ["A", "B", "C", "D", "E"] + (["F"] if model else [])
    report = {
        "created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "split": a.split, "n_queries": len(qs),
        "query_types": {t: sum(q.query_type == t for q in qs) for t in sorted({q.query_type for q in qs})},
        "label_provenance": label_prov,
        "index_config_hash": index_config()[1], "retrieval_config": retrieval_config(),
        "model_id": model.id if model else None, "corpus": corpus_stats(), "arms": {},
        "protocol": {"success": "top-K contains a result in a correct video with temporal IoU >= tau",
                     "taus": TAUS, "ks": KS, "nms_iou": retrieval_config()["nms_iou"],
                     "uncertainty": "95% CI by bootstrap over video groups"},
    }
    for arm in arms:
        res = evaluate_arm(items, arm, model if arm == "F" else None)
        th = threshold if arm == "F" else None
        report["arms"][arm] = {"label": S.ARM_LABELS[arm], "overall": summarize(res["per_query"], th),
                               "by_query_type": breakdown(res["per_query"], "type", th),
                               "by_content_type": breakdown(res["per_query"], "content_type", th),
                               "per_query": res["per_query"]}
        print(f"{arm}: {report['arms'][arm]['overall'].get('success@5_iou0.3')} success@5 (IoU 0.3)")
    primary = "F" if model else "E"
    report["primary_arm"] = primary
    report["failures"] = failures(report["arms"][primary]["per_query"], items)
    if not a.no_latency:
        report["latency"] = latency(items, a.owner, ["A", "E", "F"], model)
    if a.tune:
        report["tuned_fusion"] = tune_weights(items)
        print("suggested fusion weights:", report["tuned_fusion"])
    path = write_report(report)
    print(f"report: /data/{path} (also latest.json / latest.md)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
