from pathlib import Path

import pytest

import classnote.editing as editing
from classnote.config import Settings
from classnote.editing import correct_course_segment, undo_course_segment_correction
from classnote.models import CourseResult, Segment
from classnote.recovery import recover_course
from classnote.storage import CourseRepository


class CorrectedProcessor:
    def translate(self, text: str, subject: str, terms: dict[str, str]) -> str:
        return f"重译：{text}"

    def organize(self, title: str, subject: str, original: str, translation: str) -> str:
        return f"# {title}\n\n{original}\n\n{translation}"


def test_post_class_correction_updates_transcript_and_flags_old_notes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("CLASSNOTE_EXPORT_DIR", str(tmp_path / "exports"))
    repository = CourseRepository(tmp_path / "correction.db")
    segment = Segment("Original.", "旧译文。", 1000, 2000, marker="important")
    course = CourseResult("课堂", "网络", "mic", [segment], "# 旧整理")
    repository.save(course)

    outcome = correct_course_segment(
        repository, course.id, segment.id, "Corrected.", "新译文。",
        "Original.", "旧译文。", settings=Settings.load(),
    )

    saved = repository.get_course_segments(course.id)[0]
    course_row = repository.get_course(course.id)
    assert outcome.changed and outcome.export_path is not None
    assert outcome.export_path.exists()
    assert saved["original_text"] == "Corrected."
    assert saved["translated_text"] == "新译文。"
    assert saved["marker"] == "important"
    assert saved["translation_status"] == "completed"
    assert course_row["status"] == "needs_attention"
    assert course_row["notes_markdown"] == "# 旧整理"
    exported = outcome.export_path.read_text(encoding="utf-8")
    assert "原 AI 整理笔记尚未重新生成" in exported
    assert "Corrected." in exported and "新译文。" in exported


def test_changing_english_requires_reviewing_or_clearing_old_chinese(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("CLASSNOTE_EXPORT_DIR", str(tmp_path / "exports"))
    repository = CourseRepository(tmp_path / "paired.db")
    segment = Segment("Old English.", "旧中文。", 0, 1000)
    course = CourseResult("课堂", "网络", "mic", [segment], "# 整理")
    repository.save(course)

    with pytest.raises(ValueError, match="同时校对中文"):
        correct_course_segment(
            repository, course.id, segment.id, "New English.", "旧中文。",
            "Old English.", "旧中文。",
        )
    assert repository.get_course_segments(course.id)[0]["original_text"] == "Old English."

    outcome = correct_course_segment(
        repository, course.id, segment.id, "New English.", "",
        "Old English.", "旧中文。",
    )
    assert outcome.changed
    saved = repository.get_course_segments(course.id)[0]
    assert saved["original_text"] == "New English."
    assert saved["translated_text"] == ""
    assert saved["translation_status"] == "retry"
    assert len(repository.pending_segments(course.id)) == 1


def test_confirmed_translation_can_be_kept_after_english_typo_fix(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("CLASSNOTE_EXPORT_DIR", str(tmp_path / "exports"))
    repository = CourseRepository(tmp_path / "confirmed.db")
    segment = Segment("Welcom to class.", "欢迎上课。", 0, 1000)
    course = CourseResult("课堂", "网络", "mic", [segment], "# 旧整理")
    repository.save(course)

    outcome = correct_course_segment(
        repository, course.id, segment.id, "Welcome to class.", "欢迎上课。",
        "Welcom to class.", "欢迎上课。", settings=Settings.load(),
        keep_translation_confirmed=True,
    )

    saved = repository.get_course_segments(course.id)[0]
    assert outcome.changed
    assert saved["original_text"] == "Welcome to class."
    assert saved["translated_text"] == "欢迎上课。"
    assert saved["translation_status"] == "completed"


def test_correction_rejects_active_and_stale_records(tmp_path: Path) -> None:
    repository = CourseRepository(tmp_path / "active.db")
    segment = Segment("Old.", "旧。", 0, 1000)
    course = CourseResult("课堂", "网络", "mic", [], "")
    repository.create_course(course)
    repository.add_segment(course.id, segment, 0, "completed")

    with pytest.raises(RuntimeError, match="结束后"):
        repository.correct_segment(course.id, segment.id, "New.", "新。", "Old.", "旧。")
    repository.set_course_state(course.id, "needs_attention")
    with pytest.raises(RuntimeError, match="其他操作更新"):
        repository.correct_segment(course.id, segment.id, "New.", "新。", "Stale.", "旧。")
    assert repository.get_course_segments(course.id)[0]["original_text"] == "Old."


def test_export_failure_does_not_undo_saved_correction(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repository = CourseRepository(tmp_path / "export-failure.db")
    segment = Segment("Old.", "旧。", 0, 1000)
    course = CourseResult("课堂", "网络", "mic", [segment], "# 旧整理")
    repository.save(course)
    def fail_export(*args: object) -> Path:
        raise OSError("磁盘不可写")

    monkeypatch.setattr(editing, "export_markdown", fail_export)

    outcome = correct_course_segment(
        repository, course.id, segment.id, "Old.", "新。", "Old.", "旧。",
    )

    assert outcome.changed and outcome.export_path is None
    assert "磁盘不可写" in outcome.export_error
    assert repository.get_course_segments(course.id)[0]["translated_text"] == "新。"
    assert repository.get_course(course.id)["status"] == "needs_attention"


def test_reorganizing_after_correction_uses_corrected_text(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("CLASSNOTE_EXPORT_DIR", str(tmp_path / "exports"))
    repository = CourseRepository(tmp_path / "reorganize.db")
    segment = Segment("Old English.", "旧中文。", 0, 1000)
    course = CourseResult("课堂", "网络", "mic", [segment], "# 旧整理")
    repository.save(course)
    correct_course_segment(
        repository, course.id, segment.id, "Correct English.", "",
        "Old English.", "旧中文。", settings=Settings.load(),
    )

    result, path = recover_course(
        course.id, repository, settings=Settings.load(), text_processor=CorrectedProcessor(),
    )

    assert "Correct English." in result.notes_markdown
    assert "重译：Correct English." in path.read_text(encoding="utf-8")
    assert repository.get_course(course.id)["status"] == "completed"


def test_correction_history_can_undo_multiple_versions_after_reopening(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("CLASSNOTE_EXPORT_DIR", str(tmp_path / "exports"))
    path = tmp_path / "history.db"
    repository = CourseRepository(path)
    segment = Segment("Original.", "原译。", 0, 1000)
    course = CourseResult("课堂", "网络", "mic", [segment], "# 旧整理")
    repository.save(course)
    settings = Settings.load()
    correct_course_segment(repository, course.id, segment.id, "Original.", "第一版。", "Original.", "原译。", settings)
    correct_course_segment(repository, course.id, segment.id, "Original.", "第二版。", "Original.", "第一版。", settings)
    reopened = CourseRepository(path)

    assert reopened.has_segment_corrections(segment.id)
    first_undo = undo_course_segment_correction(
        reopened, course.id, segment.id, "Original.", "第二版。", settings
    )
    assert first_undo.changed and first_undo.export_path is not None
    assert reopened.get_course_segments(course.id)[0]["translated_text"] == "第一版。"
    assert "第一版。" in first_undo.export_path.read_text(encoding="utf-8")

    undo_course_segment_correction(reopened, course.id, segment.id, "Original.", "第一版。", settings)
    assert reopened.get_course_segments(course.id)[0]["translated_text"] == "原译。"
    assert not reopened.has_segment_corrections(segment.id)
    assert not undo_course_segment_correction(
        reopened, course.id, segment.id, "Original.", "原译。", settings
    ).changed
    assert reopened.delete_course(course.id)


def test_correction_history_is_removed_with_course(tmp_path: Path) -> None:
    repository = CourseRepository(tmp_path / "cascade.db")
    segment = Segment("Original.", "原译。", 0, 1000)
    course = CourseResult("课堂", "网络", "mic", [segment], "# 旧整理")
    repository.save(course)
    repository.correct_segment(course.id, segment.id, "Original.", "新译。", "Original.", "原译。")

    assert repository.has_segment_corrections(segment.id)
    repository.delete_course(course.id)
    assert not repository.has_segment_corrections(segment.id)
