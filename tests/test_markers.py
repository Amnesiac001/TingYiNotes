from classnote.models import CourseResult, Segment


def test_manual_markers_are_included_only_in_organizer_context() -> None:
    result = CourseResult(
        "课堂",
        "网络",
        "mic",
        [
            Segment("Window grows.", "窗口增长。", marker="important"),
            Segment("Why?", "为什么？", marker="question"),
        ],
        "",
    )

    assert "[课堂手动标记：重点] Window grows." in result.organized_original_text
    assert "[课堂手动标记：疑问] 为什么？" in result.organized_translated_text
    assert "课堂手动标记" not in result.original_text
    assert "课堂手动标记" not in result.translated_text


def test_final_organizer_context_uses_class_time_for_late_subtitles() -> None:
    first = Segment("First.", "第一句。", 0, 1000)
    second = Segment("Second.", "第二句。", 4000, 5000)
    third = Segment("Third.", "第三句。", 8000, 9000)
    result = CourseResult("课堂", "网络", "mic", [first, third, second], "")

    assert result.organized_original_text == "First.\nSecond.\nThird."
    assert result.organized_translated_text == "第一句。\n第二句。\n第三句。"
