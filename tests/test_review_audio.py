from __future__ import annotations

import wave
from pathlib import Path

import numpy as np
import pytest

from classnote.models import CourseResult
from classnote.review_audio import load_review_clip, review_reasons
from classnote.storage import CourseRepository


def test_review_reasons_are_observable_not_confidence() -> None:
    row: dict[str, object] = {
        "original_text": "one two three four five", "translated_text": "",
        "translation_status": "failed", "start_ms": 1000, "end_ms": 1100,
    }
    assert review_reasons(row) == ["中文待补译", "语速与时间戳不匹配"]
    row.update(translated_text="中文", translation_status="completed", end_ms=3000)
    assert review_reasons(row) == []


def test_review_clip_reads_only_selected_local_wav(tmp_path: Path) -> None:
    repository = CourseRepository(tmp_path / "classnote.db")
    course = CourseResult("课", "网络", "mic", [], "")
    repository.create_course(course)
    with pytest.raises(FileNotFoundError):
        load_review_clip(repository, course.id, 1000, 2000)
    path = tmp_path / "temporary-audio" / f"{course.id}.wav"
    path.parent.mkdir()
    audio = np.arange(40000, dtype=np.int16)
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16000)
        wav.writeframes(audio.tobytes())
    clip, rate = load_review_clip(repository, course.id, 1000, 1500, padding_ms=0)
    assert rate == 16000
    assert len(clip) == 16000
    assert clip[0] == audio[16000]


def test_review_clip_rejects_path_traversal(tmp_path: Path) -> None:
    repository = CourseRepository(tmp_path / "classnote.db")
    with pytest.raises(FileNotFoundError):
        load_review_clip(repository, "../outside", 0, 1000)
