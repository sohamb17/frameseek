"""Pure functions that turn timestamped observations into searchable segments.

Kept free of I/O so they are unit-tested directly (tests/test_segments.py).
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Word:
    s_ms: int
    e_ms: int
    text: str


@dataclass
class FrameObs:
    id: int | None
    idx: int
    ts_ms: int
    ocr_text: str
    ocr_words: int
    ocr_reused: bool


@dataclass
class Segment:
    idx: int
    start_ms: int
    end_ms: int
    transcript: str = ""
    ocr_text: str = ""
    speech_coverage: float = 0.0
    ocr_coverage: float = 0.0
    ocr_stability: float = 0.0
    frame_idxs: list[int] = field(default_factory=list)
    thumb_frame_idx: int | None = None


def make_windows(duration_ms: int, window_ms: int, stride_ms: int) -> list[tuple[int, int]]:
    """Fixed overlapping windows covering [0, duration]. The last window is aligned to the end."""
    if duration_ms <= 0:
        return []
    if duration_ms <= window_ms:
        return [(0, duration_ms)]
    out = []
    start = 0
    while start + window_ms < duration_ms:
        out.append((start, start + window_ms))
        start += stride_ms
    tail = (duration_ms - window_ms, duration_ms)
    if not out or out[-1] != tail:
        out.append(tail)
    return out


def interval_union_ms(intervals: list[tuple[int, int]], lo: int, hi: int) -> int:
    clipped = sorted((max(s, lo), min(e, hi)) for s, e in intervals if e > lo and s < hi)
    total, cur_s, cur_e = 0, None, None
    for s, e in clipped:
        if cur_e is None or s > cur_e:
            if cur_e is not None:
                total += cur_e - cur_s
            cur_s, cur_e = s, e
        else:
            cur_e = max(cur_e, e)
    if cur_e is not None:
        total += cur_e - cur_s
    return total


def _dedupe_lines(texts: list[str]) -> str:
    seen, out = set(), []
    for t in texts:
        for line in t.splitlines():
            key = " ".join(line.lower().split())
            if key and key not in seen:
                seen.add(key)
                out.append(line.strip())
    return "\n".join(out)


def build_segments(duration_ms: int, words: list[Word], frames: list[FrameObs], window_ms: int,
                   stride_ms: int, min_ocr_words: int = 3) -> list[Segment]:
    segs = []
    for i, (lo, hi) in enumerate(make_windows(duration_ms, window_ms, stride_ms)):
        seg = Segment(idx=i, start_ms=lo, end_ms=hi)
        in_words = [w for w in words if lo <= (w.s_ms + w.e_ms) // 2 < hi]
        seg.transcript = " ".join(w.text for w in in_words).strip()
        seg.speech_coverage = round(interval_union_ms([(w.s_ms, w.e_ms) for w in in_words], lo, hi) / (hi - lo), 4)
        in_frames = [f for f in frames if lo <= f.ts_ms < hi]
        seg.frame_idxs = [f.idx for f in in_frames]
        if in_frames:
            seg.ocr_text = _dedupe_lines([f.ocr_text for f in in_frames])
            seg.ocr_coverage = round(sum(f.ocr_words >= min_ocr_words for f in in_frames) / len(in_frames), 4)
            seg.ocr_stability = round(sum(f.ocr_reused for f in in_frames) / len(in_frames), 4)
            mid = (lo + hi) / 2
            seg.thumb_frame_idx = min(in_frames, key=lambda f: abs(f.ts_ms - mid)).idx
        elif frames:
            mid = (lo + hi) / 2
            seg.thumb_frame_idx = min(frames, key=lambda f: abs(f.ts_ms - mid)).idx
        segs.append(seg)
    return segs
