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
