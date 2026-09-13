import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from classnote.models import CourseResult, Segment
from classnote.config import Settings
from classnote.qt_gui import Bridge, LivePage, SettingsPage, friendly_error
from classnote.storage import CourseRepository


def test_friendly_error_explains_missing_key() -> None:
    message = friendly_error("Realtime classroom requires OPENAI_API_KEY")
    assert "设置" in message
    assert "OPENAI_API_KEY" in message


def test_friendly_error_distinguishes_invalid_key_from_missing_key() -> None:
    message = friendly_error("401 Invalid API key supplied")
    assert "无效" in message
    assert "尚未配置" not in message


def test_friendly_error_explains_rate_limit() -> None:
    message = friendly_error("Error code: 429 rate limit exceeded")
    assert "频繁" in message
    assert "额度" in message


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
