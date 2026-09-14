import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from classnote.models import CourseResult, Segment
from classnote.qt_gui import LibraryPage
from classnote.storage import CourseRepository


def test_library_selects_exact_course_shows_transcript_and_deletes(monkeypatch, tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    repository = CourseRepository(tmp_path / "library-ui.db")
    keep = CourseResult("Keep", "Subject", "mic", [Segment("Keep this", "保留")], "")
    remove = CourseResult(
        "Remove",
        "Subject",
        "mic",
        [Segment("Congestion window", "拥塞窗口", 1000, 2000)],
        "# Notes",
    )
    repository.save(keep)
    repository.save(remove)
    page = LibraryPage(repository)
    monkeypatch.setattr("classnote.qt_gui.confirm_course_delete", lambda *_: True)

    page.select_course(remove.id)
    app.processEvents()
    assert page.rows[page.list.currentRow()]["id"] == remove.id
    assert "Congestion window" in page.preview.toPlainText()
    assert page.delete_button.isEnabled()

    page.delete_selected()
    app.processEvents()
    assert repository.count_courses() == 1
    assert repository.get_course_segments(remove.id) == []
    assert page.rows[page.list.currentRow()]["id"] == keep.id

    page.close()
    page.deleteLater()
    app.processEvents()


def test_library_preview_uses_full_range_of_overlapping_sentences(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    repository = CourseRepository(tmp_path / "overlap-preview.db")
    result = CourseResult(
        "课堂", "网络", "mic",
        [
            Segment("Long first.", "第一句。", 0, 10_000),
            Segment("Short overlap.", "第二句。", 1000, 2000),
        ],
        "# 整理笔记",
    )
    repository.save(result)
    page = LibraryPage(repository)
    page.select_course(result.id)
    app.processEvents()

    assert "00:00–00:10 · 2 句" in page.preview.toPlainText()
    page.close()
    page.deleteLater()
    app.processEvents()


def test_active_class_cannot_be_deleted_or_recovered_from_library(monkeypatch, tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    repository = CourseRepository(tmp_path / "active-library.db")
    course = CourseResult("正在上课", "网络", "local:mic", [], "")
    repository.create_course(course)
    repository.add_segment(course.id, Segment("Live English", "", 0, 1000), 0, "pending")
    messages: list[str] = []
    monkeypatch.setattr(
        "classnote.qt_gui.show_message",
        lambda _parent, _icon, title, _body: messages.append(title),
    )
    monkeypatch.setattr(
        "classnote.qt_gui.confirm_course_delete",
        lambda *_args: (_ for _ in ()).throw(AssertionError("active class prompted for deletion")),
    )
    page = LibraryPage(repository)
    page.select_course(course.id)

    assert not page.delete_button.isEnabled()
    assert not page.recover_button.isEnabled()
    page.delete_selected()
    page.recover_selected()
    assert messages == ["暂时不能删除", "暂时不能补译"]
    assert repository.get_course(course.id) is not None
    assert not repository.delete_course_if_inactive(course.id)

    page.close()
    page.deleteLater()
    app.processEvents()


def test_library_rechecks_course_status_after_stale_selection(monkeypatch, tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    repository = CourseRepository(tmp_path / "stale-library.db")
    course = CourseResult("状态变化", "网络", "local:mic", [Segment("Saved", "已保存")], "")
    repository.save(course)
    page = LibraryPage(repository)
    page.select_course(course.id)
    assert page.delete_button.isEnabled()
    repository.set_course_state(course.id, "transcribing")
    messages: list[str] = []
    monkeypatch.setattr(
        "classnote.qt_gui.show_message",
        lambda _parent, _icon, title, _body: messages.append(title),
    )
    page.delete_selected()

    assert messages == ["暂时不能删除"]
    assert repository.get_course(course.id) is not None
    page.close()
    page.deleteLater()
    app.processEvents()
