from frameseek.eval.metrics import Interval, coverage, first_hit_rank, grouped_bootstrap, iou, is_positive, localization


def I(v, a, b):
    return Interval(v, a * 1000, b * 1000)


def test_iou_requires_same_video():
    assert iou(I("a", 0, 10), I("b", 0, 10)) == 0
    assert iou(I("a", 0, 10), I("a", 5, 15)) == 5 / 15


def test_first_hit_rank_and_threshold():
    answers = [I("a", 40, 50)]
    ranked = [I("b", 40, 50), I("a", 30, 50), I("a", 40, 60)]
    assert first_hit_rank(ranked, answers, 0.3) == 2      # 10/20 = 0.5
    assert first_hit_rank(ranked, answers, 0.6) is None


def test_short_answers_count_as_training_positives_via_coverage():
    win, ans = I("a", 0, 20), [I("a", 5, 9)]
    assert iou(win, ans[0]) < 0.3 and coverage(win, ans[0]) == 1.0
    assert is_positive(win, ans)
    assert not is_positive(I("a", 30, 50), ans)


def test_localization_errors():
    loc = localization(I("a", 10, 30), [I("a", 12, 20)])
    assert loc["start_err_s"] == 2 and loc["end_err_s"] == 10 and loc["excess_s"] == 12


def test_grouped_bootstrap_needs_two_groups():
    assert grouped_bootstrap([("g", 1.0), ("g", 0.0)]) is None
    lo, hi = grouped_bootstrap([("g1", 1.0), ("g2", 0.0), ("g3", 1.0)])
    assert 0 <= lo <= hi <= 1
