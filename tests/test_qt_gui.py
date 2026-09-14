import os
import sys
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QMessageBox
from PySide6.QtGui import QCloseEvent

from classnote.models import CourseResult, Segment
from classnote.config import Settings
import classnote.local_live as local_live
import classnote.qt_gui as qt_gui
from classnote.qt_gui import (
    Bridge, FilePage, LibraryPage, LivePage, MainWindow, SettingsPage, confirm_course_recovery,
    confirm_recovery_exit,
    friendly_error,
)
from classnote.storage import CourseRepository


def test_windows_model_preflight_runs_in_background_and_rejects_stale_result(monkeypatch) -> None:
    app = QApplication.instance() or QApplication([])
    monkeypatch.setenv("LIVE_MODE", "local")
    monkeypatch.setattr(qt_gui, "IS_MACOS", False)
    captured: dict[str, object] = {}

    class FakeThread:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        def start(self):
            captured["started"] = True

    monkeypatch.setattr(qt_gui.threading, "Thread", FakeThread)
    page = SettingsPage(Settings.load())
    page.speech_provider.setCurrentIndex(0)
    page.speech_model.setCurrentText("small.en")
    page.test_local_runtime()

    assert captured["started"] is True
    assert captured["target"] == page._prepare_windows_runtime
    assert captured["args"] == ("small.en",)
    assert not page.test_local_button.isEnabled()
    page.speech_model.setCurrentText("distil-large-v3")
    page.local_runtime_finished(True, "old ready", "old result")
    assert page.test_local_button.isEnabled()
    assert "重新预热" in page.speech_status.text()
    page.close()
    app.processEvents()


def test_windows_model_preflight_reports_loaded_model_and_microphone(monkeypatch, tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    monkeypatch.setenv("LIVE_MODE", "local")
    monkeypatch.setattr(qt_gui, "IS_MACOS", False)
    monkeypatch.setattr(local_live, "_prepare_nvidia_dlls", lambda: None)
    preheated: list[object] = []
    model = object()
    monkeypatch.setattr(local_live, "preload_local_model", lambda *args: (model, False))
    monkeypatch.setattr(local_live, "warmup_local_model", lambda value: preheated.append(value))
    monkeypatch.setitem(
        sys.modules,
        "ctranslate2",
        SimpleNamespace(
            get_cuda_device_count=lambda: 1,
            get_supported_compute_types=lambda _device: {"float16"},
        ),
    )
    monkeypatch.setattr(qt_gui, "list_input_devices", lambda: [SimpleNamespace(is_loopback=False)])
    page = SettingsPage(Settings.load())
    page.speech_provider.setCurrentIndex(0)
    page.speech_model.setCurrentText("small.en")
    page._local_probe_snapshot = (0, "small.en")
    monkeypatch.setattr(
        qt_gui.Settings,
        "load",
        classmethod(lambda _cls: SimpleNamespace(
            local_compute_type="float16", database_path=tmp_path / "classnote.db"
        )),
    )

    page._prepare_windows_runtime("small.en")

    assert "small.en" in page.speech_status.text()
    assert "1 个麦克风" in page.speech_status.text()
    assert preheated == [model]
    assert page.test_local_button.isEnabled()
    page.close()
    app.processEvents()


def test_friendly_error_explains_missing_key() -> None:
    message = friendly_error("Realtime classroom requires OPENAI_API_KEY")
    assert "设置" in message
    assert "OPENAI_API_KEY" in message


def test_friendly_error_distinguishes_invalid_key_from_missing_key() -> None:
    message = friendly_error("401 Invalid API key supplied")
    assert "无效" in message
    assert "尚未配置" not in message


def test_recovery_error_keeps_pending_count_and_actionable_cause() -> None:
    message = friendly_error(
        "连续 2 句补译失败，已暂停后续请求；仍有 5 句待补译。"
        "请检查网络、密钥和模型后重试。最近错误：401 Invalid API key"
    )
    assert "已暂停后续请求" in message
    assert "仍有 5 句待补译" in message
    assert "密钥无效" in message


def test_friendly_error_explains_rate_limit() -> None:
    message = friendly_error("Error code: 429 rate limit exceeded")
    assert "频繁" in message
    assert "额度" in message


def test_friendly_error_does_not_confuse_api_permission_with_microphone() -> None:
    message = friendly_error("403 PermissionDeniedError: model access denied")
    assert "文本服务" in message
    assert "麦克风" not in message
    assert "模型" in message


def test_friendly_error_distinguishes_spend_limit_from_rate_limit() -> None:
    message = friendly_error("429 project_spend_limit_exceeded")
    assert "支出上限" in message
    assert "稍后重试" not in message


def test_friendly_error_preserves_unknown_details() -> None:
    assert friendly_error("设备被其他程序占用") == "设备被其他程序占用"


def test_friendly_error_explains_audio_device_failures() -> None:
    assert "断开" in friendly_error("Audio device invalidated")
    assert "独占" in friendly_error("Audio device busy")
    assert "隐私和安全性" in friendly_error("Microphone permission denied")


def test_friendly_error_explains_unsupported_sample_rate() -> None:
    message = friendly_error("Invalid sample rate [PaErrorCode -9997]")
    assert "原生采样率" in message
    assert "刷新设备" in message


def test_incomplete_class_finish_offers_exact_course_record(monkeypatch, tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    repository = CourseRepository(tmp_path / "finish.db")
    segment = Segment("Saved English", "", 0, 1000)
    result = CourseResult("课", "网络", "mic", [segment], "")
    repository.create_course(result)
    repository.add_segment(result.id, segment, 0, "retry")
    repository.set_course_state(result.id, "needs_attention", "文本预算已用完")
    prompts: list[tuple[str, str]] = []
    monkeypatch.setattr(
        "classnote.qt_gui.ask_open_course",
        lambda _parent, title, message: prompts.append((title, message)) or True,
    )
    page = LivePage(Bridge(), lambda *_: None, lambda *_: None, lambda: None, repository)
    requested: list[str] = []
    page.course_open_requested.connect(requested.append)
    page.handle_event("finished", (result, tmp_path / "note.md"))
    app.processEvents()
    assert requested == [result.id]
    assert "待处理" in prompts[0][0]
    assert "待补译：1 句" in prompts[0][1]
    assert "文本预算已用完" in prompts[0][1]
    page.close()


def test_live_page_waits_for_audio_cleanup_before_enabling_next_class(monkeypatch, tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    repository = CourseRepository(tmp_path / "cleanup.db")
    result = CourseResult("课", "网络", "mic", [], "")
    repository.create_course(result)
    dialogs: list[str] = []
    monkeypatch.setattr(
        qt_gui, "show_message", lambda _parent, _icon, title, _body: dialogs.append(title)
    )
    page = LivePage(Bridge(), lambda *_: None, lambda *_: None, lambda: None, repository)
    page.session = object()
    page.cleanup_pending = True
    page.suppress_finish_dialog = True

    page.handle_event("finished", (result, tmp_path / "note.md"))

    assert page.session is None
    assert not page.start_button.isEnabled()
    assert not dialogs
    page.handle_event("session_ended", result.id)
    assert page.start_button.isEnabled()
    page.close()
    app.processEvents()


def test_asr_quality_shows_real_audio_backlog_before_inference_speed(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    repository = CourseRepository(tmp_path / "quality.db")
    page = LivePage(Bridge(), lambda *_: None, lambda *_: None, lambda: None, repository)

    page.update_asr_quality({"inference_ms": 100, "audio_ms": 1000, "queued_ms": 5400})
    assert "积压 5.4s" in page.asr_quality.text()
    page.update_asr_quality({"inference_ms": 100, "audio_ms": 1000, "queued_ms": 0})
    assert "流畅" in page.asr_quality.text()

    page.close()
    app.processEvents()


def test_backlog_status_without_inference_never_claims_zero_ms_speed(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    repository = CourseRepository(tmp_path / "backlog.db")
    page = LivePage(Bridge(), lambda *_: None, lambda *_: None, lambda: None, repository)

    page.handle_event("asr_backlog", {"queued_ms": 6000})
    assert "积压 6.0s" in page.asr_quality.text()
    assert "0ms" not in page.asr_quality.text()
    page.handle_event("asr_backlog", {"queued_ms": 4970, "backlog_level": 2})
    assert "识别 积压 5.0s" in page.asr_quality.text()
    page.handle_event("asr_backlog", {"queued_ms": 0})
    assert "等待英文" in page.asr_quality.text()

    page.close()
    app.processEvents()


def test_live_partial_does_not_show_previous_sentence_translation_as_current(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    page = LivePage(
        Bridge(), lambda *_: None, lambda *_: None, lambda: None,
        CourseRepository(tmp_path / "partial.db"),
    )
    previous = Segment("Previous sentence.", "", 0, 1000)
    page.handle_event("segment_original", previous)
    page.handle_event("partial", ("local-current", "Next sentence in progress"))
    page.handle_event("translation_delta", (previous.id, "上一句的部分译文"))

    assert page.partial.text() == "Next sentence in progress"
    assert "等待完整英文句子" in page.current_translation.text()
    assert "正在翻译" in page.transcript_cards[previous.id].chinese.text()
    assert previous.translated_text == ""
    page._render_paragraph_window(0)
    assert "上一句的部分译文" in page.transcript_cards[previous.id].chinese.text()
    assert "正在翻译" in page.transcript_cards[previous.id].chinese.text()
    previous.translated_text = "上一句完整译文"
    page.handle_event("segment_update", previous)
    assert "等待完整英文句子" in page.current_translation.text()

    current = Segment("Next sentence.", "", 1100, 2100)
    page.handle_event("segment_original", current)
    page.handle_event("translation_delta", (current.id, "下一句部分译文"))
    assert page.current_translation.text() == "下一句部分译文"
    page.close()
    app.processEvents()


def test_deferred_and_failed_translation_have_distinct_live_states(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    page = LivePage(
        Bridge(), lambda *_: None, lambda *_: None, lambda: None,
        CourseRepository(tmp_path / "translation-state.db"),
    )
    segment = Segment("Fast lecture.", "", 0, 1000)
    page.handle_event("segment_original", segment)
    page.handle_event("translation_failed", (segment.id, "翻译队列已满，空档自动补译"))
    assert segment.id in page.deferred_segment_ids
    assert segment.id not in page.failed_segment_ids
    assert "等空档自动补译" in page.transcript_cards[segment.id].chinese.text()
    assert page.transcript_cards[segment.id].retry_button.isHidden()
    page._render_paragraph_window(0)
    assert "等空档自动补译" in page.transcript_cards[segment.id].chinese.text()
    page.handle_event("metrics", {"translation_queue": 1, "translation_deferred": 1,
                                  "translation_active": 0, "last_translation_ms": 0})
    assert "等空档补 1 句" in page.translation_quality.text()

    page.handle_event("segment_retrying", segment.id)
    assert segment.id not in page.deferred_segment_ids
    page.handle_event("translation_failed", (segment.id, "网络请求失败"))
    page.handle_event("metrics", {"translation_queue": 0, "translation_deferred": 0,
                                  "translation_active": 0, "last_translation_ms": 100})
    assert segment.id in page.failed_segment_ids
    assert "待手动补 1 句" in page.translation_quality.text()
    assert not page.transcript_cards[segment.id].retry_button.isHidden()
    page.close()
    app.processEvents()


def test_close_request_waits_for_session_cleanup(monkeypatch, tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    monkeypatch.setenv("CLASSNOTE_DB", str(tmp_path / "close-live.db"))
    window = MainWindow()
    stopped: list[bool] = []
    window.live_page.session = object()
    window.live_page.cleanup_pending = True
    monkeypatch.setattr(qt_gui, "confirm_message", lambda *args: True)
    monkeypatch.setattr(window.live_page, "stop", lambda confirmed=False: stopped.append(confirmed))

    request = QCloseEvent()
    window.closeEvent(request)
    assert not request.isAccepted()
    assert stopped == [True]
    assert window.live_page.suppress_finish_dialog

    window.live_page.session = None  # The result arrived, but WAV cleanup has not.
    too_early = QCloseEvent()
    window.closeEvent(too_early)
    assert not too_early.isAccepted()
    window.handle_event("session_ended", "course")
    assert not window._close_after_session
    app.processEvents()
    window.close()


def test_course_recovery_confirmation_explains_extra_api_cost(monkeypatch, tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    repository = CourseRepository(tmp_path / "confirm.db")
    captured: dict[str, object] = {}

    def fake_exec(box: QMessageBox) -> QMessageBox.StandardButton:
        captured["text"] = box.text()
        captured["detail"] = box.informativeText()
        captured["default"] = box.defaultButton().text()
        return QMessageBox.StandardButton.No

    monkeypatch.setattr(QMessageBox, "exec", fake_exec)
    page = LibraryPage(repository)
    assert not confirm_course_recovery(
        page, "物理课", 3,
        repository.get_text_usage_summary("missing"),
    )
    assert "物理课" in captured["text"]
    assert "补译 3 句" in captured["detail"]
    assert "不受这节课的文本预算限制" in captured["detail"]
    assert "额外费用" in captured["detail"]
    assert captured["default"] == "取消"
    assert not confirm_course_recovery(
        page, "物理课", 0,
        {
            "requests": 1, "unknown_requests": 0, "unpriced_requests": 0,
            "input_tokens": 100, "output_tokens": 50,
            "cached_input_tokens": 0, "estimated_cost_usd": Decimal("0.001"),
            "phases": {},
        },
    )
    assert "重新整理笔记" in captured["detail"]
    assert "约 $0.001000" in captured["detail"]
    assert not confirm_course_recovery(
        page, "物理课", 0, repository.get_text_usage_summary("missing"),
        reuse_saved_draft=True,
    )
    assert "不会调用文本 API" in captured["detail"]
    assert "额外费用" not in captured["detail"]
    page.close()
    app.processEvents()


def test_cancelled_course_recovery_starts_no_worker(monkeypatch, tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    repository = CourseRepository(tmp_path / "cancel-recovery.db")
    segment = Segment("Saved English", "", 0, 1000)
    result = CourseResult("课", "物理", "mic", [segment], "")
    repository.create_course(result)
    repository.add_segment(result.id, segment, 0, "retry")
    repository.set_course_state(result.id, "needs_attention", "文本预算已用完")
    page = LibraryPage(repository)
    calls: list[tuple[str, int]] = []
    monkeypatch.setattr(
        "classnote.qt_gui.confirm_course_recovery",
        lambda _parent, title, pending, _usage, **_kwargs:
            calls.append((title, pending)) or False,
    )
    monkeypatch.setattr(
        "classnote.qt_gui.threading.Thread",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("worker started")),
    )
    page.recover_selected()
    assert calls == [("课", 1)]
    assert not page._recovery_running
    assert page.recover_button.isEnabled()
    assert repository.get_course(result.id)["status"] == "needs_attention"
    page.close()
    app.processEvents()


def test_library_labels_valid_draft_as_export_only(monkeypatch, tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    repository = CourseRepository(tmp_path / "export-only.db")
    course = CourseResult("课堂", "网络", "mic", [], "")
    repository.create_course(course)
    repository.add_segment(course.id, Segment("English", "中文", 0, 1000), 0, "completed")
    repository.save_notes_draft(course.id, "# 已保存草稿")
    repository.set_course_state(course.id, "needs_attention", "导出失败")

    page = LibraryPage(repository)
    assert page.recover_button.isEnabled()
    assert page.recover_button.text() == "重新导出"
    assert "不调用文本 API" in page.recover_button.toolTip()
    messages: list[tuple[str, str]] = []
    monkeypatch.setattr(
        "classnote.qt_gui.show_message",
        lambda _parent, _icon, title, body: messages.append((title, body)),
    )
    page._recovery_running = True
    page.handle_recovery_event("finished", (course, tmp_path / "note.md", 0, True))
    assert messages[0][0] == "笔记已重新导出"
    assert "保存的草稿" in messages[0][1]
    page.close()
    app.processEvents()


def test_closing_during_recovery_requires_explicit_exit(monkeypatch, tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    monkeypatch.setenv("CLASSNOTE_DB", str(tmp_path / "close-recovery.db"))
    window = MainWindow()
    window.library_page._recovery_running = True
    answers = iter([False, True])
    monkeypatch.setattr("classnote.qt_gui.confirm_recovery_exit", lambda _parent: next(answers))

    keep_open = QCloseEvent()
    window.closeEvent(keep_open)
    assert not keep_open.isAccepted()

    exit_now = QCloseEvent()
    window.closeEvent(exit_now)
    assert exit_now.isAccepted()
    window.library_page._recovery_running = False
    window.close()
    app.processEvents()


def test_recovery_exit_confirmation_defaults_to_wait(monkeypatch, tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    repository = CourseRepository(tmp_path / "exit-confirm.db")
    page = LibraryPage(repository)
    captured: dict[str, str] = {}

    def fake_exec(box: QMessageBox) -> QMessageBox.StandardButton:
        captured["detail"] = box.informativeText()
        captured["default"] = box.defaultButton().text()
        return QMessageBox.StandardButton.No

    monkeypatch.setattr(QMessageBox, "exec", fake_exec)
    assert not confirm_recovery_exit(page)
    assert "下次打开软件" in captured["detail"]
    assert "再次请求" in captured["detail"]
    assert captured["default"] == "继续等待"
    page.close()
    app.processEvents()


def test_local_speech_openai_text_key_is_visible_and_saved(monkeypatch, tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    for key in (
        "OPENAI_API_KEY", "OPENAI_TEXT_API_KEY", "DEEPSEEK_API_KEY",
        "COMPATIBLE_API_KEY", "TEXT_API_KEY", "OPENAI_TEXT_MODEL",
        "DEEPSEEK_TEXT_MODEL", "COMPATIBLE_TEXT_MODEL",
    ):
        monkeypatch.setenv(key, "")
    monkeypatch.setenv("TEXT_PROVIDER", "openai")
    monkeypatch.setenv("LIVE_MODE", "local")
    page = SettingsPage(Settings.load())
    page.output_dir.setText(str(tmp_path / "notes"))
    assert not page.text_key.isHidden()
    assert page.speech_key.isHidden()
    page.text_key.setText("text-openai-test")

    captured: dict[str, str] = {}

    def fake_save(values: dict[str, str]) -> Path:
        captured.update(values)
        for key, value in values.items():
            monkeypatch.setenv(key, value)
        return tmp_path / ".env"

    monkeypatch.setattr("classnote.qt_gui.save_env_settings", fake_save)
    page.save()
    assert captured["OPENAI_TEXT_API_KEY"] == "text-openai-test"
    assert captured["OPENAI_API_KEY"] == ""
    assert Settings.load().text_api_key == "text-openai-test"
    page.close()
    app.processEvents()


def test_switching_text_provider_keeps_keys_separate(monkeypatch, tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    for key in (
        "OPENAI_API_KEY", "OPENAI_TEXT_API_KEY", "DEEPSEEK_API_KEY",
        "COMPATIBLE_API_KEY", "TEXT_API_KEY", "OPENAI_TEXT_MODEL",
        "DEEPSEEK_TEXT_MODEL", "COMPATIBLE_TEXT_MODEL",
    ):
        monkeypatch.setenv(key, "")
    monkeypatch.setenv("TEXT_PROVIDER", "openai")
    monkeypatch.setenv("LIVE_MODE", "local")
    page = SettingsPage(Settings.load())
    page.output_dir.setText(str(tmp_path / "notes"))
    page.text_key.setText("text-openai-test")
    page.text_model.setCurrentText("custom-openai-model")
    page.provider.setCurrentText("DeepSeek")
    assert page.text_key.text() == ""
    page.text_key.setText("text-deepseek-test")
    page.text_model.setCurrentText("custom-deepseek-model")
    page.provider.setCurrentText("自定义兼容服务")
    assert page.text_key.text() == ""
    page.text_key.setText("compatible-test")
    page.text_model.setCurrentText("custom-compatible-model")
    page.base_url.setText("http://localhost:11434/v1")
    page.provider.setCurrentText("OpenAI")
    assert page.text_key.text() == "text-openai-test"
    assert page.text_model.currentText() == "custom-openai-model"
    page.provider.setCurrentText("DeepSeek")
    assert page.text_model.currentText() == "custom-deepseek-model"
    page.provider.setCurrentText("自定义兼容服务")
    assert page.text_key.text() == "compatible-test"
    assert page.text_model.currentText() == "custom-compatible-model"
    assert page.base_url.text() == "http://localhost:11434/v1"

    captured: dict[str, str] = {}

    def fake_save(values: dict[str, str]) -> Path:
        captured.update(values)
        for key, value in values.items():
            monkeypatch.setenv(key, value)
        return tmp_path / ".env"

    monkeypatch.setattr("classnote.qt_gui.save_env_settings", fake_save)
    page.save()
    assert captured["OPENAI_TEXT_API_KEY"] == "text-openai-test"
    assert captured["DEEPSEEK_API_KEY"] == "text-deepseek-test"
    assert captured["COMPATIBLE_API_KEY"] == "compatible-test"
    assert captured["OPENAI_TEXT_MODEL"] == "custom-openai-model"
    assert captured["DEEPSEEK_TEXT_MODEL"] == "custom-deepseek-model"
    assert captured["COMPATIBLE_TEXT_MODEL"] == "custom-compatible-model"
    assert captured["COMPATIBLE_BASE_URL"] == "http://localhost:11434/v1"
    assert Settings.load().text_api_key == "compatible-test"
    assert Settings.load().text_base_url == "http://localhost:11434/v1"
    reopened = SettingsPage(Settings.load())
    reopened.provider.setCurrentText("DeepSeek")
    assert reopened.text_key.text() == "text-deepseek-test"
    assert reopened.text_model.currentText() == "custom-deepseek-model"
    reopened.close()
    page.close()
    app.processEvents()


def test_cloud_speech_and_openai_text_can_share_one_key(monkeypatch, tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    for key in ("OPENAI_API_KEY", "OPENAI_TEXT_API_KEY", "TEXT_API_KEY"):
        monkeypatch.setenv(key, "")
    monkeypatch.setenv("TEXT_PROVIDER", "openai")
    monkeypatch.setenv("LIVE_MODE", "local")
    page = SettingsPage(Settings.load())
    page.output_dir.setText(str(tmp_path / "notes"))
    page.speech_provider.setCurrentIndex(1)
    page.speech_key.setText("shared-openai-key")
    page.text_key.clear()

    def fake_save(values: dict[str, str]) -> Path:
        for key, value in values.items():
            monkeypatch.setenv(key, value)
        return tmp_path / ".env"

    monkeypatch.setattr("classnote.qt_gui.save_env_settings", fake_save)
    page.save()
    settings = Settings.load()
    assert settings.api_key == "shared-openai-key"
    assert settings.text_api_key == "shared-openai-key"
    page.close()
    app.processEvents()


def test_text_connection_check_uses_unsaved_deepseek_fields(monkeypatch) -> None:
    app = QApplication.instance() or QApplication([])
    monkeypatch.setenv("TEXT_PROVIDER", "deepseek")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "")
    page = SettingsPage(Settings.load())
    page.text_key.setText("sk-unsaved-test")
    page.text_model.setCurrentText("custom-test-model")
    calls: list[tuple[str, str, str, str | None]] = []

    def fake_probe(provider: str, model: str, key: str, base_url: str | None):
        from classnote.text_preflight import TextProbeResult

        calls.append((provider, model, key, base_url))
        return TextProbeResult(42, "OK")

    class ImmediateThread:
        def __init__(self, *, target, args, **_kwargs) -> None:
            self.target = target
            self.args = args

        def start(self) -> None:
            self.target(*self.args)

    monkeypatch.setattr("classnote.text_preflight.probe_text_connection", fake_probe)
    monkeypatch.setattr("classnote.qt_gui.threading.Thread", ImmediateThread)
    page.test_text_connection()
    app.processEvents()
    assert calls == [
        ("deepseek", "custom-test-model", "sk-unsaved-test", "https://api.deepseek.com")
    ]
    assert "连接成功" in page.text_connection_status.text()
    assert page.test_text_button.isEnabled()
    page.close()


def test_text_connection_check_requires_key_and_rejects_stale_result(monkeypatch) -> None:
    app = QApplication.instance() or QApplication([])
    monkeypatch.setenv("TEXT_PROVIDER", "deepseek")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "")
    page = SettingsPage(Settings.load())
    page.text_key.clear()
    page.test_text_connection()
    assert page.test_text_button.isEnabled()

    page.text_key.setText("sk-test")
    pending: list[tuple[object, tuple[object, ...]]] = []

    class DeferredThread:
        def __init__(self, *, target, args, **_kwargs) -> None:
            self.target = target
            self.args = args

        def start(self) -> None:
            pending.append((self.target, self.args))

    from classnote.text_preflight import TextProbeResult

    monkeypatch.setattr("classnote.qt_gui.threading.Thread", DeferredThread)
    monkeypatch.setattr(
        "classnote.text_preflight.probe_text_connection",
        lambda *_args: TextProbeResult(9, "OK"),
    )
    page.test_text_connection()
    assert not page.test_text_button.isEnabled()
    page.text_model.setCurrentText("different-model")
    target, args = pending.pop()
    target(*args)
    app.processEvents()
    assert "配置已修改" in page.text_connection_status.text()
    assert page.test_text_button.isEnabled()
    page.close()


def test_settings_refuses_unwritable_output_before_saving(monkeypatch, tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    monkeypatch.setenv("TEXT_PROVIDER", "deepseek")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    monkeypatch.setenv("LIVE_MODE", "local")
    page = SettingsPage(Settings.load())
    page.output_dir.setText(str(tmp_path / "unwritable"))
    monkeypatch.setattr(
        "classnote.qt_gui.verify_output_directory",
        lambda _path: (_ for _ in ()).throw(OSError("笔记输出位置不可写")),
    )
    monkeypatch.setattr(
        "classnote.qt_gui.save_env_settings",
        lambda _values: (_ for _ in ()).throw(AssertionError("settings saved")),
    )
    page.save()
    assert "笔记输出位置不可写" in page.notice.text.text()
    page.close()
    app.processEvents()


def test_live_and_file_processing_check_output_before_start(monkeypatch, tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    monkeypatch.setenv("CLASSNOTE_DB", str(tmp_path / "output-check.db"))
    monkeypatch.setenv("TEXT_PROVIDER", "deepseek")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    monkeypatch.setenv("LIVE_MODE", "local")
    window = MainWindow()
    live = window.live_page
    live.devices = [SimpleNamespace(is_loopback=False)]
    live.device_combo.clear()
    live.device_combo.addItem("测试麦克风")
    monkeypatch.setattr(
        "classnote.qt_gui.verify_output_directory",
        lambda _path: (_ for _ in ()).throw(OSError("笔记输出位置不可写")),
    )
    live.start()
    assert live.session is None
    assert "笔记输出位置不可写" in live.notice.text.text()

    file_page = FilePage(Bridge(), window.repository)
    messages: list[str] = []
    monkeypatch.setattr(
        "classnote.qt_gui.show_message",
        lambda _parent, _icon, _title, body: messages.append(body),
    )
    file_page.start(demo=True)
    assert messages == ["笔记输出位置不可写"]
    assert file_page.start_button.isEnabled()
    file_page.close()
    window.close()
    app.processEvents()
