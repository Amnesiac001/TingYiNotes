from types import SimpleNamespace

import pytest

from classnote.services import CompatibleTextProcessor, create_text_processor
from classnote.config import Settings


class FakeCompletions:
    def __init__(self) -> None:
        self.last_kwargs = None

    def create(self, **kwargs):
        self.last_kwargs = kwargs
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="  测试译文  "))]
        )


class FakeClient:
    def __init__(self) -> None:
        self.completions = FakeCompletions()
        self.chat = SimpleNamespace(completions=self.completions)


def test_compatible_processor_uses_chat_completions() -> None:
    client = FakeClient()
    processor = CompatibleTextProcessor(client, "demo-model", {"thinking": {"type": "disabled"}})
    output = processor.translate("Hello", "网络", {})
    assert output == "测试译文"
    assert client.completions.last_kwargs["model"] == "demo-model"
    assert client.completions.last_kwargs["extra_body"]["thinking"]["type"] == "disabled"


def test_provider_rejects_unknown_name() -> None:
    with pytest.raises(ValueError, match="TEXT_PROVIDER"):
        create_text_processor("unknown", "model", "key", "https://example.com")


def test_compatible_provider_requires_base_url() -> None:
    with pytest.raises(RuntimeError, match="TEXT_BASE_URL"):
        create_text_processor("compatible", "model", "key", None)


def test_deepseek_settings_use_current_default_model(monkeypatch) -> None:
    monkeypatch.setenv("TEXT_PROVIDER", "deepseek")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    monkeypatch.delenv("TEXT_MODEL", raising=False)
    monkeypatch.delenv("TEXT_BASE_URL", raising=False)
    settings = Settings.load()
    assert settings.text_provider == "deepseek"
    assert settings.text_model == "deepseek-v4-flash"
    assert settings.text_base_url == "https://api.deepseek.com"
