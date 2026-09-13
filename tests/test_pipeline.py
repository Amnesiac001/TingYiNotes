from pathlib import Path
from types import SimpleNamespace

import classnote.config as config
import classnote.local_live as local_live
from classnote.models import Segment
from classnote.services import CoursePipeline, LocalWhisperTranscriber
from classnote.storage import CourseRepository


def test_file_transcriber_reuses_shared_model_and_stable_cache_path(monkeypatch, tmp_path: Path) -> None:
    selected: list[tuple[str, str, Path]] = []

    class CachedModel:
        def transcribe(self, audio, **kwargs):
            assert audio == str(tmp_path / "lesson.wav")
            assert kwargs["language"] == "en"
            return iter([SimpleNamespace(text=" A lecture sentence. ", start=0.2, end=1.4)]), None

    cached = CachedModel()

    def shared_model(name: str, compute: str, root: Path):
        selected.append((name, compute, root))
        return cached, True

    monkeypatch.setattr(local_live, "preload_local_model", shared_model)
    monkeypatch.setattr(
        config.Settings, "load",
        classmethod(lambda _cls: SimpleNamespace(database_path=tmp_path / "data" / "classnote.db")),
    )
    other_directory = tmp_path / "elsewhere"
    other_directory.mkdir()
    monkeypatch.chdir(other_directory)

    rows = LocalWhisperTranscriber("distil-large-v3").transcribe(tmp_path / "lesson.wav", "网络")
    LocalWhisperTranscriber(
        "distil-large-v3", model_root=tmp_path / "data" / "models"
    ).transcribe(tmp_path / "lesson.wav", "网络")

    assert selected == [("distil-large-v3", "float16", tmp_path / "data" / "models")] * 2
    assert [(row.original_text, row.start_ms, row.end_ms) for row in rows] == [
        ("A lecture sentence.", 200, 1400)
    ]


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
