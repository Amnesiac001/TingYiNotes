from types import SimpleNamespace

import pytest

import classnote.text_preflight as preflight
from classnote.services import CompatibleTextProcessor, OpenAITextProcessor


class FakeClient:
    def __init__(self, response: object) -> None:
        self.response = response
        self.options: dict[str, object] = {}
        self.request: dict[str, object] = {}
        self.responses = SimpleNamespace(create=self.create)
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def with_options(self, **kwargs: object) -> "FakeClient":
        self.options = kwargs
        return self

    def create(self, **kwargs: object) -> object:
        self.request = kwargs
        return self.response


def test_openai_text_probe_is_short_private_and_has_no_retry(monkeypatch) -> None:
    client = FakeClient(SimpleNamespace(output_text="OK"))
    monkeypatch.setattr(
        preflight, "create_text_processor",
        lambda *_args: OpenAITextProcessor(client, "gpt-5-mini"),
    )
    result = preflight.probe_text_connection("openai", "gpt-5-mini", "sk-test", None)
    assert result.reply == "OK"
    assert result.latency_ms >= 0
    assert client.options == {"timeout": 12.0, "max_retries": 0}
    assert client.request["model"] == "gpt-5-mini"
    assert client.request["store"] is False
    assert client.request["max_output_tokens"] == 128


def test_compatible_text_probe_keeps_provider_options(monkeypatch) -> None:
    client = FakeClient(
        SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="OK"))])
    )
    extra = {"thinking": {"type": "disabled"}}
    monkeypatch.setattr(
        preflight, "create_text_processor",
        lambda *_args: CompatibleTextProcessor(client, "deepseek-v4-flash", extra),
    )
    result = preflight.probe_text_connection(
        "deepseek", "deepseek-v4-flash", "sk-test", "https://api.deepseek.com"
    )
    assert result.reply == "OK"
    assert client.options == {"timeout": 12.0, "max_retries": 0}
    assert client.request["max_tokens"] == 64
    assert client.request["stream"] is False
    assert client.request["extra_body"] == extra


def test_text_probe_rejects_empty_reply(monkeypatch) -> None:
    client = FakeClient(SimpleNamespace(output_text=""))
    monkeypatch.setattr(
        preflight, "create_text_processor",
        lambda *_args: OpenAITextProcessor(client, "gpt-5-mini"),
    )
    with pytest.raises(RuntimeError, match="没有返回文字"):
        preflight.probe_text_connection("openai", "gpt-5-mini", "sk-test", None)
