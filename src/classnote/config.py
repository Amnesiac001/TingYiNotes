from __future__ import annotations

import os
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path

from dotenv import load_dotenv, set_key


load_dotenv()


def save_env_settings(values: dict[str, str], path: Path | None = None) -> Path:
    """Persist GUI settings without making users edit dotenv syntax manually."""
    env_path = (path or Path(".env")).resolve()
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
        try:
            budget = Decimal(os.getenv("CLASSNOTE_CLASS_BUDGET_USD", "0").strip() or "0")
            if not budget.is_finite() or budget < 0:
                budget = Decimal("0")
        except InvalidOperation:
            budget = Decimal("0")
        provider = os.getenv("TEXT_PROVIDER", "openai").strip().lower()
        openai_key = os.getenv("OPENAI_API_KEY") or None
        if provider == "deepseek":
            text_key = os.getenv("DEEPSEEK_API_KEY") or os.getenv("TEXT_API_KEY") or None
            text_base_url = os.getenv("TEXT_BASE_URL", "https://api.deepseek.com")
            default_text_model = "deepseek-v4-flash"
        elif provider == "compatible":
            text_key = os.getenv("TEXT_API_KEY") or None
            text_base_url = os.getenv("TEXT_BASE_URL") or None
            default_text_model = ""
        else:
            text_key = os.getenv("TEXT_API_KEY") or openai_key
            text_base_url = os.getenv("TEXT_BASE_URL") or None
            default_text_model = "gpt-5-mini"
        return cls(
            api_key=openai_key,
            transcription_model=os.getenv("TRANSCRIPTION_MODEL", "gpt-transcribe"),
            text_model=os.getenv("TEXT_MODEL") or default_text_model,
            database_path=Path(os.getenv("CLASSNOTE_DB", "data/classnote.db")),
            export_dir=Path(os.getenv("CLASSNOTE_EXPORT_DIR", "exports")),
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
