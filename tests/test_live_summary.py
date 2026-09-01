import json
import threading

import pytest

from classnote.live_summary import LiveSummaryCoordinator, parse_live_summary
from classnote.models import CourseResult, Segment


class SummaryProcessor:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def summarize_live(
        self,
        previous: str,
        title: str,
        subject: str,
        original: str,
        translation: str,
    ) -> str:
        self.calls.append((original, translation))
        return json.dumps(
            {
                "topic": "拥塞控制",
                "key_points": ["先慢启动", "再拥塞避免"],
                "terms": ["cwnd：拥塞窗口"],
                "questions": ["何时切换阶段？"],
            },
            ensure_ascii=False,
        )


def test_parse_live_summary_accepts_fence_deduplicates_and_caps() -> None:
    raw = "```json\n" + json.dumps(
        {
            "topic": "  TCP   拥塞控制  ",
            "key_points": ["同一点", "同一点", *[f"要点{i}" for i in range(10)]],
            "terms": ["cwnd：拥塞窗口"],
            "questions": ["为什么？"],
        },
        ensure_ascii=False,
    ) + "\n```"
    snapshot = parse_live_summary(raw)
    assert snapshot.topic == "TCP 拥塞控制"
    assert len(snapshot.key_points) == 6
    assert snapshot.key_points.count("同一点") == 1


def test_parse_live_summary_rejects_non_json() -> None:
    with pytest.raises(RuntimeError, match="JSON"):
        parse_live_summary("这是普通文字")


def test_coordinator_sorts_context_even_when_translations_finish_out_of_order() -> None:
    first = Segment("First", "第一", 0, 1000)
    second = Segment("Second", "第二", 1000, 2000)
    result = CourseResult("课", "网络", "local:mic", [first, second], "")
    processor = SummaryProcessor()
    updated = threading.Event()
    events: list[tuple[str, object]] = []

    def receive(name: str, payload: object) -> None:
        events.append((name, payload))
        if name == "summary_update":
            updated.set()

    coordinator = LiveSummaryCoordinator(
        result,
        processor,  # type: ignore[arg-type]
        "课",
        "网络",
        receive,
        min_segments=2,
        min_interval=999,
    )
    coordinator.start()
    coordinator.submit(second)
    coordinator.submit(first)
    assert updated.wait(timeout=2)
    coordinator.close()

    assert processor.calls[0] == ("First\nSecond", "第一\n第二")
    assert any(name == "summary_update" for name, _ in events)
    states = [payload["state"] for name, payload in events if name == "summary_status"]
    assert "collecting" in states
    assert "working" in states
    assert "updated" in states
