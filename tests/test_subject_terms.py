from pathlib import Path
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QWidget

import classnote.services as services
from classnote.app import process_course
from classnote.models import CourseResult, Segment
from classnote.recovery import recover_course
from classnote.config import Settings
from classnote.courseware import CourseContext, merge_subject_context, relevant_terms
from classnote.storage import CourseRepository
from classnote.subject_terms_dialog import SubjectTermsDialog
from classnote.qt_gui import MainWindow


def test_subject_terms_persist_and_do_not_leak(tmp_path: Path) -> None:
    path = tmp_path / "terms.db"
    repository = CourseRepository(path)
    repository.save_subject_term("  Computer   Networks ", "TCP", "传输控制协议")
    repository.save_subject_term("computer networks", " tcp ", "传输控制协议（更新）")
    repository.save_subject_term("Biology", "TCP", "另一含义")

    reopened = CourseRepository(path)
    assert reopened.get_subject_terms("COMPUTER NETWORKS") == {"tcp": "传输控制协议（更新）"}
    assert reopened.get_subject_terms("biology") == {"TCP": "另一含义"}
    assert reopened.get_subject_terms("History") == {}
    assert reopened.delete_subject_term("computer networks", "TCP")
    assert not reopened.delete_subject_term("computer networks", "TCP")
    assert reopened.get_subject_terms("computer networks") == {}
    assert reopened.get_subject_terms("Biology") == {"TCP": "另一含义"}


def test_subject_terms_validate_and_bound_size(tmp_path: Path) -> None:
    repository = CourseRepository(tmp_path / "terms.db")
    with pytest.raises(ValueError):
        repository.save_subject_term("Networks", "", "中文")
    for index in range(repository.MAX_SUBJECT_TERMS):
        repository.save_subject_term("Networks", f"Term {index}", f"术语 {index}")
    with pytest.raises(ValueError, match="最多"):
        repository.save_subject_term("Networks", "One more", "额外")
    repository.save_subject_term("Networks", "Term 0", "更新")
    assert len(repository.get_subject_terms("Networks")) == repository.MAX_SUBJECT_TERMS


def test_courseware_overrides_remembered_terms_without_mutation() -> None:
    courseware = CourseContext(
        source_count=1, hotwords=("TCP", "Latency"),
        terms={"TCP": "课件释义"}, warnings=("sample warning",),
    )
    merged = merge_subject_context(courseware, {"tcp": "旧释义", "RTT": "往返时间"})
    assert merged.terms == {"TCP": "课件释义", "RTT": "往返时间"}
    assert merged.hotwords[:2] == ("TCP", "RTT")
    assert merged.source_count == 1
    assert merged.warnings == ("sample warning",)
    assert courseware.terms == {"TCP": "课件释义"}
    assert merge_subject_context(CourseContext(), {"RTT": "往返时间"}).hotwords == ("RTT",)


def test_only_relevant_terms_are_sent_for_each_segment() -> None:
    terms = {"TCP": "传输控制协议", "IP": "互联网协议", "RTT": "往返时间"}
    assert relevant_terms("TCP and RTT are related.", terms) == {
        "TCP": "传输控制协议", "RTT": "往返时间"
    }
    assert relevant_terms("The tip is useful.", terms) == {}
    assert len(relevant_terms("TCP IP RTT", terms, limit=2)) == 2


def test_subject_terms_dialog_adds_and_edits_locally(tmp_path: Path) -> None:
    application = QApplication.instance() or QApplication([])
    repository = CourseRepository(tmp_path / "dialog.db")
    parent = QWidget()
    dialog = SubjectTermsDialog(repository, "网络", parent)
    dialog.english.setText("RTT")
    dialog.chinese.setText("往返时间")
    dialog.save_term()
    assert repository.get_subject_terms("网络") == {"RTT": "往返时间"}
    dialog.new_term()
    dialog.english.setText("TCP")
    dialog.chinese.setText("传输控制协议")
    dialog.save_term()
    assert len(repository.get_subject_terms("网络")) == 2
    dialog.close()
    parent.close()


def test_live_page_uses_current_subject_without_leaking_old_terms(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    application = QApplication.instance() or QApplication([])
    monkeypatch.setenv("CLASSNOTE_DB", str(tmp_path / "live.db"))
    window = MainWindow()
    window.repository.save_subject_term("网络", "RTT", "往返时间")
    window.repository.save_subject_term("历史", "Rome", "罗马")
    live = window.live_page
    live.subject_input.setText("网络")
    context = live.effective_course_context()
    assert context.terms == {"RTT": "往返时间"}
    assert context.hotwords == ("RTT",)
    live.subject_input.setText("历史")
    assert live.effective_course_context().terms == {"Rome": "罗马"}
    assert live.course_context.terms == {}
    window.close()


def test_file_processing_uses_subject_terms_with_explicit_override(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("CLASSNOTE_DB", str(tmp_path / "files.db"))
    monkeypatch.setenv("CLASSNOTE_EXPORT_DIR", str(tmp_path / "exports"))
    repository = CourseRepository(tmp_path / "files.db")
    repository.save_subject_term("网络", "TCP", "长期释义")
    repository.save_subject_term("网络", "RTT", "往返时间")
    received: list[dict[str, str]] = []

    def fake_run(self, audio_path, title, subject, current_repository, terms):
        received.append(dict(terms))
        result = CourseResult(title, subject, str(audio_path), [Segment("TCP", "传输控制协议")], "# 课程")
        current_repository.save(result)
        return result

    monkeypatch.setattr(services.CoursePipeline, "run_incremental", fake_run)
    process_course(tmp_path / "test.wav", "课程", "网络", {"tcp": "临时释义"}, demo=True)
    assert received == [{"tcp": "临时释义", "RTT": "往返时间"}]


def test_recovery_passes_subject_terms_to_translation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("CLASSNOTE_EXPORT_DIR", str(tmp_path / "exports"))
    repository = CourseRepository(tmp_path / "recover.db")
    repository.save_subject_term("网络", "RTT", "往返时间")
    course = CourseResult("课", "网络", "mic", [], "")
    repository.create_course(course)
    repository.add_segment(course.id, Segment("RTT", "", 0, 1000), 0, "retry")

    class Processor:
        def translate(self, text, subject, terms):
            assert terms == {"RTT": "往返时间"}
            return "往返时间"

        def organize(self, title, subject, original, translation):
            return "# 课"

    recover_course(course.id, repository, settings=Settings.load(), text_processor=Processor())
