from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from threading import Lock
from typing import Callable
from uuid import uuid4

from .storage import CourseRepository


# Text-only standard API prices in USD per 1M tokens, checked 2026-09-13.
# These are snapshots, not a guarantee of the provider's actual bill.
OPENAI_GPT5_MINI = (Decimal("0.25"), Decimal("0.025"), Decimal("2.00"))
DEEPSEEK_RATES = {
    "flash": {
        "peak": (Decimal("0.30"), Decimal("0.006"), Decimal("1.20")),
        "offpeak": (Decimal("0.15"), Decimal("0.003"), Decimal("0.60")),
    },
    "pro": {
        "peak": (Decimal("1.32"), Decimal("0.044"), Decimal("3.96")),
        "offpeak": (Decimal("0.66"), Decimal("0.022"), Decimal("1.98")),
    },
}
PRICE_SOURCES = {
    "openai": "https://developers.openai.com/api/docs/models/gpt-5-mini",
    "deepseek": "https://api-docs.deepseek.com/quick_start/pricing/",
}


def _value(source: object, name: str, default: object = None) -> object:
    if isinstance(source, dict):
        return source.get(name, default)
    return getattr(source, name, default)


def _nonnegative_int(value: object) -> int | None:
    try:
        number = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return number if number >= 0 else None


@dataclass(frozen=True)
class TextUsage:
    request_id: str
    provider: str
    model: str
    phase: str
    input_tokens: int | None
    output_tokens: int | None
    cached_input_tokens: int | None
    used_at: datetime

    @property
    def exact(self) -> bool:
        return self.input_tokens is not None and self.output_tokens is not None


def parse_text_usage(
    response: object, provider: str, model: str, phase: str,
    *, usage_override: object = None,
) -> TextUsage:
    usage = usage_override if usage_override is not None else _value(response, "usage")
    input_tokens = _nonnegative_int(
        _value(usage, "input_tokens") if provider == "openai"
        else _value(usage, "prompt_tokens")
    )
    output_tokens = _nonnegative_int(
        _value(usage, "output_tokens") if provider == "openai"
        else _value(usage, "completion_tokens")
    )
    if input_tokens is None or output_tokens is None:
        input_tokens = output_tokens = None
    if provider == "openai":
        cached = _nonnegative_int(_value(_value(usage, "input_tokens_details"), "cached_tokens"))
    else:
        cached = _nonnegative_int(_value(usage, "prompt_cache_hit_tokens"))
        if cached is None:
            cached = _nonnegative_int(_value(_value(usage, "prompt_tokens_details"), "cached_tokens"))
    if input_tokens is None or cached is None or cached > input_tokens:
        cached = None
    created = _nonnegative_int(_value(response, "created"))
    if created is None:
        created = _nonnegative_int(_value(response, "created_at"))
    try:
        used_at = datetime.fromtimestamp(created, timezone.utc) if created else datetime.now(timezone.utc)
    except (OverflowError, OSError, ValueError):
        used_at = datetime.now(timezone.utc)
    request_id = str(_value(response, "id") or uuid4())
    return TextUsage(request_id, provider, model, phase, input_tokens, output_tokens, cached, used_at)


def text_cost_usd(usage: TextUsage) -> Decimal | None:
    if not usage.exact:
        return None
    model = usage.model.casefold()
    if usage.provider == "openai" and (model == "gpt-5-mini" or model.startswith("gpt-5-mini-")):
        rates = OPENAI_GPT5_MINI
    elif usage.provider == "deepseek":
        family = (
            "flash" if model in {"deepseek-flash", "deepseek-v4-flash"}
            else "pro" if model == "deepseek-v4-pro" else ""
        )
        if not family:
            return None
        hour = usage.used_at.astimezone(timezone.utc).hour
        peak = usage.used_at.weekday() < 5 and (1 <= hour < 4 or 6 <= hour < 10)
        rates = DEEPSEEK_RATES[family]["peak" if peak else "offpeak"]
    else:
        return None
    input_tokens = usage.input_tokens or 0
    cached = usage.cached_input_tokens or 0
    output_tokens = usage.output_tokens or 0
    return (
        Decimal(input_tokens - cached) * rates[0]
        + Decimal(cached) * rates[1]
        + Decimal(output_tokens) * rates[2]
    ) / Decimal(1_000_000)


class BudgetLimitReached(RuntimeError):
    def __init__(self) -> None:
        super().__init__("本节课的文本预算已用完；英文继续保存，中文可课后补译。")


class ClassBudget:
    """Best-effort text-only budget; never guesses a missing provider price."""

    def __init__(
        self, limit_usd: Decimal, provider: str, model: str,
        event: Callable[[str, object], None] | None = None,
    ) -> None:
        self.limit_usd = limit_usd
        self.event = event
        self._lock = Lock()
        probe = TextUsage(
            "price-check", provider, model, "other", 0, 0, 0,
            datetime.now(timezone.utc),
        )
        self.state = "normal" if text_cost_usd(probe) is not None else "unavailable"
        if self.event is not None:
            self.event("budget_update", {"state": self.state, "limit_usd": limit_usd})
            if self.state == "unavailable":
                self.event("warning", "当前模型没有内置单价，无法执行课堂文本预算；请以服务商账单为准。")

    def allow(self, phase: str) -> bool:
        with self._lock:
            return self.state != "limit" and not (
                self.state == "saving" and phase == "summary"
            )

    def update(self, summary: dict[str, object]) -> None:
        with self._lock:
            if self.state in {"unavailable", "limit"}:
                return
            if int(summary["unknown_requests"]) or int(summary["unpriced_requests"]):
                state = "unavailable"
            else:
                spent = Decimal(str(summary["estimated_cost_usd"]))
                state = (
                    "limit" if spent >= self.limit_usd
                    else "saving" if spent >= self.limit_usd * Decimal("0.8")
                    else "normal"
                )
            if state == self.state:
                return
            self.state = state
        if self.event is not None:
            self.event("budget_update", {
                "state": state, "limit_usd": self.limit_usd,
                "spent_usd": summary["estimated_cost_usd"],
            })
            if state == "saving":
                self.event("warning", "文本预算已用约 80%；已暂停后台实时摘要，字幕和翻译继续。")
                self.event("summary_status", {"state": "paused_budget"})
            elif state == "limit":
                self.event("warning", "文本预算已达到上限；新翻译和摘要请求暂停，英文继续保存。")
                self.event("summary_status", {"state": "paused_budget"})
            elif state == "unavailable":
                self.event("warning", "文本服务未返回完整用量或单价，无法可靠执行课堂预算；请检查服务商账单。")


class CourseUsageRecorder:
    def __init__(
        self, repository: CourseRepository, course_id: str,
        event: Callable[[str, object], None] | None = None,
        budget: ClassBudget | None = None,
    ) -> None:
        self.repository = repository
        self.course_id = course_id
        self.event = event
        self.budget = budget
        self._warned = False

    def __call__(self, usage: TextUsage) -> None:
        try:
            inserted = self.repository.add_text_usage(self.course_id, usage, text_cost_usd(usage))
            if inserted:
                summary = self.repository.get_text_usage_summary(self.course_id)
                if self.budget is not None:
                    self.budget.update(summary)
                if self.event is not None:
                    self.event("usage_update", summary)
        except Exception as exc:
            if not self._warned and self.event is not None:
                self._warned = True
                self.event("warning", f"本次 API 用量未能保存：{exc}")


def bind_course_usage(
    processor: object, repository: CourseRepository, course_id: str,
    provider: str, event: Callable[[str, object], None] | None = None,
    budget_usd: Decimal | None = None,
) -> ClassBudget | None:
    budget = ClassBudget(budget_usd, provider, str(getattr(processor, "model", "")), event) if budget_usd else None
    if hasattr(processor, "usage_sink"):
        processor.usage_sink = CourseUsageRecorder(repository, course_id, event, budget)  # type: ignore[attr-defined]
        processor.usage_provider = provider  # type: ignore[attr-defined]
        processor.budget_guard = budget.allow if budget is not None else None  # type: ignore[attr-defined]
    return budget


def format_usage_summary(summary: dict[str, object]) -> str:
    requests = int(summary["requests"])
    if not requests:
        return "尚无文本 API 用量记录。"
    unknown = int(summary["unknown_requests"])
    unpriced = int(summary["unpriced_requests"])
    input_tokens = int(summary["input_tokens"])
    output_tokens = int(summary["output_tokens"])
    cached = int(summary["cached_input_tokens"])
    cost = Decimal(str(summary["estimated_cost_usd"]))
    prefix = "已知至少" if unknown else "共"
    lines = [
        f"{requests} 次文本请求 · {prefix} {input_tokens:,} 输入 / {output_tokens:,} 输出 Token",
        f"其中缓存输入 {cached:,} Token",
    ]
    if requests > unknown + unpriced:
        suffix = "（仅可计价部分）" if unknown or unpriced else ""
        lines.append(f"按官方单价快照估算：约 ${cost:.6f}{suffix}")
    else:
        lines.append("费用尚无法估算：模型单价或 API 用量缺失。")
    if unknown:
        lines.append(f"{unknown} 次请求未返回完整 Token 用量，未计入上述数字。")
    if unpriced:
        lines.append(f"{unpriced} 次请求使用未配置单价的模型，未计入费用。")
    phase_names = {
        "translation": "翻译", "summary": "实时摘要", "organizing": "课后整理",
    }
    phases = summary.get("phases", {})
    if isinstance(phases, dict) and phases:
        details = []
        for phase in ("translation", "summary", "organizing"):
            data = phases.get(phase)
            if isinstance(data, dict) and data.get("requests"):
                details.append(
                    f"{phase_names[phase]} {data['requests']} 次 / "
                    f"{data['input_tokens']:,}+{data['output_tokens']:,} Token"
                )
        if details:
            lines.append("用途：" + "；".join(details))
    lines.append("仅含文本服务；云端语音费用及平台最终账单不在此统计中。")
    return "\n".join(lines)
