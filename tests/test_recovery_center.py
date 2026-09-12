from __future__ import annotations

import os
import wave
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QWidget

from classnote.models import CourseResult, Segment, new_id
from classnote.recovery_center import RecoveryCenterDialog
from classnote.recovery_inventory import list_recovery_candidates
from classnote.qt_gui import MainWindow
from classnote.storage import CourseRepository


def _wav(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as recording:
        recording.setnchannels(1)
        recording.setsampwidth(2)
        recording.setframerate(16000)
        recording.writeframes(b"\0\0" * 1600)


def test_inventory_separates_saved_text_audio_only_and_completed(tmp_path: Path) -> None:
    repository = CourseRepository(tmp_path / "course.db")
    interrupted = CourseResult("中断课程", "网络", "mic", [], "")
    repository.create_course(interrupted)
    repository.add_segment(interrupted.id, Segment("RTT", "", 0, 1000), 0, "retry")
    repository.set_course_state(interrupted.id, "interrupted")
    audio_course = CourseResult("只有录音", "物理", "mic", [], "")
    repository.create_course(audio_course)
    repository.set_course_state(audio_course.id, "failed")
    _wav(tmp_path / "temporary-audio" / f"{audio_course.id}.wav")
    completed = CourseResult("已完成", "网络", "mic", [Segment("Done", "完成")], "# 笔记")
    repository.save(completed)
    _wav(tmp_path / "temporary-audio" / f"{completed.id}.wav")
    orphan_id = new_id()
    _wav(tmp_path / "temporary-audio" / f"{orphan_id}.wav")
    (tmp_path / "temporary-audio" / "not-a-course.wav").write_bytes(b"bad")

    found = list_recovery_candidates(repository)
    assert len(found) == 3
    by_title = {item.title: item for item in found}
    assert by_title["中断课程"].can_resume
    assert by_title["中断课程"].pending_count == 1
    assert not by_title["只有录音"].can_resume
    assert by_title["只有录音"].audio_path is not None
    assert by_title["未关联的课堂录音"].audio_path.name == f"{orphan_id}.wav"
    assert "已完成" not in by_title


def test_recovery_dialog_offers_only_available_actions(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    repository = CourseRepository(tmp_path / "course.db")
    text_course = CourseResult("字幕可恢复", "网络", "mic", [], "")
    repository.create_course(text_course)
    repository.add_segment(text_course.id, Segment("Hello", "", 0, 1000), 0, "pending")
    repository.set_course_state(text_course.id, "interrupted")
    audio_course = CourseResult("录音可恢复", "物理", "mic", [], "")
    repository.create_course(audio_course)
    repository.set_course_state(audio_course.id, "failed")
    _wav(tmp_path / "temporary-audio" / f"{audio_course.id}.wav")
    resumed: list[str] = []
    opened: list[tuple[Path, str, str]] = []
    parent = QWidget()
    dialog = RecoveryCenterDialog(
        repository, resumed.append,
        lambda path, title, subject: opened.append((path, title, subject)), parent,
    )
    text_index = next(i for i, row in enumerate(dialog.candidates) if row.title == "字幕可恢复")
    dialog.list.setCurrentRow(text_index)
    assert dialog.resume_button.isEnabled()
    assert not dialog.audio_button.isEnabled()
    dialog._resume()
    assert resumed == [text_course.id]
    audio_index = next(i for i, row in enumerate(dialog.candidates) if row.title == "录音可恢复")
    dialog.list.setCurrentRow(audio_index)
    assert not dialog.resume_button.isEnabled()
    assert dialog.audio_button.isEnabled()
    dialog._open_audio()
    assert opened[0][1:] == ("录音可恢复", "物理")
    parent.close()


def test_home_recovery_entry_and_audio_navigation(monkeypatch, tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    monkeypatch.setenv("CLASSNOTE_DB", str(tmp_path / "course.db"))
    window = MainWindow()
    course = CourseResult("需要恢复", "网络", "mic", [], "")
    window.repository.create_course(course)
    window.repository.set_course_state(course.id, "failed")
    path = tmp_path / "temporary-audio" / f"{course.id}.wav"
    _wav(path)
    window.home_page.reload()
    assert not window.home_page.recovery_card.isHidden()
    assert "1 项" in window.home_page.recovery_label.text()
    window.open_recovery_audio(path, course.title, course.subject)
    assert window.stack.currentWidget() is window.file_page
    assert window.file_page.path == str(path)
    assert window.file_page.subject_input.text() == "网络"
    called: list[bool] = []
    monkeypatch.setattr(window.library_page, "recover_selected", lambda: called.append(True))
    window.resume_course(new_id())
    assert not called
    window.close()
