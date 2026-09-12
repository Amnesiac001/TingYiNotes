import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from classnote.models import Segment
from classnote.qt_gui import ParagraphCard, QuickReviewPane, RecentReviewPane


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
