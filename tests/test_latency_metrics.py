from classnote.latency_metrics import LatencyWindow, format_quality_summary


def test_latency_window_uses_bounded_samples_and_nearest_rank() -> None:
    window = LatencyWindow(size=5)
    for value in (100, 200, 300, 400, 500, 600):
        assert window.add({"stage": "chinese_first", "ms": value})
    assert window.count("chinese_first") == 5
    assert window.percentile("chinese_first", 50) == 400
    assert window.percentile("chinese_first", 95) == 600
    assert not window.add({"stage": "chinese_first", "ms": -1})
    assert not window.add({"stage": "chinese_first", "ms": True})


def test_latency_window_tracks_optional_speech_end_estimate() -> None:
    window = LatencyWindow()
    assert window.add({"stage": "english", "ms": 520})
    assert window.add({"stage": "chinese_first", "ms": 800, "from_speech_end_ms": 1320})
    assert window.percentile("speech_to_chinese_first", 50) == 1320


def test_quality_summary_has_no_claimed_accuracy_percentage() -> None:
    window = LatencyWindow()
    for value in (100, 200, 300, 400, 500):
        window.add({"stage": "chinese_first", "ms": value})
    summary = window.summary(protected_mismatch_count=2)
    display = format_quality_summary(summary)
    assert "P95 0.50s" in display
    assert "建议核对：2 句" in display
    assert "不能据此计算识别准确率" in display
