import os
from pathlib import Path

from dotenv import dotenv_values

from classnote.config import Settings, save_env_settings


def test_save_env_settings_writes_and_updates_environment(tmp_path: Path) -> None:
    path = tmp_path / ".env"
    keys = ("TEXT_PROVIDER", "TEXT_MODEL")
    previous = {key: os.environ.get(key) for key in keys}
    try:
        save_env_settings({"TEXT_PROVIDER": "deepseek", "TEXT_MODEL": "deepseek-v4-flash"}, path)
        content = path.read_text(encoding="utf-8")
        parsed = dotenv_values(path)
        assert "TEXT_PROVIDER=deepseek" in content
        assert parsed["TEXT_MODEL"] == "deepseek-v4-flash"
        assert os.environ["TEXT_PROVIDER"] == "deepseek"
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def test_save_env_settings_quotes_windows_output_path_safely(tmp_path: Path) -> None:
    path = tmp_path / ".env"
    output = r"D:\课堂 笔记\Network #1"
    previous = os.environ.get("CLASSNOTE_EXPORT_DIR")
    try:
        save_env_settings({"CLASSNOTE_EXPORT_DIR": output}, path)
        parsed = dotenv_values(path)
        assert parsed["CLASSNOTE_EXPORT_DIR"] == output
        assert os.environ["CLASSNOTE_EXPORT_DIR"] == output
    finally:
        if previous is None:
            os.environ.pop("CLASSNOTE_EXPORT_DIR", None)
        else:
            os.environ["CLASSNOTE_EXPORT_DIR"] = previous


def test_settings_has_default_mlx_model(monkeypatch) -> None:
    monkeypatch.delenv("MAC_TRANSCRIPTION_MODEL", raising=False)
    settings = Settings.load()
    assert settings.mac_transcription_model == "mlx-community/whisper-large-v3-turbo"


def test_class_budget_configuration_is_optional_and_validated(monkeypatch) -> None:
    monkeypatch.setenv("CLASSNOTE_CLASS_BUDGET_USD", "0.10")
    assert Settings.load().class_budget_usd is not None
    assert str(Settings.load().class_budget_usd) == "0.10"
    monkeypatch.setenv("CLASSNOTE_CLASS_BUDGET_USD", "invalid")
    assert Settings.load().class_budget_usd is None


def test_provider_specific_keys_do_not_cross_or_override_explicit_empty(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "speech-key")
    monkeypatch.setenv("TEXT_API_KEY", "legacy-key")
    monkeypatch.delenv("OPENAI_TEXT_API_KEY", raising=False)
    monkeypatch.setenv("TEXT_PROVIDER", "openai")
    assert Settings.load().text_api_key == "legacy-key"
    monkeypatch.setenv("OPENAI_TEXT_API_KEY", "")
    assert Settings.load().text_api_key == "speech-key"
    monkeypatch.setenv("OPENAI_TEXT_API_KEY", "text-key")
    assert Settings.load().text_api_key == "text-key"

    monkeypatch.setenv("TEXT_PROVIDER", "deepseek")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "deepseek-key")
    monkeypatch.setenv("DEEPSEEK_TEXT_MODEL", "chosen-deepseek")
    assert Settings.load().text_api_key == "deepseek-key"
    assert Settings.load().text_model == "chosen-deepseek"
    monkeypatch.setenv("DEEPSEEK_API_KEY", "")
    assert Settings.load().text_api_key is None

    monkeypatch.setenv("TEXT_PROVIDER", "compatible")
    monkeypatch.setenv("COMPATIBLE_API_KEY", "compatible-key")
    monkeypatch.setenv("COMPATIBLE_TEXT_MODEL", "chosen-compatible")
    monkeypatch.setenv("COMPATIBLE_BASE_URL", "https://compatible.example/v1")
    assert Settings.load().text_api_key == "compatible-key"
    assert Settings.load().text_model == "chosen-compatible"
    assert Settings.load().text_base_url == "https://compatible.example/v1"
    monkeypatch.setenv("COMPATIBLE_API_KEY", "")
    assert Settings.load().text_api_key is None
    monkeypatch.setenv("COMPATIBLE_BASE_URL", "")
    monkeypatch.setenv("TEXT_BASE_URL", "https://old.example/v1")
    assert Settings.load().text_base_url is None
