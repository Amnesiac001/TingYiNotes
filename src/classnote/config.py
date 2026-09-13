from __future__ import annotations

import os
import sys
import tempfile
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path

from dotenv import load_dotenv, set_key


def application_data_dir() -> Path:
    """Stable per-user location for installed or frozen applications."""
    if sys.platform == "darwin":
        root = Path.home() / "Library" / "Application Support"
        preferred, legacy = root / "听译记", root / "ClassNote"
        return legacy if legacy.exists() and not preferred.exists() else preferred
    if os.name == "nt":
        root = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
        return root / "听译记"
    root = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return root / "tingyiji"


def settings_env_path() -> Path:
    """Use the project .env in a checkout, or user data in an installed app."""
    explicit = os.environ.get("CLASSNOTE_ENV_FILE")
    if explicit:
        return Path(explicit).expanduser().resolve()
    if not getattr(sys, "frozen", False):
        project_root = Path(__file__).resolve().parents[2]
        if (project_root / "pyproject.toml").is_file() and (project_root / ".env.example").is_file():
            return project_root / ".env"
    return application_data_dir() / ".env"


def _data_path(raw: str, config_dir: Path) -> Path:
    path = Path(raw).expanduser()
    return path if path.is_absolute() else (config_dir / path).resolve()


load_dotenv(settings_env_path(), override=False)


def verify_output_directory(path: Path) -> Path:
    """Check the same create/write/replace operations used by Markdown export."""
    target = path.expanduser().resolve()
    if target.exists() and not target.is_dir():
        raise ValueError("笔记输出位置必须是文件夹，不能是文件。")
    temporary_paths: list[Path] = []
    try:
        target.mkdir(parents=True, exist_ok=True)
        for _ in range(2):
            with tempfile.NamedTemporaryFile(
                mode="wb", dir=target, prefix=".tingyiji-write-check-",
                suffix=".tmp", delete=False,
            ) as temporary:
                temporary_paths.append(Path(temporary.name))
                temporary.write(b"write-check")
                temporary.flush()
                os.fsync(temporary.fileno())
        os.replace(temporary_paths[0], temporary_paths[1])
    except OSError as exc:
        raise OSError(f"笔记输出位置不可写：{target}。请换一个有写入权限的文件夹。原因：{exc}") from exc
    finally:
        for temporary_path in temporary_paths:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                pass
    return target


def _provider_setting(name: str, legacy: str | None = None) -> str | None:
    """An explicit empty provider slot must not fall back to another provider's old key."""
    return (os.environ.get(name) or None) if name in os.environ else legacy


def save_env_settings(values: dict[str, str], path: Path | None = None) -> Path:
    """Persist GUI settings without making users edit dotenv syntax manually."""
    env_path = (path or settings_env_path()).expanduser().resolve()
    env_path.parent.mkdir(parents=True, exist_ok=True)
    if not env_path.exists():
        env_path.touch()
    for key, value in values.items():
        # Auto quoting keeps Windows paths with spaces, Chinese, or '#' parseable.
        set_key(str(env_path), key, value, quote_mode="auto")
        os.environ[key] = value
    return env_path


@dataclass(frozen=True)
class Settings:
    api_key: str | None
    transcription_model: str
    text_model: str
    database_path: Path
    export_dir: Path
    live_chunk_seconds: int
    live_mode: str
    live_transcription_model: str
    live_transcription_delay: str
    local_transcription_model: str
    mac_transcription_model: str
    local_compute_type: str
    local_refresh_ms: int
    text_provider: str
    text_api_key: str | None
    text_base_url: str | None
    temporary_audio: bool
    class_budget_usd: Decimal | None = None

    @classmethod
    def load(cls) -> "Settings":
        env_path = settings_env_path()
        try:
            budget = Decimal(os.getenv("CLASSNOTE_CLASS_BUDGET_USD", "0").strip() or "0")
            if not budget.is_finite() or budget < 0:
                budget = Decimal("0")
        except InvalidOperation:
            budget = Decimal("0")
        provider = os.getenv("TEXT_PROVIDER", "openai").strip().lower()
        openai_key = os.getenv("OPENAI_API_KEY") or None
        if provider == "deepseek":
            text_key = _provider_setting("DEEPSEEK_API_KEY", os.getenv("TEXT_API_KEY") or None)
            text_base_url = os.getenv("TEXT_BASE_URL", "https://api.deepseek.com")
            default_text_model = "deepseek-v4-flash"
        elif provider == "compatible":
            text_key = _provider_setting("COMPATIBLE_API_KEY", os.getenv("TEXT_API_KEY") or None)
            text_base_url = _provider_setting("COMPATIBLE_BASE_URL", os.getenv("TEXT_BASE_URL") or None)
            default_text_model = ""
        else:
            text_key = _provider_setting(
                "OPENAI_TEXT_API_KEY", os.getenv("TEXT_API_KEY") or openai_key
            ) or openai_key
            text_base_url = os.getenv("TEXT_BASE_URL") or None
            default_text_model = "gpt-5-mini"
        model_key = {
            "openai": "OPENAI_TEXT_MODEL",
            "deepseek": "DEEPSEEK_TEXT_MODEL",
            "compatible": "COMPATIBLE_TEXT_MODEL",
        }.get(provider, "TEXT_MODEL")
        return cls(
            api_key=openai_key,
            transcription_model=os.getenv("TRANSCRIPTION_MODEL", "gpt-transcribe"),
            text_model=os.getenv(model_key) or os.getenv("TEXT_MODEL") or default_text_model,
            database_path=_data_path(os.getenv("CLASSNOTE_DB", "data/classnote.db"), env_path.parent),
            export_dir=_data_path(os.getenv("CLASSNOTE_EXPORT_DIR", "exports"), env_path.parent),
            live_chunk_seconds=max(5, int(os.getenv("LIVE_CHUNK_SECONDS", "10"))),
            live_mode=os.getenv("LIVE_MODE", "local").strip().lower(),
            live_transcription_model=os.getenv(
                "LIVE_TRANSCRIPTION_MODEL", "gpt-live-transcribe"
            ),
            live_transcription_delay=os.getenv("LIVE_TRANSCRIPTION_DELAY", "low"),
            local_transcription_model=os.getenv(
                "LOCAL_TRANSCRIPTION_MODEL", "distil-large-v3"
            ).strip(),
            mac_transcription_model=os.getenv(
                "MAC_TRANSCRIPTION_MODEL", "mlx-community/whisper-large-v3-turbo"
            ).strip(),
            local_compute_type=os.getenv("LOCAL_COMPUTE_TYPE", "float16").strip(),
            local_refresh_ms=max(500, int(os.getenv("LOCAL_REFRESH_MS", "800"))),
            text_provider=provider,
            text_api_key=text_key,
            text_base_url=text_base_url,
            temporary_audio=os.getenv("CLASSNOTE_TEMP_AUDIO", "false").strip().lower()
            in {"1", "true", "yes", "on"},
            class_budget_usd=budget if budget > 0 else None,
        )

    def ensure_directories(self) -> None:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.export_dir.mkdir(parents=True, exist_ok=True)
