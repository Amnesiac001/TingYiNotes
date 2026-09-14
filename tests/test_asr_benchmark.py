import wave
from types import SimpleNamespace

import numpy as np
import pytest

from tools.benchmark_local_asr import benchmark_windows, read_windows, word_error_rate


class FakeModel:
    def __init__(self):
        self.calls = []

    def transcribe(self, audio, **options):
        self.calls.append((len(audio), options))
        return iter((SimpleNamespace(text="  hello class  "),)), None


def test_benchmark_consumes_windows_and_uses_live_options():
    model = FakeModel()
    times = iter((0.0, 0.2, 0.2, 0.5))
    result = benchmark_windows(
        model,
        (np.zeros(16000, dtype=np.float32), np.zeros(8000, dtype=np.float32)),
        clock=lambda: next(times),
    )
    assert result["audio_seconds"] == 1.5
    assert result["inference_seconds"] == pytest.approx(0.5)
    assert result["rtf"] == pytest.approx(1 / 3)
    assert result["slow_windows"] == 0
    assert result["transcript"] == "hello class hello class"
    assert all(options["beam_size"] == 1 for _, options in model.calls)
    assert all(options["vad_filter"] is False for _, options in model.calls)


def test_read_windows_streams_pcm_and_rejects_wrong_format(tmp_path):
    path = tmp_path / "sample.wav"
    with wave.open(str(path), "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(16000)
        writer.writeframes(np.arange(16000, dtype="<i2").tobytes())
    parts = list(read_windows(path, 0.4))
    assert [len(part) for part in parts] == [6400, 6400, 3200]
    assert parts[0][1] == pytest.approx(1 / 32768)

    with wave.open(str(path), "wb") as writer:
        writer.setnchannels(2)
        writer.setsampwidth(2)
        writer.setframerate(16000)
        writer.writeframes(bytes(16000 * 4))
    with pytest.raises(ValueError, match="单声道"):
        list(read_windows(path, 10))
    with pytest.raises(ValueError, match="有限"):
        list(read_windows(path, float("nan")))


def test_word_error_rate_ignores_case_and_punctuation():
    assert word_error_rate("Hello, CLASS!", "hello class") == 0
    assert word_error_rate("one two three", "one four three") == pytest.approx(1 / 3)
    assert word_error_rate("", "hello") is None
