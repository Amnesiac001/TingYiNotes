from pathlib import Path

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
