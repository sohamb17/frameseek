from frameseek.pipeline.segments import FrameObs, Word, build_segments, interval_union_ms, make_windows


def test_windows_cover_whole_video_and_align_last_window():
    w = make_windows(65_000, 20_000, 10_000)
    assert w[0] == (0, 20_000)
    assert w[-1] == (45_000, 65_000)
    assert all(b - a == 20_000 for a, b in w)


def test_short_video_is_one_window():
    assert make_windows(7_000, 20_000, 10_000) == [(0, 7_000)]


def test_interval_union_merges_overlaps_and_clips():
    assert interval_union_ms([(0, 5), (3, 8), (20, 30)], 0, 25) == 8 + 5


def test_words_assigned_by_midpoint_and_coverage():
    words = [Word(1_000, 2_000, "hello"), Word(19_500, 20_500, "edge"), Word(25_000, 26_000, "later")]
    frames = [FrameObs(None, 0, 0, "Slide title\nline two", 4, False), FrameObs(None, 1, 2_000, "Slide title", 2, True)]
    segs = build_segments(30_000, words, frames, 20_000, 10_000)
    assert segs[0].transcript == "hello"          # 'edge' has midpoint 20.0 s -> second window
    assert "edge" in segs[1].transcript and "later" in segs[1].transcript
    assert segs[0].ocr_text == "Slide title\nline two"   # duplicate lines removed
    assert segs[0].ocr_coverage == 0.5               # one of two frames has >= 3 words
    assert segs[0].ocr_stability == 0.5              # one frame reused the previous OCR
    assert 0 < segs[0].speech_coverage < 0.1
