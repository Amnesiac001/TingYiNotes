import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from classnote.models import Segment
from classnote.qt_gui import ParagraphCard


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
