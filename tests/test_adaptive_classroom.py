import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QEvent, Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from classnote.config import Settings
from classnote.models import Segment
from classnote.qt_gui import MainWindow, MaterialsPane, ParagraphCard, SettingsPage, TopicTimelinePane
from classnote.live_summary import LiveSummarySnapshot
from classnote.models import CourseResult
from classnote.storage import CourseRepository


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


def test_timeline_deduplicates_short_updates_and_emits_selected_time() -> None:
    application = app()
    timeline = TopicTimelinePane()
    selected: list[int] = []
    timeline.topic_selected.connect(selected.append)

    assert timeline.add_topic(0, "TCP 基础")
    assert not timeline.add_topic(30_000, "TCP 基础")
    assert not timeline.add_topic(60_000, "慢启动")
    assert timeline.add_topic(120_000, "慢启动")
    assert len(timeline.entries) == 2
    timeline._select_item(timeline.list.item(1))
    assert selected == [120_000]
    timeline.reset()
    assert timeline.list.count() == 0
    timeline.close()
    application.processEvents()


def test_live_timeline_switches_with_materials_and_jumps_without_autofollow() -> None:
    application = app()
    window = MainWindow()
    live = window.live_page
    live.session = object()
    live.set_materials_visible(False)
    assert not live.timeline.isHidden()
    assert live.materials.isHidden()

    live.materials.add_paths(["D:/course/slides.pptx"])
    assert not live.materials.isHidden()
    assert live.timeline.isHidden()
    live.toggle_materials()
    assert not live.timeline.isHidden()
    assert live.materials.isHidden()

    first = Segment("TCP basics.", "TCP 基础。", 0, 1000)
    later = Segment("Slow start.", "慢启动。", 130_000, 131_000)
    live.add_segment(first, pending=True)
    live.add_segment(later, pending=True)
    live.handle_event("summary_update", LiveSummarySnapshot(topic="慢启动"))
    assert live.timeline.entries == [(0, "慢启动")]
    live.jump_to_topic(0)
    assert not live.auto_follow
    assert not live.new_items_button.isHidden()
    assert "回到实时" in live.new_items_button.text()
    assert live.transcript_cards[first.id].review_highlight_timer.isActive()
    live.clear_session_content()
    assert live.timeline.entries == []
    live.session = None
    window.close()
    application.processEvents()


def test_live_summary_topic_is_saved_for_course_library(monkeypatch, tmp_path: Path) -> None:
    application = app()
    database = tmp_path / "timeline.db"
    repository = CourseRepository(database)
    result = CourseResult("课堂", "网络", "mic", [], "")
    repository.create_course(result)
    monkeypatch.setenv("CLASSNOTE_DB", str(database))
    window = MainWindow()
    live = window.live_page

    class Session:
        def __init__(self) -> None:
            self.result = result

    live.session = Session()
    live.add_segment(Segment("TCP basics.", "TCP 基础。", 0, 1000), pending=True)
    live.handle_event("summary_update", LiveSummarySnapshot(topic="TCP 基础"))
    live.handle_event("summary_update", LiveSummarySnapshot(topic="TCP 基础"))

    assert len(repository.get_course_topics(result.id)) == 1
    live.session = None
    window.close()
    application.processEvents()


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

    window.handle_event("error", "课堂整理失败，录音仍在收尾")
    application.processEvents()
    assert window.isVisible()

    window.handle_event("session_ended", "course")
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


def test_quick_review_tracks_new_subtitles_without_stopping_the_session() -> None:
    application = app()
    window = MainWindow()
    live = window.live_page
    session = object()
    live.session = session
    first = Segment("First.", "第一句。", 0, 1000)
    live.add_segment(first, pending=True)

    live.toggle_quick_review()
    assert not live.quick_review.isHidden()
    assert live.recent_review.isHidden()
    assert "第一句" in live.quick_review.body.text()
    later = Segment("Later.", "", 2000, 3000)
    live.add_segment(later, pending=True)
    assert "[待翻译] Later." in live.quick_review.body.text()
    live.update_translation(later.id, "第二句。")
    assert "第二句" in live.quick_review.body.text()
    assert live.session is session

    live.close_quick_review()
    assert live.quick_review.isHidden()
    assert live.auto_follow
    live.session = None
    window.close()
    application.processEvents()


def test_reading_choice_updates_existing_and_new_cards_and_remembers_choice(monkeypatch) -> None:
    application = app()
    monkeypatch.setenv("CLASSNOTE_READING_MODE", "chinese")
    saved: list[dict[str, str]] = []
    monkeypatch.setattr("classnote.qt_gui.save_env_settings", lambda values: saved.append(values))
    window = MainWindow()
    live = window.live_page
    first = Segment("First English.", "第一句中文。", 0, 1000)
    live.add_segment(first, pending=True)
    first_card = live.transcript_cards[first.id]
    assert first_card.english.isHidden()

    live.reading_choice.setCurrentIndex(2)
    assert live.reading_mode == "english"
    assert saved == [{"CLASSNOTE_READING_MODE": "english"}]
    assert not first_card.english.isHidden()
    assert "font-size:20px" in live.partial.styleSheet()

    second = Segment("Second English.", "第二句中文。", 2000, 3000)
    live.add_segment(second, pending=True)
    assert live.transcript_cards[second.id].reading_mode == "english"
    live.reading_choice.setCurrentIndex(0)
    assert first_card.english.isHidden()
    assert live.transcript_cards[second.id].english.isHidden()
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

    assert len(live.paragraph_cards) == 1
    assert live.unseen_segments == 1
    assert not live.new_items_button.isHidden()
    assert "1" in live.new_items_button.text()
    live._scroll_to_latest()
    assert live.auto_follow
    assert live.unseen_segments == 0
    assert live.new_items_button.isHidden()
    window.close()


def test_pending_auto_follow_does_not_cancel_a_review_jump() -> None:
    application = app()
    window = MainWindow()
    live = window.live_page
    old = Segment("Earlier sentence.", "之前的句子。", 0, 1000)
    later = Segment("New sentence.", "新句子。", 5000, 6000)

    live.add_segment(old)
    live.add_segment(later)
    assert live.follow_timer.isActive()
    live.jump_to_segment(old.id)
    assert not live.follow_timer.isActive()
    QTest.qWait(80)

    assert not live.auto_follow
    assert not live.new_items_button.isHidden()
    assert live.transcript_cards[old.id].review_highlight_timer.isActive()
    live._scroll_to_latest()
    assert live.auto_follow
    assert live.new_items_button.isHidden()
    window.close()
    application.processEvents()


def test_burst_of_subtitles_uses_one_pending_follow_timer() -> None:
    application = app()
    window = MainWindow()
    live = window.live_page
    live.add_segment(Segment("First.", "第一句。", 0, 1000))
    timer_id = live.follow_timer.timerId()
    assert timer_id > 0

    for index in range(1, 20):
        live.add_segment(
            Segment(f"Sentence {index}.", f"句子 {index}。", index * 3000, index * 3000 + 1000)
        )

    assert live.follow_timer.isActive()
    assert live.follow_timer.timerId() == timer_id
    assert len(live.live_segments) == 20
    live.reset()
    assert not live.follow_timer.isActive()
    window.close()
    application.processEvents()


def test_scrolling_away_from_latest_immediately_offers_a_return_button() -> None:
    application = app()
    window = MainWindow()
    live = window.live_page
    bar = live.scroll.verticalScrollBar()
    bar.setRange(0, 100)

    live._on_transcript_scroll(0)
    assert not live.auto_follow
    assert not live.new_items_button.isHidden()
    assert live.new_items_button.text() == "回到实时"

    live._on_transcript_scroll(100)
    assert live.auto_follow
    assert live.new_items_button.isHidden()
    window.close()
    application.processEvents()


def test_return_button_closes_quick_review_as_well_as_restoring_follow() -> None:
    application = app()
    window = MainWindow()
    live = window.live_page
    live.add_segment(Segment("Earlier.", "之前。", 0, 1000))
    live.toggle_quick_review()
    live.auto_follow = False
    live._show_return_to_live()

    live.new_items_button.click()

    assert live.quick_review.isHidden()
    assert live.auto_follow
    assert live.new_items_button.isHidden()
    window.close()
    application.processEvents()


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
    assert live._all_live_segments() == [segment]
    live.update_translation(segment.id, "后来完成的译文。")
    live.add_segment(Segment("Saved first", "", 0, 1000, id=segment.id), pending=True)
    assert live.transcript_cards[segment.id].chinese.text() == "后来完成的译文。"
    live.clear_session_content()
    assert live._all_live_segments() == []
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
    assert "这很重要。" in live.summary.markers.text()

    following = Segment("A supporting point.", "", 2000, 3000)
    live.add_segment(following, pending=True)
    assert "A supporting point." in live.summary.markers.text()
    live.update_translation(following.id, "补充说明。")
    assert "补充说明。" in live.summary.markers.text()

    live.latest_segment_id = segment.id
    live.toggle_latest_marker("important")
    assert calls[-1] == (segment.id, "")
    assert segment.marker == ""
    assert "这很重要。" not in live.summary.markers.text()
    live.session = None
    window.close()
    application.processEvents()


def test_old_paragraph_can_be_marked_and_reopened_from_summary() -> None:
    application = app()
    window = MainWindow()
    window.show()
    calls: list[tuple[str, str]] = []

    class Session:
        def set_segment_marker(self, segment_id: str, marker: str) -> None:
            calls.append((segment_id, marker))

    live = window.live_page
    live.session = Session()
    old = Segment("An earlier idea.", "之前的观点。", 0, 1000)
    latest = Segment("A new topic.", "新主题。", 5000, 6000)
    live.add_segment(old)
    live.add_segment(latest)
    old_card = live.transcript_cards[old.id]

    old_card.question_action.trigger()
    assert calls == [(old.id, "question")]
    assert old.marker == "question"
    assert "segment:" + old.id in live.summary.markers.text()
    assert live.question_button.text() == "? 疑问"

    live.summary._marker_link_activated("segment:" + old.id)
    assert not live.auto_follow
    assert live.transcript_cards[old.id] is old_card
    old_card.question_action.trigger()
    assert calls[-1] == (old.id, "")
    assert "segment:" + old.id not in live.summary.markers.text()
    live.session = None
    window.close()
    application.processEvents()


def test_long_classroom_keeps_only_a_window_of_cards_without_losing_old_content() -> None:
    application = app()
    window = MainWindow()
    live = window.live_page
    live.VISIBLE_PARAGRAPHS = 3
    live.PAGE_PARAGRAPHS = 2
    calls: list[tuple[str, str]] = []

    class Session:
        def set_segment_marker(self, segment_id: str, marker: str) -> None:
            calls.append((segment_id, marker))

    live.session = Session()
    segments = [
        Segment(f"Sentence {index}.", f"句子 {index}。", index * 5000, index * 5000 + 1000)
        for index in range(8)
    ]
    for segment in segments:
        live.add_segment(segment)
    application.processEvents()

    assert len(live.paragraph_cards) == 3
    assert live.visible_start == 5
    assert len(live._all_live_segments()) == 8
    assert segments[0].id not in live.transcript_cards
    assert "第 6–8 / 8 段" in live.history_head.text()
    assert not live.older_button.isHidden()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert len(window.findChildren(ParagraphCard)) == 3

    live.show_older_paragraphs()
    assert live.visible_start == 3
    assert not live.newer_button.isHidden()
    live.show_newer_paragraphs()
    assert live.visible_start == 5

    live.update_translation(segments[0].id, "迟到的中文。")
    live.handle_event("translation_failed", (segments[1].id, "timeout"))
    live.jump_to_topic(0)
    old_card = live.transcript_cards[segments[0].id]
    assert old_card.chinese.text() == "迟到的中文。"
    assert live.visible_start == 0
    assert not live.newer_button.isHidden()
    assert not live.transcript_cards[segments[1].id].retry_button.isHidden()
    old_card.important_action.trigger()
    assert calls == [(segments[0].id, "important")]

    live.toggle_latest_marker("question")
    assert calls[-1] == (segments[-1].id, "question")

    newcomer = Segment("New live sentence.", "新句。", 50_000, 51_000)
    live.add_segment(newcomer)
    assert newcomer.id not in live.transcript_cards
    assert live.unseen_segments == 1
    live._scroll_to_latest()
    assert newcomer.id in live.transcript_cards
    assert len(live.paragraph_cards) == 3
    assert live.auto_follow
    live.summary._marker_link_activated("segment:" + segments[0].id)
    assert segments[0].id in live.transcript_cards
    assert not live.auto_follow
    live._scroll_to_latest()
    next_segment = Segment("Still live.", "仍在继续。", 55_000, 56_000)
    live.add_segment(next_segment)
    assert next_segment.id in live.transcript_cards
    assert len(live.paragraph_cards) == 3
    live.clear_session_content()
    assert live.paragraph_groups == []
    assert live.transcript_cards == {}
    live.session = None
    window.close()
    application.processEvents()


def test_jump_refreshes_a_visible_paragraph_that_grew_during_review() -> None:
    application = app()
    window = MainWindow()
    live = window.live_page
    first = Segment("First.", "第一句。", 0, 1000)
    second = Segment("Second.", "第二句。", 1100, 2000)
    live.add_segment(first)
    live.auto_follow = False
    live.add_segment(second)
    assert second.id not in live.transcript_cards

    live.jump_to_segment(second.id)

    assert second.id in live.transcript_cards
    assert "第二句" in live.transcript_cards[second.id].chinese.text()
    assert live.transcript_cards[second.id].review_highlight_timer.isActive()
    window.close()
    application.processEvents()


def test_finishing_classroom_releases_live_paragraph_window() -> None:
    application = app()
    window = MainWindow()
    live = window.live_page
    live.add_segment(Segment("Saved.", "已保存。", 0, 1000))
    assert live.paragraph_groups

    live.reset()

    assert live.paragraph_groups == []
    assert live.transcript_cards == {}
    assert live.live_segments == []
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
