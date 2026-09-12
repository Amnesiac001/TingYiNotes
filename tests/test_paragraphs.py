from classnote.models import Segment
from classnote.paragraphs import (
    ParagraphRules, group_segments, paragraph_time_bounds, should_start_new_paragraph,
)


def sentence(text: str, start: int, end: int) -> Segment:
    return Segment(text, "", start, end)


def test_groups_three_to_five_continuous_classroom_sentences() -> None:
    segments = [
        sentence(f"Sentence {index} explains congestion control.", index * 3000, index * 3000 + 2400)
        for index in range(7)
    ]
    paragraphs = group_segments(segments)

    assert [len(paragraph) for paragraph in paragraphs] == [5, 2]


def test_strong_pause_and_transition_start_new_paragraph() -> None:
    current = [
        sentence("The congestion window grows.", 0, 2500),
        sentence("This is called slow start.", 2700, 5200),
    ]
    assert should_start_new_paragraph(current, sentence("It later grows linearly.", 7500, 9500))
    assert should_start_new_paragraph(current, sentence("Now let's move to flow control.", 5400, 8000))


def test_length_and_duration_limits_do_not_split_a_single_short_lead_in() -> None:
    rules = ParagraphRules(max_duration_ms=10_000, max_english_chars=80)
    first = sentence("A" * 70, 0, 9000)
    second = sentence("B" * 40, 9200, 12_000)
    third = sentence("C", 12_100, 12_500)

    assert not should_start_new_paragraph([first], second, rules)
    assert should_start_new_paragraph([first, second], third, rules)


def test_late_subtitles_are_grouped_by_class_time_not_arrival_order() -> None:
    first = sentence("First.", 0, 1000)
    second = sentence("Second.", 4000, 5000)
    third = sentence("Third.", 8000, 9000)

    paragraphs = group_segments([first, third, second])

    assert [[item.original_text for item in group] for group in paragraphs] == [
        ["First."], ["Second."], ["Third."]
    ]


def test_overlapping_sentences_keep_their_actual_paragraph_end() -> None:
    current = [sentence("Long sentence.", 0, 10_000), sentence("Short overlap.", 1000, 2000)]
    incoming = sentence("Continuation.", 11_000, 12_000)

    assert paragraph_time_bounds(current) == (0, 10_000)
    assert not should_start_new_paragraph(current, incoming)
    assert paragraph_time_bounds([*current, incoming]) == (0, 12_000)
