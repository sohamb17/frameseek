"""Feature construction for query/segment pairs (shared by serving, training and evaluation).

Serving/training parity: the exact same function builds the matrix in all three places,
and the fitted pipeline stores FEATURE_NAMES so a schema mismatch fails loudly.
"""
from __future__ import annotations

import re

import numpy as np

CHANNELS = ["transcript_lex", "transcript_sem", "ocr_lex", "ocr_sem", "visual"]
SCORE_FIELD = {"transcript_lex": "t_lex", "transcript_sem": "t_sem", "ocr_lex": "o_lex",
               "ocr_sem": "o_sem", "visual": "v_max"}

FEATURE_SCHEMA_VERSION = 1
FEATURE_NAMES = [
    # raw channel scores (0 when the modality is missing; see the indicators below)
    "t_lex", "t_sem", "o_lex", "o_sem", "v_max", "v_mean",
    # score relative to the best candidate for this query (channel scales are not comparable)
    "t_lex_rel", "t_sem_rel", "o_lex_rel", "o_sem_rel", "v_max_rel",
    # reciprocal channel ranks (0 = not retrieved by that channel)
    "rr_transcript_lex", "rr_transcript_sem", "rr_ocr_lex", "rr_ocr_sem", "rr_visual",
    "n_channels",
    # evidence quality / availability
    "speech_coverage", "ocr_coverage", "ocr_stability", "duration_s",
    "no_speech", "no_ocr", "no_frames",
    "q_len",
]


def query_len(q: str) -> int:
    return len(re.findall(r"\w+", q))


def build_matrix(cands: list[dict], channel_ranks: dict[str, dict[int, int]], query: str) -> np.ndarray:
    """cands: rows from retrieval.search._candidate_rows; channel_ranks: channel -> {segment_id: rank (1-based)}."""
    if not cands:
        return np.zeros((0, len(FEATURE_NAMES)), dtype=np.float32)
    def col(name):
        return np.array([float(c.get(name) or 0.0) for c in cands], dtype=np.float32)

    base = {k: col(k) for k in ["t_lex", "t_sem", "o_lex", "o_sem", "v_max", "v_mean"]}
    rel = {f"{k}_rel": base[k] - base[k].max() for k in ["t_lex", "t_sem", "o_lex", "o_sem", "v_max"]}
    rr = {}
    for ch in CHANNELS:
        ranks = channel_ranks.get(ch, {})
        rr[f"rr_{ch}"] = np.array([1.0 / ranks[c["id"]] if c["id"] in ranks else 0.0 for c in cands], dtype=np.float32)
    n_channels = sum((rr[f"rr_{ch}"] > 0).astype(np.float32) for ch in CHANNELS)
    feats = {
        **base, **rel, **rr, "n_channels": n_channels,
        "speech_coverage": col("speech_coverage"),
        "ocr_coverage": col("ocr_coverage"),
        "ocr_stability": col("ocr_stability"),
        "duration_s": np.array([(c["end_ms"] - c["start_ms"]) / 1000 for c in cands], dtype=np.float32),
        "no_speech": np.array([0.0 if (c.get("transcript") or "").strip() else 1.0 for c in cands], dtype=np.float32),
        "no_ocr": np.array([0.0 if (c.get("ocr_text") or "").strip() else 1.0 for c in cands], dtype=np.float32),
        "no_frames": np.array([0.0 if c.get("n_frames") else 1.0 for c in cands], dtype=np.float32),
        "q_len": np.full(len(cands), float(query_len(query)), dtype=np.float32),
    }
    return np.stack([feats[n] for n in FEATURE_NAMES], axis=1)
