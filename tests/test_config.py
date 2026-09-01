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
