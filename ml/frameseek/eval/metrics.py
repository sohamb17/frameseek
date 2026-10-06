"""Retrieval and localization metrics (pure functions, unit-tested).

Terminology follows the blueprint: "query success@K" means at least one of the top K
results is in a correct video and overlaps a labeled answer interval with temporal IoU >= tau.
It is not called "recall" because only one valid hit is required.
"""
from __future__ import annotations

import random
from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class Interval:
    video_id: str
    start_ms: int
    end_ms: int


def iou(a: Interval, b: Interval) -> float:
    if a.video_id != b.video_id:
        return 0.0
    inter = max(0, min(a.end_ms, b.end_ms) - max(a.start_ms, b.start_ms))
    union = max(a.end_ms, b.end_ms) - min(a.start_ms, b.start_ms)
    return inter / union if union > 0 else 0.0


def coverage(pred: Interval, ans: Interval) -> float:
    """Fraction of the answer interval covered by the prediction."""
    if pred.video_id != ans.video_id:
        return 0.0
    inter = max(0, min(pred.end_ms, ans.end_ms) - max(pred.start_ms, ans.start_ms))
    return inter / max(1, ans.end_ms - ans.start_ms)


def best_iou(pred: Interval, answers: list[Interval]) -> float:
    return max((iou(pred, a) for a in answers), default=0.0)


def first_hit_rank(ranked: list[Interval], answers: list[Interval], tau: float) -> int | None:
    for i, r in enumerate(ranked):
        if best_iou(r, answers) >= tau:
            return i + 1
    return None


def success_at(ranked: list[Interval], answers: list[Interval], k: int, tau: float) -> bool:
    r = first_hit_rank(ranked[:k], answers, tau)
    return r is not None


def is_positive(pred: Interval, answers: list[Interval], tau: float = 0.3, min_coverage: float = 0.5) -> bool:
    """Training label for a candidate window: overlaps an answer well enough to be useful.

    Fixed 20 s windows cannot reach IoU 0.3 with answers shorter than 6 s, so a window that
    covers at least half of an answer also counts as positive for *training*. Evaluation keeps
    the stricter IoU definition so the localization limitation stays visible.
    """
    return any(iou(pred, a) >= tau or coverage(pred, a) >= min_coverage for a in answers)


def localization(top: Interval, answers: list[Interval]) -> dict | None:
    """Errors of a top result relative to its best-overlapping answer (None if no overlap)."""
    same = [a for a in answers if a.video_id == top.video_id and iou(top, a) > 0]
    if not same:
        return None
    a = max(same, key=lambda x: iou(top, x))
    inter = max(0, min(top.end_ms, a.end_ms) - max(top.start_ms, a.start_ms))
    return {
        "iou": iou(top, a),
        "start_err_s": abs(top.start_ms - a.start_ms) / 1000,
        "end_err_s": abs(top.end_ms - a.end_ms) / 1000,
        "excess_s": ((top.end_ms - top.start_ms) - inter) / 1000,
    }


def percentile(values: list[float], p: float) -> float | None:
    return float(np.percentile(values, p)) if values else None


def grouped_bootstrap(per_query: list[tuple[str, float]], n: int = 1000, seed: int = 7) -> tuple[float, float] | None:
    """95% CI of the mean, resampling whole groups (videos/series) instead of queries.

    Queries about the same video are correlated, so resampling queries would understate
    uncertainty on a small corpus.
    """
    if not per_query:
        return None
    groups: dict[str, list[float]] = {}
    for g, v in per_query:
        groups.setdefault(g, []).append(v)
    keys = list(groups)
    if len(keys) < 2:
        return None
    rng = random.Random(seed)
    means = []
    for _ in range(n):
        sample = [v for _ in keys for v in groups[rng.choice(keys)]]
        means.append(sum(sample) / len(sample))
    means.sort()
    return means[int(0.025 * n)], means[int(0.975 * n) - 1]
