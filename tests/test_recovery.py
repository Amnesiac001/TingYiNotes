from pathlib import Path

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
    repository.set_course_state(result.id, "interrupted")

    recovered, path = recover_course(
        result.id,
        repository,
        settings=settings,
        text_processor=RecoveryProcessor(),
    )

    assert path.exists()
    assert "已补译：Second" in path.read_text(encoding="utf-8")
    assert recovered.notes_markdown.startswith("# 待恢复课堂")
    assert repository.pending_segments(result.id) == []
    assert repository.get_course(result.id)["status"] == "completed"
