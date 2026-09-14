from pathlib import Path

import pytest

import classnote.recovery as recovery
from classnote.config import Settings
from classnote.models import CourseResult, Segment
from classnote.recovery import recover_course
from classnote.storage import CourseRepository


class RecoveryProcessor:
    def translate(self, text: str, subject: str, terms: dict[str, str]) -> str:
        return f"已补译：{text}"

    def organize(self, title: str, subject: str, original: str, translation: str) -> str:
        return f"# {title}\n\n{translation}"


def test_recovery_refuses_a_course_that_is_still_active(tmp_path: Path) -> None:
    repository = CourseRepository(tmp_path / "active-recovery.db")
    course = CourseResult("正在上课", "网络", "local:mic", [], "")
    repository.create_course(course)
    repository.add_segment(course.id, Segment("Live English", "", 0, 1000), 0, "pending")

    with pytest.raises(RuntimeError, match="课堂仍在录音或处理"):
        recover_course(course.id, repository, text_processor=RecoveryProcessor())

    assert repository.get_course(course.id)["status"] == "recording"
    assert repository.pending_segments(course.id)[0]["translation_status"] == "pending"


def test_recover_course_translates_pending_rebuilds_notes_and_exports(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("CLASSNOTE_EXPORT_DIR", str(tmp_path / "exports"))
    settings = Settings.load()
    repository = CourseRepository(tmp_path / "recovery.db")
    result = CourseResult("待恢复课堂", "网络", "mic", [], "")
    repository.create_course(result)
    completed = Segment("First", "第一", 0, 1000)
    pending = Segment("Second", "", 1000, 2000)
    repository.add_segment(result.id, completed, 0, "completed")
    repository.add_segment(result.id, pending, 1, "retry")
    repository.add_course_topic(result.id, 1000, "第二部分")
    repository.set_course_state(result.id, "interrupted")

    recovered, path = recover_course(
        result.id,
        repository,
        settings=settings,
        text_processor=RecoveryProcessor(),
    )

    assert path.exists()
    assert "已补译：Second" in path.read_text(encoding="utf-8")
    assert "- 00:01　第二部分" in path.read_text(encoding="utf-8")
    assert recovered.notes_markdown.startswith("# 待恢复课堂")
    assert repository.pending_segments(result.id) == []
    assert repository.get_course(result.id)["status"] == "completed"


def test_recovery_export_failure_keeps_draft_and_requires_attention(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("CLASSNOTE_EXPORT_DIR", str(tmp_path / "exports"))
    repository = CourseRepository(tmp_path / "recovery-failure.db")
    result = CourseResult("待恢复课堂", "网络", "mic", [], "")
    repository.create_course(result)
    repository.add_segment(result.id, Segment("First", "第一", 0, 1000), 0, "completed")
    repository.set_course_state(result.id, "interrupted")

    def fail_export(*args: object) -> Path:
        raise OSError("磁盘不可写")

    real_export = recovery.export_markdown
    monkeypatch.setattr(recovery, "export_markdown", fail_export)
    with pytest.raises(OSError, match="磁盘不可写"):
        recover_course(
            result.id,
            repository,
            settings=Settings.load(),
            text_processor=RecoveryProcessor(),
        )

    saved = repository.get_course(result.id)
    assert saved["status"] == "needs_attention"
    assert saved["notes_markdown"].startswith("# 待恢复课堂")
    assert saved["notes_draft_ready"] == 1
    assert saved["export_path"] == ""

    class NoApiProcessor(RecoveryProcessor):
        def organize(self, title: str, subject: str, original: str, translation: str) -> str:
            raise AssertionError("saved draft should be reused")

    monkeypatch.setattr(recovery, "export_markdown", real_export)
    recovered, path = recover_course(
        result.id, repository, settings=Settings.load(), text_processor=NoApiProcessor(),
    )
    assert path.exists()
    assert recovered.notes_markdown == saved["notes_markdown"]
    assert repository.get_course(result.id)["status"] == "completed"


def test_recovery_reexports_saved_draft_without_another_model_request(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("CLASSNOTE_EXPORT_DIR", str(tmp_path / "exports"))
    repository = CourseRepository(tmp_path / "saved-draft.db")
    course = CourseResult("课堂", "网络", "mic", [], "")
    repository.create_course(course)
    repository.add_segment(course.id, Segment("First", "第一", 0, 1000), 0, "completed")
    repository.set_course_state(course.id, "organizing")
    repository.save_notes_draft(course.id, "# 已保存的笔记")
    repository.mark_active_courses_interrupted()

    monkeypatch.setattr(
        recovery, "create_text_processor",
        lambda *_args: (_ for _ in ()).throw(AssertionError("API client created")),
    )
    progress: list[str] = []
    recovered, path = recover_course(
        course.id, repository, settings=Settings.load(), progress=progress.append,
    )
    assert recovered.notes_markdown == "# 已保存的笔记"
    assert path.exists()
    assert "# 已保存的笔记" in path.read_text(encoding="utf-8")
    assert any("重新导出" in message for message in progress)
    assert repository.get_course(course.id)["notes_draft_ready"] == 0


def test_recovery_regenerates_notes_when_only_old_notes_exist(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("CLASSNOTE_EXPORT_DIR", str(tmp_path / "exports"))
    repository = CourseRepository(tmp_path / "old-notes.db")
    course = CourseResult(
        "课堂", "网络", "mic", [Segment("New English", "新中文", 0, 1000)], "# 旧整理"
    )
    repository.save(course)
    repository.set_course_state(course.id, "needs_attention", "字幕已校对，需要重新整理")
    assert repository.get_course(course.id)["notes_draft_ready"] == 0

    recovered, _ = recover_course(
        course.id, repository, settings=Settings.load(), text_processor=RecoveryProcessor(),
    )
    assert recovered.notes_markdown != "# 旧整理"
    assert "新中文" in recovered.notes_markdown


def test_recovery_pauses_after_two_consecutive_translation_failures(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("CLASSNOTE_EXPORT_DIR", str(tmp_path / "exports"))
    repository = CourseRepository(tmp_path / "service-failure.db")
    course = CourseResult("待补译课堂", "网络", "mic", [], "")
    repository.create_course(course)
    for index in range(5):
        repository.add_segment(
            course.id, Segment(f"Sentence {index}", "", index * 1000, (index + 1) * 1000),
            index, "retry",
        )
    repository.set_course_state(course.id, "interrupted")

    class FailingProcessor(RecoveryProcessor):
        def __init__(self) -> None:
            self.calls: list[str] = []

        def translate(self, text: str, subject: str, terms: dict[str, str]) -> str:
            self.calls.append(text)
            raise RuntimeError("401 Invalid API key")

        def organize(self, title: str, subject: str, original: str, translation: str) -> str:
            raise AssertionError("failed translation must not start organizing")

    processor = FailingProcessor()
    with pytest.raises(RuntimeError, match="连续 2 句补译失败.*仍有 5 句待补译"):
        recover_course(
            course.id, repository, settings=Settings.load(), text_processor=processor,
        )

    assert processor.calls == ["Sentence 0", "Sentence 1"]
    assert [row["translation_status"] for row in repository.get_course_segments(course.id)] == [
        "failed", "failed", "retry", "retry", "retry",
    ]
    assert repository.get_course(course.id)["status"] == "needs_attention"
    assert len(repository.pending_segments(course.id)) == 5

    recovered, path = recover_course(
        course.id, repository, settings=Settings.load(), text_processor=RecoveryProcessor(),
    )
    assert path.exists()
    assert len(recovered.segments) == 5
    assert repository.pending_segments(course.id) == []
    assert repository.get_course(course.id)["status"] == "completed"


def test_recovery_continues_after_one_isolated_translation_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("CLASSNOTE_EXPORT_DIR", str(tmp_path / "exports"))
    repository = CourseRepository(tmp_path / "isolated-failure.db")
    course = CourseResult("待补译课堂", "网络", "mic", [], "")
    repository.create_course(course)
    for index in range(3):
        repository.add_segment(
            course.id, Segment(f"Sentence {index}", "", index * 1000, (index + 1) * 1000),
            index, "retry",
        )
    repository.set_course_state(course.id, "interrupted")

    class IntermittentProcessor(RecoveryProcessor):
        def __init__(self) -> None:
            self.calls: list[str] = []

        def translate(self, text: str, subject: str, terms: dict[str, str]) -> str:
            self.calls.append(text)
            if text == "Sentence 0":
                raise RuntimeError("temporary error")
            return super().translate(text, subject, terms)

    processor = IntermittentProcessor()
    with pytest.raises(RuntimeError, match="仍有 1 句待补译"):
        recover_course(
            course.id, repository, settings=Settings.load(), text_processor=processor,
        )

    assert processor.calls == ["Sentence 0", "Sentence 1", "Sentence 2"]
    assert [row["translation_status"] for row in repository.get_course_segments(course.id)] == [
        "failed", "completed", "completed",
    ]
    assert repository.get_course(course.id)["status"] == "needs_attention"


def test_recovery_preserves_class_time_with_late_and_overlapping_subtitles(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("CLASSNOTE_EXPORT_DIR", str(tmp_path / "exports"))
    repository = CourseRepository(tmp_path / "late-recovery.db")
    result = CourseResult("交叠课堂", "网络", "mic", [], "")
    repository.create_course(result)
    repository.add_segment(result.id, Segment("First", "第一", 0, 10_000), 0, "completed")
    repository.add_segment(result.id, Segment("Third", "", 13_000, 14_000), 1, "retry")
    repository.add_segment(result.id, Segment("Second", "第二", 1000, 2000), 2, "completed")
    repository.add_course_topic(result.id, 8000, "第一部分")
    repository.set_course_state(result.id, "interrupted")

    recovered, path = recover_course(
        result.id,
        repository,
        settings=Settings.load(),
        text_processor=RecoveryProcessor(),
    )

    assert [item.original_text for item in recovered.segments] == [
        "First", "Second", "Third"
    ]
    assert recovered.notes_markdown.index("第一") < recovered.notes_markdown.index("第二")
    content = path.read_text(encoding="utf-8")
    assert "### 00:00–00:10 · 2 句" in content
    assert "- 00:08　第一部分" in content
    transcript = content.split("## 英中对照记录", 1)[1]
    assert transcript.index("First Second") < transcript.index("Third")
    assert repository.get_course(result.id)["status"] == "completed"
