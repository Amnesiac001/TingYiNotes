from classnote.qt_gui import friendly_error


def test_friendly_error_explains_missing_key() -> None:
    message = friendly_error("Realtime classroom requires OPENAI_API_KEY")
    assert "设置" in message
    assert "OPENAI_API_KEY" in message


def test_friendly_error_explains_rate_limit() -> None:
    message = friendly_error("Error code: 429 rate limit exceeded")
    assert "频繁" in message
    assert "额度" in message


def test_friendly_error_preserves_unknown_details() -> None:
    assert friendly_error("设备被其他程序占用") == "设备被其他程序占用"


def test_friendly_error_explains_audio_device_failures() -> None:
    assert "断开" in friendly_error("Audio device invalidated")
    assert "独占" in friendly_error("Audio device busy")
    assert "隐私和安全性" in friendly_error("Microphone permission denied")


def test_friendly_error_explains_unsupported_sample_rate() -> None:
    message = friendly_error("Invalid sample rate [PaErrorCode -9997]")
    assert "原生采样率" in message
    assert "刷新设备" in message
