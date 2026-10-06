import numpy as np

from frameseek.retrieval.features import FEATURE_NAMES, build_matrix
from frameseek.retrieval.search import temporal_nms


def cand(i, **kw):
    base = dict(id=i, video_id="v", start_ms=0, end_ms=20_000, transcript="x", ocr_text="", n_frames=10,
                t_lex=0.1, t_sem=0.5, o_lex=0, o_sem=None, v_max=0.25, v_mean=0.2,
                speech_coverage=0.5, ocr_coverage=0, ocr_stability=0)
    base.update(kw)
    return base


def test_feature_matrix_shape_missing_indicators_and_ranks():
    cands = [cand(1), cand(2, transcript="", t_sem=None, v_max=0.3)]
    X = build_matrix(cands, {"transcript_sem": {1: 1}, "visual": {2: 1, 1: 2}}, "a query")
    assert X.shape == (2, len(FEATURE_NAMES))
    col = {n: X[:, i] for i, n in enumerate(FEATURE_NAMES)}
    assert col["no_speech"].tolist() == [0, 1] and col["no_ocr"].tolist() == [1, 1]
    assert col["rr_transcript_sem"].tolist() == [1.0, 0.0]
    assert col["rr_visual"].tolist() == [0.5, 1.0]
    assert np.isclose(col["v_max_rel"], [-0.05, 0.0]).all()   # relative to the query's best candidate
    assert col["q_len"].tolist() == [2, 2]


def test_temporal_nms_drops_overlapping_neighbours_only_in_same_video():
    order = [dict(video_id="a", start_ms=10_000, end_ms=30_000), dict(video_id="a", start_ms=20_000, end_ms=40_000),
             dict(video_id="b", start_ms=10_000, end_ms=30_000), dict(video_id="a", start_ms=40_000, end_ms=60_000)]
    kept = temporal_nms(order, 0.3)
    assert [(k["video_id"], k["start_ms"]) for k in kept] == [("a", 10_000), ("b", 10_000), ("a", 40_000)]
