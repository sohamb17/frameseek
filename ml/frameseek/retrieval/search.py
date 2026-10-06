"""Multimodal candidate generation, scoring and result assembly.

Flow for one query:
  1. Snapshot the active index version of every video in scope (REPEATABLE READ, read-only),
     so a reindex publishing mid-search can never mix old segments with new vectors.
  2. Encode the query twice: MiniLM (for transcript/OCR vectors) and CLIP text (for frames).
     The two spaces are never compared with each other.
  3. Candidate generation: up to K segments from each channel
     (transcript lexical, transcript semantic, OCR lexical, OCR semantic, visual).
  4. Union the candidates and compute EVERY channel's feature for each of them
     (a segment missed by one channel still gets that channel's score).
  5. Score with the selected arm (single channel, fixed fusion, or learned scorer).
  6. Temporal non-max suppression over overlapping windows, then build evidence.
"""
from __future__ import annotations

import functools
import json
import re
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from ..config import index_config, retrieval_config
from ..db import connection
from . import ranker as ranker_mod
from .features import CHANNELS, SCORE_FIELD, build_matrix

ARMS = {
    # arm: (channels used for candidate generation, scorer)
    "A": (["transcript_lex"], "t_lex"),
    "B": (["transcript_sem"], "t_sem"),
    "C": (["transcript_lex", "transcript_sem", "ocr_lex", "ocr_sem"], "rrf"),
    "D": (["visual"], "v_max"),
    "E": (CHANNELS, "rrf"),
    "F": (CHANNELS, "learned"),
}
ARM_LABELS = {
    "A": "Transcript keyword search",
    "B": "Transcript semantic search",
    "C": "Transcript + on-screen text (fixed fusion)",
    "D": "Visual only (CLIP)",
    "E": "All modalities, fixed rank fusion",
    "F": "All modalities, learned scorer",
}


@dataclass
class SearchRequest:
    query: str
    owner_id: str
    collection_id: str | None = None
    video_ids: list[str] | None = None
    exclude_video_ids: list[str] | None = None
    content_types: list[str] | None = None
    k: int = 10
    ranker: str = "auto"          # auto | learned | fusion | A..F
    record: bool = True
    nms: bool = True
    debug: bool = False


@dataclass
class Prepared:
    """Everything computed for one query before scoring (reused by evaluation across arms)."""
    query: str
    snapshot: dict[str, dict]
    channel_hits: dict[str, list[tuple[int, float]]]
    cands: list[dict]
    timings: dict[str, float] = field(default_factory=dict)
    tsquery: str = ""


# --------------------------------------------------------------------------- encoders
@functools.lru_cache(maxsize=256)
def _encode_query(q: str) -> tuple[np.ndarray, np.ndarray]:
    from ..models import clip, text
    cfg, _ = index_config()
    return text.encode([q], cfg["text_encoder"])[0], clip.encode_text([q], cfg["visual_encoder"])[0]


def warmup() -> None:
    _encode_query("warm up the encoders")


# --------------------------------------------------------------------------- snapshot + channels
def _snapshot(conn, req: SearchRequest) -> dict[str, dict]:
    sql = ["SELECT id::text, active_index_version AS version, title, content_type, duration_ms, collection_id::text "
           "FROM videos WHERE owner_id = %(owner)s AND active_index_version IS NOT NULL"]
    params: dict[str, Any] = {"owner": req.owner_id}
    if req.collection_id:
        sql.append("AND collection_id = %(coll)s")
        params["coll"] = req.collection_id
    if req.video_ids:
        sql.append("AND id = ANY(%(vids)s::uuid[])")
        params["vids"] = req.video_ids
    if req.exclude_video_ids:
        sql.append("AND NOT (id = ANY(%(xvids)s::uuid[]))")
        params["xvids"] = req.exclude_video_ids
    if req.content_types:
        sql.append("AND content_type = ANY(%(cts)s)")
        params["cts"] = req.content_types
    return {r["id"]: r for r in conn.execute(" ".join(sql), params).fetchall()}


_ACTIVE = "JOIN unnest(%(vids)s::uuid[], %(vers)s::int[]) AS a(vid, ver) ON {t}.video_id = a.vid AND {t}.index_version = a.ver"


def _channel(conn, name: str, p: dict, k: int, frame_k: int) -> list[tuple[int, float]]:
    if name in ("transcript_lex", "ocr_lex"):
        if not p["tsq"]:
            return []
        col = "tsv_transcript" if name == "transcript_lex" else "tsv_ocr"
        sql = (f"SELECT s.id, ts_rank_cd(s.{col}, %(tsq)s::tsquery, 32) AS score FROM segments s "
               + _ACTIVE.format(t="s") + f" WHERE s.{col} @@ %(tsq)s::tsquery ORDER BY score DESC, s.id LIMIT %(k)s")
    elif name in ("transcript_sem", "ocr_sem"):
        kind = "transcript" if name == "transcript_sem" else "ocr"
        sql = ("SELECT s.id, 1 - (e.embedding <=> %(qv)s) AS score FROM segment_embeddings e "
               "JOIN segments s ON s.id = e.segment_id " + _ACTIVE.format(t="s")
               + f" WHERE e.kind = '{kind}' ORDER BY e.embedding <=> %(qv)s, s.id LIMIT %(k)s")
    elif name == "visual":
        sql = ("WITH top AS (SELECT f.video_id, f.index_version, f.ts_ms, f.id AS frame_id, "
               "1 - (fe.embedding <=> %(cv)s) AS sim FROM frame_embeddings fe JOIN frames f ON f.id = fe.frame_id "
               + _ACTIVE.format(t="f") + " ORDER BY fe.embedding <=> %(cv)s LIMIT %(fk)s) "
               "SELECT s.id, max(top.sim) AS score FROM top JOIN segments s ON s.video_id = top.video_id "
               "AND s.index_version = top.index_version AND top.ts_ms >= s.start_ms AND top.ts_ms < s.end_ms "
               "GROUP BY s.id ORDER BY score DESC, s.id LIMIT %(k)s")
    else:
        raise ValueError(name)
    rows = conn.execute(sql, {**p, "k": k, "fk": frame_k}).fetchall()
    return [(r["id"], float(r["score"])) for r in rows]


def _candidate_rows(conn, ids: list[int], p: dict) -> list[dict]:
    if not ids:
        return []
    tsq_expr = "%(tsq)s::tsquery" if p["tsq"] else "NULL::tsquery"
    sql = f"""
    SELECT s.id, s.video_id::text AS video_id, s.index_version, s.idx, s.start_ms, s.end_ms, s.transcript,
           s.ocr_text, s.speech_coverage, s.ocr_coverage, s.ocr_stability, s.n_frames, s.thumb_frame_id,
           coalesce(ts_rank_cd(s.tsv_transcript, {tsq_expr}, 32), 0) AS t_lex,
           coalesce(ts_rank_cd(s.tsv_ocr, {tsq_expr}, 32), 0) AS o_lex,
           (SELECT 1 - (e.embedding <=> %(qv)s) FROM segment_embeddings e
             WHERE e.segment_id = s.id AND e.kind = 'transcript') AS t_sem,
           (SELECT 1 - (e.embedding <=> %(qv)s) FROM segment_embeddings e
             WHERE e.segment_id = s.id AND e.kind = 'ocr') AS o_sem,
           vb.frame_id AS best_frame_id, vb.ts_ms AS best_frame_ts, vb.sim AS v_max, va.v_mean
    FROM segments s
    LEFT JOIN LATERAL (
        SELECT f.id AS frame_id, f.ts_ms, 1 - (fe.embedding <=> %(cv)s) AS sim
        FROM frames f JOIN frame_embeddings fe ON fe.frame_id = f.id
        WHERE f.video_id = s.video_id AND f.index_version = s.index_version
          AND f.ts_ms >= s.start_ms AND f.ts_ms < s.end_ms
        ORDER BY fe.embedding <=> %(cv)s LIMIT 1) vb ON true
    LEFT JOIN LATERAL (
        SELECT avg(1 - (fe.embedding <=> %(cv)s)) AS v_mean
        FROM frames f JOIN frame_embeddings fe ON fe.frame_id = f.id
        WHERE f.video_id = s.video_id AND f.index_version = s.index_version
          AND f.ts_ms >= s.start_ms AND f.ts_ms < s.end_ms) va ON true
    WHERE s.id = ANY(%(ids)s)
    ORDER BY s.id
    """
    return [dict(r) for r in conn.execute(sql, {**p, "ids": ids}).fetchall()]


def prepare(req: SearchRequest, channels: list[str] | None = None) -> Prepared:
    """Snapshot + candidate generation + feature rows, inside one consistent read-only snapshot."""
    rcfg = retrieval_config()
    timings: dict[str, float] = {}
    t0 = time.perf_counter()
    qv, cv = _encode_query(req.query)
    timings["embed_ms"] = (time.perf_counter() - t0) * 1000
    channels = channels or CHANNELS
    with connection() as conn:
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        snap = _snapshot(conn, req)
        t1 = time.perf_counter()
        tsq = conn.execute("SELECT replace(plainto_tsquery('english', %s)::text, '&', '|') AS t",
                           (req.query,)).fetchone()["t"]
        p = {"vids": list(snap), "vers": [v["version"] for v in snap.values()], "qv": qv, "cv": cv, "tsq": tsq}
        hits: dict[str, list[tuple[int, float]]] = {}
        if snap:
            for ch in channels:
                hits[ch] = _channel(conn, ch, p, rcfg["per_channel_k"], rcfg["visual_frame_k"])
        timings["candidates_ms"] = (time.perf_counter() - t1) * 1000
        t2 = time.perf_counter()
        ids = sorted({sid for lst in hits.values() for sid, _ in lst})
        cands = _candidate_rows(conn, ids, p)
        timings["features_ms"] = (time.perf_counter() - t2) * 1000
        conn.rollback()
    return Prepared(query=req.query, snapshot=snap, channel_hits=hits, cands=cands, timings=timings, tsquery=tsq)


# --------------------------------------------------------------------------- scoring
def channel_ranks(prep: Prepared) -> dict[str, dict[int, int]]:
    return {ch: {sid: i + 1 for i, (sid, _) in enumerate(lst)} for ch, lst in prep.channel_hits.items()}


def score(prep: Prepared, arm: str, weights: dict[str, float] | None = None,
          model: "ranker_mod.LoadedRanker | None" = None) -> tuple[list[dict], np.ndarray]:
    """Return (candidates restricted to the arm's pool, scores)."""
    chans, scorer = ARMS[arm]
    ranks = channel_ranks(prep)
    pool_ids = {sid for ch in chans for sid in ranks.get(ch, {})}
    cands = [c for c in prep.cands if c["id"] in pool_ids]
    if not cands:
        return [], np.zeros(0)
    if scorer == "rrf":
        rcfg = retrieval_config()
        w = weights or rcfg["fusion_weights"]
        k = rcfg["rrf_k"]
        s = np.array([sum(w.get(ch, 1.0) / (k + ranks[ch][c["id"]]) for ch in chans if c["id"] in ranks.get(ch, {}))
                      for c in cands])
    elif scorer == "learned":
        if model is None:
            raise ValueError("arm F needs a trained model")
        X = build_matrix(cands, {ch: ranks.get(ch, {}) for ch in CHANNELS}, prep.query)
        s = model.score(X)
    else:
        s = np.array([float(c.get(scorer) or 0.0) for c in cands])
    return cands, s


def temporal_nms(order: list[dict], iou_thr: float) -> list[dict]:
    kept: list[dict] = []
    for c in order:
        clash = False
        for k in kept:
            if k["video_id"] != c["video_id"]:
                continue
            inter = max(0, min(k["end_ms"], c["end_ms"]) - max(k["start_ms"], c["start_ms"]))
            union = max(k["end_ms"], c["end_ms"]) - min(k["start_ms"], c["start_ms"])
            if union > 0 and inter / union >= iou_thr:
                clash = True
                break
        if not clash:
            kept.append(c)
    return kept


def rank(prep: Prepared, arm: str, k: int, nms: bool = True, weights=None, model=None) -> list[dict]:
    cands, s = score(prep, arm, weights, model)
    order = [dict(c, score=float(x)) for c, x in sorted(zip(cands, s), key=lambda t: (-t[1], t[0]["id"]))]
    if nms:
        order = temporal_nms(order, retrieval_config()["nms_iou"])
    return order[:k]


# --------------------------------------------------------------------------- evidence
def _percentile_bars(prep: Prepared) -> dict[int, dict[str, float]]:
    """Per-modality strength in [0,1]: the candidate's percentile within this query's pool."""
    out: dict[int, dict[str, float]] = {c["id"]: {} for c in prep.cands}
    n = len(prep.cands)
    for label, fields in {"speech": ["t_sem", "t_lex"], "screen_text": ["o_sem", "o_lex"], "visual": ["v_max"]}.items():
        vals = np.array([max(float(c.get(f) or 0) for f in fields) for c in prep.cands])
        order = vals.argsort().argsort()  # rank of each value
        for c, r, v in zip(prep.cands, order, vals):
            out[c["id"]][label] = 0.0 if v <= 0 or n <= 1 else round(float(r) / (n - 1), 3)
    return out


def _headlines(ids: list[int], tsq: str) -> dict[int, dict[str, str]]:
    if not ids:
        return {}
    opts = "StartSel=[[, StopSel=]], MaxWords=45, MinWords=18, MaxFragments=2, FragmentDelimiter=\" … \""
    q = "%(tsq)s::tsquery" if tsq else "NULL::tsquery"
    with connection() as conn:
        rows = conn.execute(
            f"SELECT id, CASE WHEN transcript = '' THEN '' ELSE ts_headline('english', transcript, {q}, %(o)s) END AS t, "
            f"CASE WHEN ocr_text = '' THEN '' ELSE ts_headline('english', replace(ocr_text, E'\\n', ' · '), {q}, %(o)s) END AS o "
            "FROM segments WHERE id = ANY(%(ids)s)", {"ids": ids, "tsq": tsq, "o": opts}).fetchall()
    return {r["id"]: {"transcript": r["t"] or "", "ocr": r["o"] or ""} for r in rows}


def _clip_excerpt(text: str, n: int = 260) -> str:
    text = " ".join(text.split())
    return text if len(text) <= n else text[:n].rsplit(" ", 1)[0] + " …"


def assemble(prep: Prepared, ranked: list[dict], arm: str, model=None) -> list[dict]:
    ranks = channel_ranks(prep)
    bars = _percentile_bars(prep)
    heads = _headlines([c["id"] for c in ranked], prep.tsquery)
    explain_rows = None
    if arm == "F" and model is not None and ranked:
        X = build_matrix(ranked, ranks, prep.query)
        explain_rows = model.explain(X, top=3)
    out = []
    for i, c in enumerate(ranked):
        vid = prep.snapshot[c["video_id"]]
        h = heads.get(c["id"], {})
        retrieved_by = [ch for ch in CHANNELS if c["id"] in ranks.get(ch, {})]
        evidence_types = []
        if any(ch.startswith("transcript") for ch in retrieved_by):
            evidence_types.append("speech")
        if any(ch.startswith("ocr") for ch in retrieved_by):
            evidence_types.append("screen_text")
        if "visual" in retrieved_by:
            evidence_types.append("visual")
        out.append({
            "rank": i + 1,
            "video_id": c["video_id"],
            "video_title": vid["title"],
            "content_type": vid["content_type"],
            "video_duration_ms": vid["duration_ms"],
            "index_version": c["index_version"],
            "segment_id": c["id"],
            "start_ms": c["start_ms"],
            "end_ms": c["end_ms"],
            "score": round(c["score"], 5),
            "evidence_types": evidence_types,
            "retrieved_by": retrieved_by,
            "modality_strength": bars.get(c["id"], {}),
            "evidence": {
                "transcript": h.get("transcript") or _clip_excerpt(c["transcript"]),
                "transcript_missing": not c["transcript"].strip(),
                "ocr": h.get("ocr") or _clip_excerpt(c["ocr_text"].replace("\n", " · ")),
                "ocr_missing": not c["ocr_text"].strip(),
                "visual": None if c.get("best_frame_id") is None else {
                    "frame_id": c["best_frame_id"], "ts_ms": c["best_frame_ts"],
                    "similarity": round(float(c["v_max"] or 0), 4)},
            },
            "thumb_frame_id": c.get("best_frame_id") or c.get("thumb_frame_id"),
            "channel_scores": {ch: {"score": round(float(c.get(SCORE_FIELD[ch]) or 0), 4),
                                    "rank": ranks.get(ch, {}).get(c["id"])} for ch in CHANNELS},
            "explain": explain_rows[i] if explain_rows else None,
        })
    return out


# --------------------------------------------------------------------------- entry point
def resolve_arm(requested: str) -> tuple[str, "ranker_mod.LoadedRanker | None"]:
    model = ranker_mod.active_model()
    r = requested.upper() if len(requested) == 1 else requested
    if r in ARMS:
        if r == "F" and model is None:
            return "E", None
        return r, model if r == "F" else None
    if requested == "fusion":
        return "E", None
    # auto / learned: use the trained scorer only if one is registered and active
    return ("F", model) if model is not None else ("E", None)


def search(req: SearchRequest) -> dict:
    t0 = time.perf_counter()
    arm, model = resolve_arm(req.ranker)
    prep = prepare(req)
    t1 = time.perf_counter()
    ranked = rank(prep, arm, max(1, min(req.k, 50)), req.nms, model=model)
    results = assemble(prep, ranked, arm, model)
    prep.timings["rank_ms"] = (time.perf_counter() - t1) * 1000
    prep.timings["total_ms"] = (time.perf_counter() - t0) * 1000
    low = None
    if arm == "F" and model is not None and model.no_answer_threshold is not None:
        low = [r["score"] < model.no_answer_threshold for r in results]
        for r, flag in zip(results, low):
            r["low_relevance"] = flag
    search_id = str(uuid.uuid4())
    resp = {
        "search_id": search_id,
        "query": req.query,
        "arm": arm,
        "arm_label": ARM_LABELS[arm],
        "model_version": model.id if model else None,
        "results": results,
        "timings": {k: round(v, 1) for k, v in prep.timings.items()},
        "candidate_counts": {ch: len(v) for ch, v in prep.channel_hits.items()},
        "candidate_pool": len(prep.cands),
        "videos_searched": len(prep.snapshot),
    }
    if req.record:
        filters = {"video_ids": req.video_ids, "exclude_video_ids": req.exclude_video_ids,
                   "content_types": req.content_types}
        compact = [{k: r[k] for k in ("rank", "video_id", "index_version", "segment_id", "start_ms", "end_ms",
                                      "score", "video_title")} for r in results]
        with connection() as conn:
            conn.execute(
                "INSERT INTO searches (id, owner_id, collection_id, query, filters, ranker, model_version, snapshot, "
                "results, timings) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (search_id, req.owner_id, req.collection_id, req.query, json.dumps(filters), arm,
                 resp["model_version"], json.dumps({k: v["version"] for k, v in prep.snapshot.items()}),
                 json.dumps(compact), json.dumps(resp["timings"])))
            conn.commit()
    return resp


