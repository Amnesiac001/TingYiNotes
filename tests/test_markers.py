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
