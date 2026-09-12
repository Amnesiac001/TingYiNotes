from classnote.marked_context import build_marked_contexts, marked_contexts_markdown
from classnote.models import Segment


def test_marker_collects_nearby_subtitles_before_and_after() -> None:
    segments = [
        Segment("Too early.", "太早。", 0, 1000),
        Segment("Before.", "之前。", 10_000, 11_000),
        Segment("Anchor.", "标记句。", 25_000, 26_000, marker="important"),
        Segment("After.", "之后。", 40_000, 41_000),
        Segment("Too late.", "太晚。", 50_000, 51_000),
    ]

    contexts = build_marked_contexts(segments)

    assert len(contexts) == 1
    assert contexts[0].english == "Before. Anchor. After."
    assert contexts[0].chinese == "之前。 标记句。 之后。"
    assert (contexts[0].start_ms, contexts[0].end_ms) == (10_000, 41_000)
    assert "⭐ 重点" in marked_contexts_markdown(contexts)


def test_marker_updates_when_following_translation_arrives_and_clears() -> None:
    anchor = Segment("Question?", "疑问？", 0, 1000, marker="question")
    following = Segment("Explanation.", "", 3000, 4000)

    assert build_marked_contexts([anchor, following])[0].chinese == "疑问？"
    following.translated_text = "解释。"
    assert build_marked_contexts([anchor, following])[0].chinese == "疑问？ 解释。"
    anchor.marker = ""
    assert build_marked_contexts([anchor, following]) == []


def test_untimed_legacy_segments_use_neighbouring_sequence() -> None:
    segments = [Segment(f"Sentence {index}") for index in range(8)]
    segments[4].marker = "important"

    context = build_marked_contexts(segments)[0]

    assert context.english == "Sentence 2 Sentence 3 Sentence 4 Sentence 5 Sentence 6"


def test_context_window_handles_unsorted_overlapping_segments() -> None:
    anchor = Segment("Anchor.", "标记。", 30_000, 31_000, marker="important")
    overlap = Segment("Long explanation.", "长解释。", 5000, 15_000)
    outside = Segment("Outside.", "外部。", 1000, 2000)

    context = build_marked_contexts([anchor, outside, overlap])[0]

    assert context.english == "Long explanation. Anchor."
