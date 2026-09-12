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
