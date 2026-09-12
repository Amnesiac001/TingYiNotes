from classnote.models import Segment
from classnote.recent_index import RecentSegmentIndex


def test_index_updates_append_only_history_and_late_subtitles() -> None:
    index = RecentSegmentIndex()
    history = [Segment("First", "第一", 0, 1000)]
    index.update(history)
    assert [item.original_text for item in index.recent(120_000)] == ["First"]

    history.append(Segment("Latest", "最新", 300_000, 301_000))
    index.update(history)
    assert [item.original_text for item in index.recent(120_000)] == ["Latest"]

    history.append(Segment("Late arrival", "晚到", 250_000, 251_000))
    index.update(history)
    assert [item.original_text for item in index.recent(120_000)] == ["Late arrival", "Latest"]


def test_index_keeps_mutated_translation_and_rebuilds_for_a_new_course() -> None:
    index = RecentSegmentIndex()
    pending = Segment("Pending", "", 0, 1000)
    first_course = [pending]
    index.update(first_course)
    pending.translated_text = "已翻译"
    index.update(first_course)
    assert index.recent(5000)[0].translated_text == "已翻译"

    second_course = [Segment("New course", "新课程", 0, 2000)]
    index.update(second_course)
    assert [item.original_text for item in index.recent(5000)] == ["New course"]
    index.clear()
    assert index.recent(5000) == []
