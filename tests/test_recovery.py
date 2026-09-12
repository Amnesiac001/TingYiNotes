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
    assert saved["export_path"] == ""
