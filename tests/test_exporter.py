from pathlib import Path

import pytest

import classnote.exporter as exporter
from classnote.exporter import export_markdown, format_time, safe_filename
from classnote.models import CourseResult, Segment


def test_safe_filename_removes_windows_reserved_characters() -> None:
    assert safe_filename('网络课: 第一讲?/\\') == "网络课_ 第一讲___"


def test_format_time() -> None:
    assert format_time(0) == "00:00"
    assert format_time(125_000) == "02:05"


def test_export_contains_notes_and_transcript(tmp_path: Path) -> None:
    result = CourseResult(
        title="测试课",
        subject="测试",
        source_path="test.mp3",
        segments=[Segment("Hello", "你好", 1000, 2000)],
        notes_markdown="# 测试课\n\n摘要",
    )
    path = export_markdown(result, tmp_path)
    content = path.read_text(encoding="utf-8")
    assert "# 测试课" in content
    assert "Hello" in content
    assert "你好" in content
    assert "00:01" in content


def test_export_groups_continuous_sentences_into_paragraphs(tmp_path: Path) -> None:
    result = CourseResult(
        title="课堂",
        subject="测试",
        source_path="mic",
        segments=[
            Segment(f"Sentence {index}.", f"句子 {index}。", index * 3000, index * 3000 + 2000)
            for index in range(4)
        ],
        notes_markdown="# 课堂",
    )
    content = export_markdown(result, tmp_path).read_text(encoding="utf-8")

    assert "00:00–00:11 · 4 句" in content
    assert "Sentence 0. Sentence 1. Sentence 2. Sentence 3." in content
    assert content.count("**English**") == 1


def test_export_preserves_manual_important_and_question_markers(tmp_path: Path) -> None:
    result = CourseResult(
        title="标记课堂",
        subject="网络",
        source_path="mic",
        segments=[
            Segment("Important.", "重点。", 0, 1000, marker="important"),
            Segment("Why?", "为什么？", 1200, 2000, marker="question"),
        ],
        notes_markdown="# 课堂",
    )

    content = export_markdown(result, tmp_path).read_text(encoding="utf-8")

    assert "⭐ 重点" in content
    assert "❓ 疑问" in content
    assert "## 课堂手动标记与前后语境" in content
    assert "Important. Why?" in content
    assert "[课堂手动标记" not in content


def test_failed_reexport_keeps_existing_note_and_removes_temporary_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    result = CourseResult(
        title="课堂",
        subject="测试",
        source_path="mic",
        segments=[Segment("Original.", "原文。", 0, 1000)],
        notes_markdown="# 初版笔记",
    )
    path = export_markdown(result, tmp_path)
    original_content = path.read_text(encoding="utf-8")
    result.notes_markdown = "# 更新后的笔记"

    def fail_replace(source: Path, target: Path) -> None:
        assert target == path
        assert source.read_text(encoding="utf-8").startswith("# 更新后的笔记")
        raise OSError("文件暂时被占用")

    monkeypatch.setattr(exporter.os, "replace", fail_replace)
    with pytest.raises(OSError, match="文件暂时被占用"):
        export_markdown(result, tmp_path)

    assert path.read_text(encoding="utf-8") == original_content
    assert list(tmp_path.glob("*.tmp")) == []


def test_successful_reexport_replaces_note_without_temporary_file(tmp_path: Path) -> None:
    result = CourseResult("课堂", "测试", "mic", [], "# 初版笔记")
    path = export_markdown(result, tmp_path)
    result.notes_markdown = "# 更新后的笔记"

    assert export_markdown(result, tmp_path) == path
    assert path.read_text(encoding="utf-8").startswith("# 更新后的笔记")
    assert list(tmp_path.glob("*.tmp")) == []
