import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication, QWidget

from classnote.config import Settings
from classnote.models import CourseResult, Segment
from classnote.qt_gui import LibraryPage
from classnote.segment_editor import SegmentEditorDialog
from classnote.storage import CourseRepository


def test_segment_editor_saves_both_languages_and_searches_updated_text(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    app = QApplication.instance() or QApplication([])
    monkeypatch.setenv("CLASSNOTE_EXPORT_DIR", str(tmp_path / "exports"))
    repository = CourseRepository(tmp_path / "editor.db")
    segment = Segment("Old English.", "旧中文。", 0, 1000)
    course = CourseResult("课堂", "网络", "mic", [segment], "# 旧整理")
    repository.save(course)
    parent = QWidget()
    editor = SegmentEditorDialog(repository, course.id, course.title, parent, Settings.load())

    assert editor.current_id == segment.id
    editor.english.setPlainText("Correct English.")
    editor.chinese.setPlainText("正确中文。")
    assert editor.save_button.isEnabled()
    editor.save_current()

    assert editor.saved_any
    assert not editor.save_button.isEnabled()
    assert editor.undo_button.isEnabled()
    assert repository.get_course_segments(course.id)[0]["original_text"] == "Correct English."
    editor.search.setText("Correct English")
    assert not editor.list.item(0).isHidden()
    editor.search.setText("No matching sentence")
    assert editor.list.item(0).isHidden()
    editor.undo_current()
    assert editor.english.toPlainText() == "Old English."
    assert editor.chinese.toPlainText() == "旧中文。"
    assert not editor.undo_button.isEnabled()
    editor.close()
    parent.close()
    app.processEvents()


def test_segment_editor_keeps_unsaved_text_when_switch_is_cancelled(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    app = QApplication.instance() or QApplication([])
    monkeypatch.setenv("CLASSNOTE_EXPORT_DIR", str(tmp_path / "exports"))
    repository = CourseRepository(tmp_path / "switch.db")
    first = Segment("First.", "第一句。", 0, 1000)
    second = Segment("Second.", "第二句。", 2000, 3000)
    course = CourseResult("课堂", "网络", "mic", [first, second], "# 旧整理")
    repository.save(course)
    parent = QWidget()
    editor = SegmentEditorDialog(repository, course.id, course.title, parent, Settings.load())
    monkeypatch.setattr(editor, "_confirm_discard", lambda message: False)
    editor.english.setPlainText("Unsaved change.")

    editor.list.setCurrentRow(1)

    assert editor.list.currentRow() == 0
    assert editor.current_id == first.id
    assert editor.english.toPlainText() == "Unsaved change."
    editor.english.setPlainText("First.")
    editor.close()
    parent.close()
    app.processEvents()


def test_editor_requires_explicit_confirmation_to_keep_old_chinese(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    app = QApplication.instance() or QApplication([])
    monkeypatch.setenv("CLASSNOTE_EXPORT_DIR", str(tmp_path / "exports"))
    repository = CourseRepository(tmp_path / "confirm-ui.db")
    segment = Segment("Welcom.", "欢迎。", 0, 1000)
    course = CourseResult("课堂", "网络", "mic", [segment], "# 旧整理")
    repository.save(course)
    parent = QWidget()
    editor = SegmentEditorDialog(repository, course.id, course.title, parent, Settings.load())

    editor.english.setPlainText("Welcome.")
    assert not editor.keep_chinese.isHidden()
    assert not editor.save_button.isEnabled()
    editor.keep_chinese.setChecked(True)
    assert editor.save_button.isEnabled()
    editor.save_current()

    assert repository.get_course_segments(course.id)[0]["translated_text"] == "欢迎。"
    editor.close()
    parent.close()
    app.processEvents()


def test_library_enables_post_class_edit_and_refreshes_corrected_preview(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    app = QApplication.instance() or QApplication([])
    monkeypatch.setenv("CLASSNOTE_EXPORT_DIR", str(tmp_path / "exports"))
    repository = CourseRepository(tmp_path / "library-edit.db")
    segment = Segment("Old.", "旧。", 0, 1000)
    course = CourseResult("课堂", "网络", "mic", [segment], "# 整理")
    repository.save(course)
    page = LibraryPage(repository)
    page.select_course(course.id)
    assert page.edit_button.isEnabled()

    editor = SegmentEditorDialog(repository, course.id, course.title, page, Settings.load())
    editor.chinese.setPlainText("新译文。")
    editor.save_current()
    editor.close()
    page.select_course(course.id)

    assert "新译文。" in page.preview.toPlainText()
    assert page.recover_button.isEnabled()
    assert page.recover_button.text() == "重新整理"
    page.close()
    app.processEvents()
