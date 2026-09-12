import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from classnote.models import Segment
from classnote.qt_gui import MainWindow, ParagraphCard, QuickReviewPane, RecentReviewPane


def test_paragraph_card_grows_and_updates_one_sentence_translation() -> None:
    app = QApplication.instance() or QApplication([])
    first = Segment("First sentence.", "第一句。", 0, 2000)
    second = Segment("Second sentence.", "", 2300, 4200)
    card = ParagraphCard(first)

    card.add_segment(second)
    assert "2 句" in card.meta.text()
    assert "First sentence. Second sentence." == card.english.text()
    assert "正在翻译" in card.chinese.text()
    assert not card.detail_button.isHidden()

    card.update_translation(second.id, "第二句。")
    assert card.chinese.text() == "第一句。 第二句。"
    assert "Second sentence." in card.details.text()

    card.close()
    card.deleteLater()
    app.processEvents()


def test_paragraph_card_uses_latest_end_for_overlapping_sentences() -> None:
    app = QApplication.instance() or QApplication([])
    card = ParagraphCard(Segment("Long first.", "第一句。", 0, 10_000))
    card.add_segment(Segment("Short overlap.", "第二句。", 1000, 2000))

    assert "00:00–00:10" in card.meta.text()
    card.close()
    card.deleteLater()
    app.processEvents()


def test_hidden_sentence_details_are_not_rebuilt_for_every_stream_delta() -> None:
    app = QApplication.instance() or QApplication([])
    first = Segment("First.", "第一句。", 0, 1000)
    second = Segment("Second.", "", 1200, 2200)
    card = ParagraphCard(first)
    card.add_segment(second)
    before = card.details.text()

    card.update_translation(second.id, "流式片段", final=False)

    assert card.chinese.text() == "第一句。 流式片段"
    assert card.details.text() == before
    card.toggle_details()
    assert "流式片段" in card.details.text()
    card.close()
    card.deleteLater()
    app.processEvents()


def test_paragraph_card_is_chinese_first_and_english_can_be_revealed() -> None:
    app = QApplication.instance() or QApplication([])
    card = ParagraphCard(Segment("Congestion window.", "拥塞窗口。", 0, 1000))

    assert card.english.isHidden()
    assert card.english_button.text() == "显示英文"
    card.toggle_english()
    assert not card.english.isHidden()
    assert card.english_button.text() == "隐藏英文"

    card.close()
    card.deleteLater()
    app.processEvents()


def test_paragraph_card_reading_modes_change_order_and_emphasis() -> None:
    app = QApplication.instance() or QApplication([])
    card = ParagraphCard(Segment("English first.", "中文第一。", 0, 1000))
    assert card.body_layout.itemAt(1).widget() is card.chinese
    assert card.english.isHidden()

    card.set_reading_mode("bilingual")
    assert card.body_layout.itemAt(1).widget() is card.english
    assert not card.english.isHidden()
    assert "font-size:16px" in card.english.styleSheet()

    card.set_reading_mode("english")
    assert "font-size:18px" in card.english.styleSheet()
    assert "font-size:14px" in card.chinese.styleSheet()

    card.set_reading_mode("chinese")
    assert card.body_layout.itemAt(1).widget() is card.chinese
    assert card.english.isHidden()
    card.close()
    card.deleteLater()
    app.processEvents()


def test_paragraph_card_can_request_a_retroactive_marker_on_its_last_sentence() -> None:
    app = QApplication.instance() or QApplication([])
    first = Segment("First.", "第一句。", 0, 1000)
    last = Segment("Second.", "第二句。", 1100, 2000)
    card = ParagraphCard(first)
    card.add_segment(last)
    requests: list[tuple[str, str]] = []
    card.marker_requested.connect(lambda segment_id, marker: requests.append((segment_id, marker)))

    card.important_action.trigger()
    assert requests == [(last.id, "important")]
    card.set_marker(last.id, "important")
    assert card.important_action.text() == "取消段末重点"
    card.important_action.trigger()
    assert requests[-1] == (last.id, "")
    card.close()
    card.deleteLater()
    app.processEvents()


def test_recent_review_keeps_a_bounded_two_minute_chinese_window() -> None:
    app = QApplication.instance() or QApplication([])
    review = RecentReviewPane()
    old = Segment("Old.", "很早以前的内容。", 0, 1000)
    recent = Segment("Recent.", "老师刚刚讲到拥塞避免。", 125_000, 130_000)

    review.set_segments([old, recent])
    review.set_topic("TCP 拥塞控制")

    assert review.title.text() == "TCP 拥塞控制"
    assert "老师刚刚讲到" in review.body.text()
    assert "很早以前" not in review.body.text()
    assert "02:05–02:10" in review.meta.text()

    review.reset()
    assert review.title.text() == "最近几分钟"
    assert review.meta.text() == "等待稳定中文"
    review.close()
    review.deleteLater()
    app.processEvents()


def test_recent_review_does_not_present_stale_chinese_as_recent() -> None:
    app = QApplication.instance() or QApplication([])
    review = RecentReviewPane()
    review.set_segments([
        Segment("Old translated.", "旧译文。", 0, 1000),
        Segment("Current pending.", "", 180_000, 181_000),
    ])

    assert "旧译文" not in review.body.text()
    assert "最近两分钟暂无稳定中文" in review.body.text()
    assert "英文已保存" in review.body.text()
    review.close()
    review.deleteLater()
    app.processEvents()


def test_long_review_history_keeps_only_the_recent_tail() -> None:
    app = QApplication.instance() or QApplication([])
    segments = [
        Segment(f"English {index}.", f"中文 {index}。", index * 5000, index * 5000 + 1000)
        for index in range(1000)
    ]
    recent = RecentReviewPane()
    quick = QuickReviewPane()
    recent.set_segments(segments)
    quick.set_segments(segments)

    assert "中文 999" in recent.body.text()
    assert "中文 900" not in recent.body.text()
    assert "中文 999" in quick.body.text()
    assert "中文 900" not in quick.body.text()
    assert len(quick.selected) <= quick.MAX_SEGMENTS
    recent.close()
    recent.deleteLater()
    quick.close()
    quick.deleteLater()
    app.processEvents()


def test_quick_review_orders_a_late_subtitle_by_class_time() -> None:
    app = QApplication.instance() or QApplication([])
    quick = QuickReviewPane()
    history = [
        Segment("First.", "第一句。", 0, 1000),
        Segment("Third.", "第三句。", 8000, 9000),
    ]
    quick.set_segments(history)
    history.append(Segment("Second, arrived late.", "第二句。", 4000, 5000))
    quick.set_segments(history)

    assert quick.body.text() == "第一句。 第二句。 第三句。"
    quick.close()
    quick.deleteLater()
    app.processEvents()


def test_overlapping_quick_review_reads_chronologically_and_shows_full_range() -> None:
    app = QApplication.instance() or QApplication([])
    review = QuickReviewPane()
    review.set_segments([
        Segment("Long first.", "第一句。", 0, 10_000),
        Segment("Short second.", "第二句。", 4000, 5000),
    ])

    assert review.body.text() == "第一句。 第二句。"
    assert "00:00–00:10" in review.meta.text()
    review.close()
    review.deleteLater()
    app.processEvents()


def test_quick_review_switches_ranges_and_can_reveal_original() -> None:
    app = QApplication.instance() or QApplication([])
    review = QuickReviewPane()
    old = Segment("Older English.", "更早的内容。", 0, 1000)
    middle = Segment("Middle English.", "中间的内容。", 80_000, 81_000)
    recent = Segment("Recent English.", "刚刚讲的内容。", 130_000, 131_000)
    review.set_segments([old, middle, recent])
    review.set_topic("TCP 窗口")

    assert "刚刚讲的内容" in review.body.text()
    assert "中间的内容" in review.body.text()
    assert "更早的内容" not in review.body.text()
    review.range_choice.setCurrentIndex(0)
    assert "中间的内容" not in review.body.text()
    review.toggle_english()
    assert not review.english.isHidden()
    assert "Recent English" in review.english.text()

    jumps: list[int] = []
    review.jump_requested.connect(jumps.append)
    review._request_jump()
    assert jumps == [130_000]
    review.reset()
    assert review.jump_button.isEnabled() is False
    assert review.english.isHidden()
    review.close()
    review.deleteLater()
    app.processEvents()


def test_full_transcript_places_late_subtitle_without_replacing_current_line() -> None:
    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    window.show()
    live = window.live_page
    live.workspace.show()
    first = Segment("First.", "第一句。", 0, 1000)
    second = Segment("Second, arrived late.", "第二句。", 4000, 5000)
    third = Segment("Third, current.", "第三句。", 8000, 9000)
    live.add_segment(first)
    live.add_segment(third)
    live.jump_to_segment(first.id)
    assert not live.auto_follow

    live.add_segment(second)

    assert [group[0].id for group in live.paragraph_groups] == [
        first.id, second.id, third.id
    ]
    assert live.latest_segment_id == third.id
    assert live.partial.text() == "Third, current."
    live.update_translation(second.id, "迟到译文。")
    assert live.current_translation.text() == "第三句。"
    assert not live.auto_follow
    assert first.id in live.transcript_cards
    live.jump_to_segment(second.id)
    assert second.id in live.transcript_cards
    window.close()
    app.processEvents()


def test_topic_jump_stays_with_overlapping_long_paragraph(monkeypatch) -> None:
    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    live = window.live_page
    live.add_segment(Segment("Long first.", "第一句。", 0, 10_000))
    live.add_segment(Segment("Short overlap.", "第二句。", 1000, 2000))
    live.add_segment(Segment("Next topic.", "下一主题。", 13_000, 14_000))
    selected: list[int] = []
    monkeypatch.setattr(live, "_jump_to_group", selected.append)

    live.jump_to_topic(8000)

    assert selected == [0]
    window.close()
    app.processEvents()


def test_quick_review_shows_saved_english_while_translation_is_pending() -> None:
    app = QApplication.instance() or QApplication([])
    review = QuickReviewPane()
    review.set_segments([Segment("English is safe.", "", 0, 1000)])
    assert "[待翻译] English is safe." in review.body.text()
    closed: list[bool] = []
    review.close_requested.connect(lambda: closed.append(True))
    review.close_button.click()
    assert closed == [True]
    review.close()
    review.deleteLater()
    app.processEvents()
