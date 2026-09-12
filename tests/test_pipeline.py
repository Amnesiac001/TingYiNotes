from pathlib import Path

from classnote.models import Segment
from classnote.services import CoursePipeline
from classnote.storage import CourseRepository


class FakeTranscriber:
    def transcribe(self, audio_path: Path, subject: str) -> list[Segment]:
        return [Segment("A lecture sentence.")]


class FakeTextProcessor:
    def translate(self, text: str, subject: str, terms: dict[str, str]) -> str:
        return "一句课堂内容。"

    def organize(self, title: str, subject: str, original: str, translation: str) -> str:
        return f"# {title}\n\n{translation}"


def test_pipeline_builds_complete_result(tmp_path: Path) -> None:
    audio = tmp_path / "lesson.mp3"
    audio.write_bytes(b"fake")
    messages: list[str] = []
    pipeline = CoursePipeline(FakeTranscriber(), FakeTextProcessor(), messages.append)
    result = pipeline.run(audio, "第一课", "网络", {"TCP": "TCP"})
    assert result.original_text == "A lecture sentence."
    assert result.translated_text == "一句课堂内容。"
    assert result.notes_markdown.startswith("# 第一课")
    assert len(messages) == 4


class MultiSegmentTranscriber:
    def transcribe(self, audio_path: Path, subject: str) -> list[Segment]:
        return [
            Segment("First sentence.", start_ms=0, end_ms=1000),
            Segment("Second sentence.", start_ms=1000, end_ms=2200),
        ]


class PerSegmentProcessor:
    def translate(self, text: str, subject: str, terms: dict[str, str]) -> str:
        return {"First sentence.": "第一句。", "Second sentence.": "第二句。"}[text]

    def organize(self, title: str, subject: str, original: str, translation: str) -> str:
        return f"# {title}\n\n{translation}"


def test_incremental_pipeline_persists_each_translation_with_its_segment(tmp_path: Path) -> None:
    audio = tmp_path / "lesson.wav"
    audio.write_bytes(b"fake")
    repository = CourseRepository(tmp_path / "incremental.db")
    pipeline = CoursePipeline(MultiSegmentTranscriber(), PerSegmentProcessor())

    result = pipeline.run_incremental(audio, "课堂", "网络", repository)
    rows = repository.get_course_segments(result.id)

    assert [row["translated_text"] for row in rows] == ["第一句。", "第二句。"]
    assert [row["translation_status"] for row in rows] == ["completed", "completed"]
    course = repository.get_course(result.id)
    assert course["status"] == "organizing"
    assert course["notes_markdown"] == result.notes_markdown


class PartlyFailingProcessor(PerSegmentProcessor):
    def translate(self, text: str, subject: str, terms: dict[str, str]) -> str:
        if text.startswith("Second"):
            raise RuntimeError("network unavailable")
        return super().translate(text, subject, terms)


def test_incremental_pipeline_keeps_english_when_translation_fails(tmp_path: Path) -> None:
    import pytest

    audio = tmp_path / "lesson.wav"
    audio.write_bytes(b"fake")
    repository = CourseRepository(tmp_path / "failed.db")
    pipeline = CoursePipeline(MultiSegmentTranscriber(), PartlyFailingProcessor())

    with pytest.raises(RuntimeError, match="英文已保存"):
        pipeline.run_incremental(audio, "课堂", "网络", repository)

    course = repository.list_courses()[0]
    rows = repository.get_course_segments(str(course["id"]))
    assert [row["original_text"] for row in rows] == ["First sentence.", "Second sentence."]
    assert rows[1]["translation_status"] == "failed"
    assert course["status"] == "needs_attention"
