import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QApplication

from classnote.config import Settings
from classnote.models import Segment
from classnote.qt_gui import MainWindow, MaterialsPane, SettingsPage


def app() -> QApplication:
    return QApplication.instance() or QApplication([])


def test_materials_pane_reports_content_and_can_release_space() -> None:
    app()
    pane = MaterialsPane()
    changes: list[tuple[bool, int]] = []
    pane.materials_changed.connect(lambda available, count: changes.append((available, count)))

    pane.add_paths(["D:/course/week-1.pdf", "D:/course/week-1.pdf"])
    assert pane.has_materials
    assert pane.list.count() == 1
    assert changes[-1] == (True, 1)

    pane.clear_materials()
    assert not pane.has_materials
    assert pane.list.count() == 0
    assert changes[-1] == (False, 0)


def test_escape_exits_fullscreen_without_leaving_classroom_focus() -> None:
    application = app()
    window = MainWindow()
    window.show()
    application.processEvents()

    window.set_immersive(True)
    application.processEvents()
    assert window.immersive_active
    assert window.sidebar.isHidden()
    assert window.live_page.title_label.isHidden()
    assert window.isFullScreen()

    escape = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Escape, Qt.KeyboardModifier.NoModifier)
    window.keyPressEvent(escape)
    application.processEvents()
    assert window.immersive_active
    assert not window.isFullScreen()
    assert window.sidebar.isHidden()

    window.set_immersive(False)
    application.processEvents()
    assert not window.immersive_active
    assert not window.sidebar.isHidden()
    assert not window.live_page.title_label.isHidden()
    window.close()


def test_live_workspace_only_shows_material_column_when_needed() -> None:
    application = app()
    window = MainWindow()
    window.show()
    application.processEvents()
    live = window.live_page

    assert live.materials.isHidden()
    live.materials.add_paths(["D:/course/slides.pptx"])
    application.processEvents()
    assert not live.materials.isHidden()
    assert live.material_toggle.text() == "隐藏课件"

    live.materials.clear_materials()
    application.processEvents()
    assert live.materials.isHidden()
    window.close()


def test_settings_output_directory_can_be_created_and_rejects_a_file(tmp_path: Path) -> None:
    app()
    page = SettingsPage(Settings.load())
    new_directory = tmp_path / "中文 课堂笔记"
    page.output_dir.setText(str(new_directory))
    assert page.resolved_output_directory(create=True) == new_directory.resolve()
    assert new_directory.is_dir()

    file_path = tmp_path / "not-a-directory.txt"
    file_path.write_text("file", encoding="utf-8")
    page.output_dir.setText(str(file_path))
    with pytest.raises(ValueError, match="必须是文件夹"):
        page.resolved_output_directory(create=True)


def test_window_waits_for_classroom_terminal_event_before_closing(monkeypatch) -> None:
    application = app()
    window = MainWindow()
    window.show()
    application.processEvents()
    calls = []

    class Session:
        def stop(self) -> None:
            calls.append("stop")

    monkeypatch.setattr("classnote.qt_gui.confirm_message", lambda *_: True)
    window.live_page.session = Session()
    window.close()
    application.processEvents()

    assert calls == ["stop"]
    assert window.isVisible()
    assert window._close_after_session

    window.handle_event("error", "课堂收尾完成")
    application.processEvents()
    assert not window.isVisible()


def test_live_translation_never_replaces_current_text_with_an_older_sentence() -> None:
    application = app()
    window = MainWindow()
    window.show()
    live = window.live_page
    older = Segment("Older sentence", "", 0, 1000)
    latest = Segment("Latest sentence", "", 1000, 2000)

    live.add_segment(older, pending=True)
    live.add_segment(latest, pending=True)
    live.update_translation(older.id, "较早句子的译文")
    assert live.current_translation.text() == "正在翻译……"
    live.update_translation(latest.id, "最新句子的译文")
    assert live.current_translation.text() == "最新句子的译文"
    assert "最新句子的译文" in live.recent_review.body.text()
    assert "较早句子的译文" in live.recent_review.body.text()
    window.close()
    application.processEvents()


def test_new_subtitles_do_not_steal_scroll_when_user_reads_older_content() -> None:
    application = app()
    window = MainWindow()
    window.show()
    live = window.live_page
    live.auto_follow = False

    live.add_segment(Segment("A new sentence", "新句子", 0, 1000))
    application.processEvents()

    assert live.unseen_segments == 1
    assert not live.new_items_button.isHidden()
    assert "1" in live.new_items_button.text()
    live._scroll_to_latest()
    assert live.auto_follow
    assert live.unseen_segments == 0
    assert live.new_items_button.isHidden()
    window.close()


def test_latest_translation_failure_is_visible_without_overwriting_older_state() -> None:
    application = app()
    window = MainWindow()
    latest = Segment("Latest", "", 1000, 2000)
    window.live_page.add_segment(latest, pending=True)

    window.live_page.handle_event("translation_failed", (latest.id, "timeout"))

    assert "英文已保存" in window.live_page.current_translation.text()
    window.close()
    application.processEvents()


def test_end_class_requires_a_second_click_and_can_expire_safely() -> None:
    application = app()
    window = MainWindow()
    calls: list[str] = []

    class Session:
        def stop(self) -> None:
            calls.append("stop")

    live = window.live_page
    live.session = Session()
    live.live_status.setText("正在实时记录")
    live.stop()
    assert calls == []
    assert live.stop_armed
    assert live.stop_button.text() == "再次点击结束"

    live.handle_event("status", "网络重连已恢复")
    assert "再次点击" in live.live_status.text()
    live._disarm_stop()
    assert live.live_status.text() == "网络重连已恢复"
    live.stop()
    live.stop()
    assert calls == ["stop"]
    assert not live.stop_armed

    live.session = None
    window.close()
    application.processEvents()


def test_live_workspace_shows_durable_english_save_count() -> None:
    application = app()
    window = MainWindow()
    live = window.live_page
    segment = Segment("Saved first", "", 0, 1000)

    live.add_segment(segment, pending=True)
    live.add_segment(segment, pending=True)

    assert live.saved_segment_count == 1
    assert live.save_quality.text() == "已保存 1 句"
    window.close()
    application.processEvents()


def test_summary_progress_is_visible_before_the_first_summary() -> None:
    application = app()
    window = MainWindow()
    summary = window.live_page.summary

    summary.set_status({"state": "collecting", "count": 3, "target": 6})
    assert summary.hint.text() == "积累中 3/6"
    summary.set_status({"state": "working"})
    assert summary.hint.text() == "正在整理…"
    summary.set_status({"state": "updated"})
    assert summary.hint.text() == "刚刚更新"
    window.close()
    application.processEvents()


def test_latest_sentence_marker_is_persisted_and_can_be_toggled() -> None:
    application = app()
    window = MainWindow()
    calls: list[tuple[str, str]] = []

    class Session:
        def set_segment_marker(self, segment_id: str, marker: str) -> None:
            calls.append((segment_id, marker))

    live = window.live_page
    live.session = Session()
    segment = Segment("This is important.", "这很重要。", 0, 1000)
    live.add_segment(segment)

    live.toggle_latest_marker("important")
    assert calls == [(segment.id, "important")]
    assert segment.marker == "important"
    assert "重点" in live.transcript_cards[segment.id].marker_badge.text()
    assert live.important_button.text() == "★ 已标重点"

    live.toggle_latest_marker("important")
    assert calls[-1] == (segment.id, "")
    assert segment.marker == ""
    live.session = None
    window.close()
    application.processEvents()


def test_failed_translation_can_request_an_in_class_retry() -> None:
    application = app()
    window = MainWindow()
    calls: list[str] = []

    class Session:
        def retry_translation(self, segment_id: str) -> None:
            calls.append(segment_id)

    live = window.live_page
    live.session = Session()
    segment = Segment("Retry me.", "", 0, 1000)
    live.add_segment(segment, pending=True)
    live.handle_event("translation_failed", (segment.id, "timeout"))
    card = live.transcript_cards[segment.id]
    assert not card.retry_button.isHidden()

    card.retry_button.click()
    assert calls == [segment.id]
    live.handle_event("segment_retrying", segment.id)
    assert card.retry_button.isHidden()
    assert "重新翻译" in live.current_translation.text()
    live.session = None
    window.close()
    application.processEvents()


def test_retry_button_recovers_when_retry_cannot_be_queued() -> None:
    application = app()
    window = MainWindow()

    class Session:
        def retry_translation(self, segment_id: str) -> None:
            raise RuntimeError("queue full")

    live = window.live_page
    live.session = Session()
    segment = Segment("Retry later.", "", 0, 1000)
    live.add_segment(segment, pending=True)
    live.handle_event("translation_failed", (segment.id, "timeout"))
    card = live.transcript_cards[segment.id]

    card.retry_button.click()

    assert not card.retry_button.isHidden()
    assert "英文仍已安全保存" in live.current_translation.text()
    live.session = None
    window.close()
    application.processEvents()


def test_local_startup_distinguishes_audio_capture_from_model_readiness() -> None:
    application = app()
    window = MainWindow()
    live = window.live_page

    live.handle_event("capture_started", {"buffer_capacity_ms": 300_000})
    assert "已开始采集" in live.audio_quality.text()
    assert "开场内容正在缓存" in live.live_status.text()

    live.handle_event("capture_buffer", {"buffered_ms": 5200, "capacity_ms": 300_000})
    assert "缓存 5s" in live.asr_quality.text()
    live.handle_event(
        "model_ready",
        {"model": "distil-large-v3", "load_seconds": 3.2, "buffered_ms": 5200},
    )
    assert "本地 GPU 已就绪" in live.asr_quality.text()
    assert "5.2s" in live.live_status.text()
    window.close()
    application.processEvents()
