import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtWidgets import QApplication

from classnote.models import Segment
from classnote.qt_gui import MainWindow, ParagraphCard


def test_long_classroom_keeps_widget_count_bounded_in_qt_event_loop() -> None:
    application = QApplication.instance() or QApplication([])
    window = MainWindow()
    window.show()
    live = window.live_page
    live.workspace.show()
    live.VISIBLE_PARAGRAPHS = 6
    live.PAGE_PARAGRAPHS = 4
    segments: list[Segment] = []
    completed = False
    loop = QEventLoop()
    watchdog = QTimer()
    watchdog.setSingleShot(True)
    watchdog.timeout.connect(loop.quit)

    def feed() -> None:
        nonlocal completed
        for _ in range(12):
            number = len(segments)
            if number >= 480:
                break
            start_ms = number * 3000
            segment = Segment(
                f"English {number}.", f"中文 {number}。", start_ms, start_ms + 1000
            )
            segments.append(segment)
            live.add_segment(segment)
        if len(segments) == 240:
            live.jump_to_segment(segments[10].id)
        if len(segments) == 360:
            live._scroll_to_latest()
        if len(segments) >= 480:
            completed = True
            QTimer.singleShot(80, loop.quit)
        else:
            QTimer.singleShot(1, feed)

    QTimer.singleShot(0, feed)
    watchdog.start(10_000)
    loop.exec()
    watchdog.stop()

    assert completed
    assert len(live.live_segments) == 480
    assert len(live.paragraph_groups) == 480
    assert len(live.paragraph_cards) == 6
    assert len(window.findChildren(ParagraphCard)) <= 6
    live.jump_to_segment(segments[10].id)
    assert segments[10].id in live.transcript_cards
    live.reset()
    window.close()
    application.processEvents()
