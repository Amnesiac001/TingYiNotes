from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

from classnote.models import CourseResult
from classnote.services import CompatibleTextProcessor, OpenAITextProcessor
from classnote.storage import CourseRepository
from classnote.usage import (
    TextUsage, bind_course_usage, format_usage_summary, parse_text_usage, text_cost_usd,
)


def test_openai_and_deepseek_usage_fields_are_parsed_without_guessing() -> None:
    openai = SimpleNamespace(
        id="resp-1", created=1789286400,
        usage=SimpleNamespace(
            input_tokens=100, output_tokens=40,
            input_tokens_details=SimpleNamespace(cached_tokens=25),
        ),
    )
    value = parse_text_usage(openai, "openai", "gpt-5-mini", "organizing")
    assert (value.input_tokens, value.output_tokens, value.cached_input_tokens) == (100, 40, 25)
    assert value.exact
    assert text_cost_usd(value) == Decimal("0.000099375")

    deepseek = SimpleNamespace(
        id="chat-1", created=1789286400,
        usage=SimpleNamespace(
            prompt_tokens=200, completion_tokens=50, prompt_cache_hit_tokens=100,
        ),
    )
    value = parse_text_usage(deepseek, "deepseek", "deepseek-v4-flash", "translation")
    assert (value.input_tokens, value.output_tokens, value.cached_input_tokens) == (200, 50, 100)
    assert text_cost_usd(value) is not None
    assert parse_text_usage(SimpleNamespace(id="missing"), "deepseek", "deepseek-flash", "summary").exact is False
    bad_timestamp = SimpleNamespace(id="bad-time", created=10**30, usage=openai.usage)
    assert parse_text_usage(bad_timestamp, "openai", "gpt-5-mini", "organizing").exact


def test_deepseek_price_accounts_for_peak_cache_and_unknown_models() -> None:
    sunday = datetime(2026, 9, 13, 2, tzinfo=timezone.utc)
    monday_peak = datetime(2026, 9, 14, 2, tzinfo=timezone.utc)
    base = dict(
        request_id="r", provider="deepseek", model="deepseek-flash",
        phase="translation", input_tokens=200, output_tokens=50, cached_input_tokens=100,
    )
    offpeak = TextUsage(**base, used_at=sunday)
    peak = TextUsage(**base, used_at=monday_peak)
    assert text_cost_usd(offpeak) == Decimal("0.0000453")
    assert text_cost_usd(peak) == Decimal("0.0000906")
    assert text_cost_usd(TextUsage(**{**base, "model": "custom"}, used_at=sunday)) is None


def test_usage_persists_per_course_and_deduplicates_request_id(tmp_path: Path) -> None:
    path = tmp_path / "usage.db"
    repository = CourseRepository(path)
    course = CourseResult("课", "网络", "mic", [], "")
    repository.create_course(course)
    event = TextUsage(
        "req-1", "deepseek", "deepseek-flash", "translation", 200, 50, 100,
        datetime(2026, 9, 13, 2, tzinfo=timezone.utc),
    )
    assert repository.add_text_usage(course.id, event, text_cost_usd(event))
    assert not repository.add_text_usage(course.id, event, text_cost_usd(event))
    missing = TextUsage(
        "req-2", "deepseek", "deepseek-flash", "summary", None, None, None,
        event.used_at,
    )
    repository.add_text_usage(course.id, missing, None)
    reopened = CourseRepository(path)
    summary = reopened.get_text_usage_summary(course.id)
    assert summary["requests"] == 2
    assert summary["exact_requests"] == 1
    assert summary["unknown_requests"] == 1
    assert summary["input_tokens"] == 200
    assert summary["cached_input_tokens"] == 100
    assert summary["estimated_cost_usd"] == Decimal("0.0000453")
    assert summary["phases"]["translation"] == {
        "requests": 1, "input_tokens": 200, "output_tokens": 50,
    }
    assert "至少" in format_usage_summary(summary)
    assert "翻译 1 次 / 200+50 Token" in format_usage_summary(summary)


def test_deepseek_stream_records_usage_from_final_chunk(tmp_path: Path) -> None:
    class StreamClient:
        def __init__(self):
            self.chat = SimpleNamespace(completions=self)
            self.request = None

        def with_options(self, **kwargs):
            return self

        def create(self, **kwargs):
            self.request = kwargs
            return iter([
                SimpleNamespace(
                    id="stream-1", created=1789286400, usage=None,
                    choices=[SimpleNamespace(delta=SimpleNamespace(content="你好"))],
                ),
                SimpleNamespace(
                    id="stream-1", created=1789286400,
                    usage=SimpleNamespace(
                        prompt_tokens=60, completion_tokens=8, prompt_cache_hit_tokens=20,
                    ),
                    choices=[],
                ),
            ])

    repository = CourseRepository(tmp_path / "stream.db")
    course = CourseResult("课", "网络", "mic", [], "")
    repository.create_course(course)
    client = StreamClient()
    processor = CompatibleTextProcessor(client, "deepseek-flash")
    bind_course_usage(processor, repository, course.id, "deepseek")
    assert "".join(processor.translate_stream("Hello", "网络", {})) == "你好"
    assert client.request["stream_options"] == {"include_usage": True}
    assert repository.get_text_usage_summary(course.id)["input_tokens"] == 60


def test_stream_without_usage_is_marked_unknown(tmp_path: Path) -> None:
    class StreamClient:
        def __init__(self):
            self.chat = SimpleNamespace(completions=self)

        def with_options(self, **kwargs):
            return self

        def create(self, **kwargs):
            return iter([
                SimpleNamespace(
                    id="no-usage", created=1789286400, usage=None,
                    choices=[SimpleNamespace(delta=SimpleNamespace(content="翻译"))],
                ),
            ])

    repository = CourseRepository(tmp_path / "unknown.db")
    course = CourseResult("课", "网络", "mic", [], "")
    repository.create_course(course)
    processor = CompatibleTextProcessor(StreamClient(), "deepseek-flash")
    bind_course_usage(processor, repository, course.id, "deepseek")
    assert "".join(processor.translate_stream("Text", "网络", {})) == "翻译"
    summary = repository.get_text_usage_summary(course.id)
    assert summary["unknown_requests"] == 1
    assert summary["input_tokens"] == 0
    assert "已知至少" in format_usage_summary(summary)


def test_openai_response_records_organizing_phase(tmp_path: Path) -> None:
    class ResponseClient:
        def __init__(self):
            self.responses = self

        def with_options(self, **kwargs):
            return self

        def create(self, **kwargs):
            return SimpleNamespace(
                id="resp-organize", output_text="# 笔记", created=1789286400,
                usage=SimpleNamespace(input_tokens=50, output_tokens=10),
            )

    repository = CourseRepository(tmp_path / "openai.db")
    course = CourseResult("课", "网络", "mic", [], "")
    repository.create_course(course)
    processor = OpenAITextProcessor(ResponseClient(), "gpt-5-mini")
    bind_course_usage(processor, repository, course.id, "openai")
    assert processor.organize("课", "网络", "Original", "翻译") == "# 笔记"
    with repository.connect() as connection:
        row = connection.execute("SELECT phase FROM text_usage_events").fetchone()
    assert row["phase"] == "organizing"
