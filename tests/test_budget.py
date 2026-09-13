from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

from classnote.live_translation import LiveTranslationCoordinator
from classnote.live import ChunkedLiveCourseSession
from classnote.local_live import LocalLiveCourseSession
from classnote.realtime_live import RealtimeLiveCourseSession
from classnote.models import CourseResult, Segment
from classnote.services import OpenAITextProcessor
from classnote.storage import CourseRepository
from classnote.usage import BudgetLimitReached, ClassBudget


def _summary(cost: str, unknown: int = 0, unpriced: int = 0) -> dict[str, object]:
    return {
        "estimated_cost_usd": Decimal(cost),
        "unknown_requests": unknown,
        "unpriced_requests": unpriced,
    }


def test_budget_pauses_summary_then_new_text_requests() -> None:
    events: list[tuple[str, object]] = []
    budget = ClassBudget(Decimal("0.10"), "deepseek", "deepseek-flash", lambda n, p: events.append((n, p)))
    assert budget.allow("summary") and budget.allow("translation")
    budget.update(_summary("0.08"))
    assert not budget.allow("summary") and budget.allow("translation")
    budget.update(_summary("0.10"))
    assert not budget.allow("summary") and not budget.allow("translation")
    budget.update(_summary("0.10", unknown=1))
    assert budget.state == "limit"
    assert len([event for event in events if event[0] == "budget_update"]) == 3


def test_budget_does_not_invent_unknown_prices_or_usage() -> None:
    unsupported = ClassBudget(Decimal("0.10"), "compatible", "local-model")
    assert unsupported.state == "unavailable"
    assert unsupported.allow("translation")
    budget = ClassBudget(Decimal("0.10"), "openai", "gpt-5-mini")
    budget.update(_summary("0.01", unknown=1))
    assert budget.state == "unavailable"
    assert budget.allow("summary")


def test_processor_does_not_send_request_after_budget_limit() -> None:
    class Client:
        def __init__(self) -> None:
            self.responses = self
            self.calls = 0

        def with_options(self, **kwargs):
            return self

        def create(self, **kwargs):
            self.calls += 1
            return SimpleNamespace(output_text="译文")

    client = Client()
    processor = OpenAITextProcessor(client, "gpt-5-mini")
    budget = ClassBudget(Decimal("0.01"), "openai", "gpt-5-mini")
    budget.update(_summary("0.01"))
    processor.budget_guard = budget.allow
    with pytest.raises(BudgetLimitReached):
        processor.translate("Hello", "通用", {})
    assert client.calls == 0


def test_budget_limited_translation_keeps_english_for_recovery(tmp_path: Path) -> None:
    class Processor:
        def translate(self, *args):
            raise BudgetLimitReached()

    repository = CourseRepository(tmp_path / "class.db")
    result = CourseResult("课", "通用", "mic", [], "")
    repository.create_course(result)
    events: list[tuple[str, object]] = []
    coordinator = LiveTranslationCoordinator(
        result, repository, Processor(), "通用", lambda n, p: events.append((n, p)),
        workers=1,
    )
    coordinator.start()
    segment = coordinator.submit("English stays", 0, 1000)
    assert coordinator.close_and_wait(timeout=5)
    rows = repository.get_course_segments(result.id)
    assert len(rows) == 1
    assert rows[0]["original_text"] == "English stays"
    assert rows[0]["translation_status"] == "retry"
    assert any(name == "translation_failed" for name, _ in events)


@pytest.mark.parametrize(
    "session_type",
    [LocalLiveCourseSession, RealtimeLiveCourseSession, ChunkedLiveCourseSession],
)
def test_budget_exhaustion_still_exports_english(
    tmp_path: Path, session_type: type,
) -> None:
    class Processor:
        def organize(self, *args):
            raise BudgetLimitReached()

    repository = CourseRepository(tmp_path / "class.db")
    segment = Segment("Saved English", "", 0, 1000)
    result = CourseResult("课", "通用", "mic", [segment], "")
    repository.create_course(result)
    repository.add_segment(result.id, segment, 0, "retry")
    events: list[tuple[str, object]] = []
    session = session_type.__new__(session_type)
    session.finalized = False
    session.result = result
    session.repository = repository
    session.text_processor = Processor()
    session.settings = SimpleNamespace(export_dir=tmp_path / "exports")
    session.title = result.title
    session.subject = result.subject
    session.event = lambda n, p: events.append((n, p))
    session._finalize()
    assert repository.get_course(result.id)["status"] == "needs_attention"
    exported = list((tmp_path / "exports").glob("*.md"))
    assert len(exported) == 1
    assert "Saved English" in exported[0].read_text(encoding="utf-8")
    assert any(name == "finished" for name, _ in events)
