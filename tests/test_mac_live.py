from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np

import classnote.mac_live as mac_live
from classnote.mac_live import (
    EnergyVadOptions,
    MacLocalLiveCourseSession,
    get_energy_speech_timestamps,
    preload_mlx_model,
)
from classnote.services import MLXWhisperTranscriber


def test_energy_vad_finds_speech_and_preserves_trailing_silence() -> None:
    silence = np.zeros(16000, dtype=np.float32)
    tone = np.full(8000, 0.03, dtype=np.float32)
    audio = np.concatenate([silence, tone, silence])

    timestamps = get_energy_speech_timestamps(audio, EnergyVadOptions())

    assert len(timestamps) == 1
    assert timestamps[0]["start"] < 16000
    assert timestamps[0]["end"] < len(audio) - 4000


def test_energy_vad_rejects_silence() -> None:
    assert get_energy_speech_timestamps(np.zeros(32000, dtype=np.float32), EnergyVadOptions()) == []


def test_mac_live_transcribe_passes_numpy_audio_and_model() -> None:
    session = MacLocalLiveCourseSession.__new__(MacLocalLiveCourseSession)
    session.transcription_model_name = "mlx-community/test-model"
    session.course_context = SimpleNamespace(hotword_prompt="Fourier transform")
    received: dict[str, object] = {}

    class FakeMLX:
        @staticmethod
        def transcribe(audio: np.ndarray, **kwargs: object) -> dict[str, str]:
            received["audio"] = audio
            received.update(kwargs)
            return {"text": "  A stable lecture sentence.  "}

    audio = np.ones(16000, dtype=np.float32)
    text = session._transcribe(FakeMLX(), audio)

    assert text == "A stable lecture sentence."
    assert received["audio"] is audio
    assert received["path_or_hf_repo"] == "mlx-community/test-model"
    assert received["initial_prompt"] == "Fourier transform"


def test_mlx_file_transcriber_converts_result_segments(monkeypatch, tmp_path: Path) -> None:
    fake_module = SimpleNamespace(
        transcribe=lambda *args, **kwargs: {
            "text": "First. Second.",
            "segments": [
                {"start": 0.25, "end": 1.5, "text": " First."},
                {"start": 1.5, "end": 2.75, "text": " Second."},
            ],
        }
    )
    monkeypatch.setitem(sys.modules, "mlx_whisper", fake_module)

    segments = MLXWhisperTranscriber("mlx-community/test").transcribe(
        tmp_path / "lecture.wav", "Physics"
    )

    assert [segment.original_text for segment in segments] == ["First.", "Second."]
    assert (segments[0].start_ms, segments[1].end_ms) == (250, 2750)


def test_preload_mlx_model_only_warms_once(monkeypatch) -> None:
    calls: list[tuple[object, dict[str, object]]] = []
    fake_module = SimpleNamespace(
        transcribe=lambda audio, **kwargs: calls.append((audio, kwargs)) or {"text": ""}
    )
    monkeypatch.setattr(mac_live.platform, "machine", lambda: "arm64")
    monkeypatch.setitem(sys.modules, "mlx_whisper", fake_module)
    mac_live._MLX_MODELS_READY.clear()

    assert preload_mlx_model("mlx-community/test-preload") is False
    assert preload_mlx_model("mlx-community/test-preload") is True
    assert len(calls) == 1
    assert calls[0][1]["path_or_hf_repo"] == "mlx-community/test-preload"
