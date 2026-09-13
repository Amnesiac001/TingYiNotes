import os
import subprocess
import sys
from pathlib import Path

import pytest
from dotenv import dotenv_values

import classnote.config as config
from classnote.config import Settings, save_env_settings, verify_output_directory


def test_output_directory_probe_creates_directory_and_cleans_its_files(tmp_path: Path) -> None:
    output = tmp_path / "中文 课堂笔记"
    assert verify_output_directory(output) == output.resolve()
    assert output.is_dir()
    assert list(output.iterdir()) == []


def test_output_directory_probe_rejects_file_and_failed_replace(monkeypatch, tmp_path: Path) -> None:
    file_path = tmp_path / "not-a-folder"
    file_path.write_text("existing", encoding="utf-8")
    with pytest.raises(ValueError, match="必须是文件夹"):
        verify_output_directory(file_path)
    assert file_path.read_text(encoding="utf-8") == "existing"

    output = tmp_path / "notes"

    def reject_replace(_source: object, _target: object) -> None:
        raise PermissionError("destination is locked")

    monkeypatch.setattr("classnote.config.os.replace", reject_replace)
    with pytest.raises(OSError, match="笔记输出位置不可写"):
        verify_output_directory(output)
    assert list(output.iterdir()) == []


def test_source_settings_use_project_env_not_launch_directory(monkeypatch, tmp_path: Path) -> None:
    project = tmp_path / "project"
    package = project / "src" / "classnote"
    package.mkdir(parents=True)
    (project / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
    (project / ".env.example").write_text("", encoding="utf-8")
    monkeypatch.setattr(config, "__file__", str(package / "config.py"))
    monkeypatch.delenv("CLASSNOTE_ENV_FILE", raising=False)
    monkeypatch.setenv("TEXT_PROVIDER", os.environ.get("TEXT_PROVIDER", "openai"))
    monkeypatch.chdir(tmp_path)

    assert config.settings_env_path() == project / ".env"
    saved = save_env_settings({"TEXT_PROVIDER": "deepseek"})
    assert saved == project / ".env"
    assert dotenv_values(saved)["TEXT_PROVIDER"] == "deepseek"
    assert not (tmp_path / ".env").exists()


def test_frozen_settings_use_stable_user_directory(monkeypatch, tmp_path: Path) -> None:
    user_data = tmp_path / "user-data"
    monkeypatch.setattr(config.sys, "frozen", True, raising=False)
    monkeypatch.setattr(config, "application_data_dir", lambda: user_data)
    monkeypatch.delenv("CLASSNOTE_ENV_FILE", raising=False)
    monkeypatch.setenv("TEXT_PROVIDER", os.environ.get("TEXT_PROVIDER", "openai"))
    monkeypatch.chdir(tmp_path)

    assert config.settings_env_path() == user_data / ".env"
    saved = save_env_settings({"TEXT_PROVIDER": "deepseek"})
    assert saved == user_data / ".env"
    assert dotenv_values(saved)["TEXT_PROVIDER"] == "deepseek"
    assert not (tmp_path / ".env").exists()


def test_macos_user_directory_preserves_legacy_classnote_data(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(config.sys, "platform", "darwin")
    monkeypatch.setattr(config.Path, "home", classmethod(lambda _cls: tmp_path))
    root = tmp_path / "Library" / "Application Support"
    legacy = root / "ClassNote"
    preferred = root / "听译记"
    legacy.mkdir(parents=True)

    assert config.application_data_dir() == legacy
    preferred.mkdir()
    assert config.application_data_dir() == preferred


def test_relative_data_paths_follow_config_directory_not_cwd(monkeypatch, tmp_path: Path) -> None:
    config_dir = tmp_path / "settings"
    monkeypatch.setenv("CLASSNOTE_ENV_FILE", str(config_dir / ".env"))
    monkeypatch.setenv("CLASSNOTE_DB", "data/classnote.db")
    monkeypatch.setenv("CLASSNOTE_EXPORT_DIR", "notes")
    monkeypatch.chdir(tmp_path)

    settings = Settings.load()
    assert settings.database_path == config_dir / "data" / "classnote.db"
    assert settings.export_dir == config_dir / "notes"


def test_new_process_loads_saved_env_from_explicit_path_outside_cwd(tmp_path: Path) -> None:
    config_dir = tmp_path / "settings"
    config_dir.mkdir()
    env_file = config_dir / ".env"
    env_file.write_text(
        "TEXT_PROVIDER=deepseek\nDEEPSEEK_API_KEY=sk-test\n"
        "CLASSNOTE_DB=data/classnote.db\nCLASSNOTE_EXPORT_DIR=notes\n",
        encoding="utf-8",
    )
    environment = os.environ.copy()
    for key in ("TEXT_PROVIDER", "DEEPSEEK_API_KEY", "CLASSNOTE_DB", "CLASSNOTE_EXPORT_DIR"):
        environment.pop(key, None)
    environment["CLASSNOTE_ENV_FILE"] = str(env_file)
    environment["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
    process = subprocess.run(
        [sys.executable, "-c", (
            "from classnote.config import Settings; s=Settings.load(); "
            "print(s.text_provider, bool(s.text_api_key), s.database_path, s.export_dir)"
        )],
        cwd=tmp_path, env=environment, text=True, capture_output=True, check=True,
    )
    assert process.stdout.strip() == (
        f"deepseek True {config_dir / 'data' / 'classnote.db'} {config_dir / 'notes'}"
    )


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
