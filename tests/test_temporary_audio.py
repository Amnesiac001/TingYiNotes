from __future__ import annotations

import time
import wave
import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication

from classnote.config import Settings
from classnote.models import CourseResult, new_id
from classnote.storage import CourseRepository
from classnote.temporary_audio import (
    TemporaryAudioRecorder, finish_temporary_audio, start_temporary_audio,
)
from classnote.qt_gui import SettingsPage


def test_temporary_audio_is_disabled_by_default(monkeypatch) -> None:
    monkeypatch.delenv("CLASSNOTE_TEMP_AUDIO", raising=False)
    assert not Settings.load().temporary_audio
    monkeypatch.setenv("CLASSNOTE_TEMP_AUDIO", "true")
    assert Settings.load().temporary_audio


def test_settings_page_shows_saved_audio_choice(monkeypatch) -> None:
    application = QApplication.instance() or QApplication([])
    monkeypatch.setenv("CLASSNOTE_TEMP_AUDIO", "true")
    page = SettingsPage(Settings.load())
    assert page.temporary_audio_check.isChecked()
    page.close()


def test_audio_backup_writes_valid_wav_and_deletes_after_success(tmp_path: Path) -> None:
    repository = CourseRepository(tmp_path / "classnote.db")
    course = CourseResult("课", "网络", "mic", [], "")
    repository.create_course(course)
    warnings: list[str] = []
    recorder = start_temporary_audio(
        repository.database_path, course.id, 16000, warnings.append,
    )
    assert recorder is not None
    pcm = b"\x01\x00" * 16000
    recorder.submit(pcm)
    # In-progress data becomes a playable WAV before the classroom finishes.
    deadline = time.monotonic() + 3
    while not recorder.path.exists() or recorder.path.stat().st_size < 44 + len(pcm):
        assert time.monotonic() < deadline
        time.sleep(0.02)
    with wave.open(str(recorder.path), "rb") as wav:
        assert wav.getframerate() == 16000
        assert wav.getnframes() == 16000
    repository.finalize_course(course.id, "# 课", str(tmp_path / "notes.md"))
    finish_temporary_audio(recorder, repository, course.id, warnings.append)
    assert not recorder.path.exists()
    assert not warnings


def test_failed_class_keeps_audio_for_recovery(tmp_path: Path) -> None:
    repository = CourseRepository(tmp_path / "classnote.db")
    course = CourseResult("课", "网络", "mic", [], "")
    repository.create_course(course)
    warnings: list[str] = []
    recorder = TemporaryAudioRecorder(repository.database_path, course.id, 24000, warnings.append)
    recorder.start()
    recorder.submit(b"\x00\x00" * 2400)
    repository.set_course_state(course.id, "needs_attention", "中断")
    finish_temporary_audio(recorder, repository, course.id, warnings.append)
    assert recorder.path.exists()
    with wave.open(str(recorder.path), "rb") as wav:
        assert wav.getnframes() == 2400
    assert any("保留" in message for message in warnings)


def test_audio_backup_only_accepts_a_course_uuid(tmp_path: Path) -> None:
    try:
        TemporaryAudioRecorder(tmp_path / "db.sqlite", "../outside", 16000, lambda _: None)
    except ValueError:
        pass
    else:
        raise AssertionError("untrusted course ID was accepted")
    assert TemporaryAudioRecorder(tmp_path / "db.sqlite", new_id(), 16000, lambda _: None)
