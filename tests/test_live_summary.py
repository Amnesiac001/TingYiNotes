import json
import threading

import pytest

from classnote.live_summary import LiveSummaryCoordinator, parse_live_summary
from classnote.models import CourseResult, Segment
from classnote.services import _live_summary_request


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


def test_incremental_summary_sends_only_new_segments_and_catches_late_translation() -> None:
    first = Segment("First", "第一", 1000, 2000)
    second = Segment("Second", "第二", 2000, 3000)
    result = CourseResult("课", "网络", "mic", [first, second], "")
    processor = SummaryProcessor()
    coordinator = LiveSummaryCoordinator(result, processor, "课", "网络", lambda *_: None)

    coordinator._summarize()
    assert processor.calls == [("First\nSecond", "第一\n第二")]
    late = Segment("Opening that arrived late", "迟到的开场", 0, 1000)
    result.segments.append(late)
    coordinator._summarize()
    assert processor.calls[1] == ("Opening that arrived late", "迟到的开场")
    coordinator._summarize()
    assert len(processor.calls) == 2


def test_late_only_batch_cannot_rewind_current_topic() -> None:
    class MovingTopicProcessor(SummaryProcessor):
        def summarize_live(self, previous, title, subject, original, translation):
            self.calls.append((original, translation))
            return json.dumps({"topic": "当前主题" if len(self.calls) == 1 else "旧主题"}, ensure_ascii=False)

    current = Segment("Current", "当前", 10000, 11000)
    result = CourseResult("课", "网络", "mic", [current], "")
    processor = MovingTopicProcessor()
    coordinator = LiveSummaryCoordinator(result, processor, "课", "网络", lambda *_: None)
    coordinator._summarize()
    result.segments.append(Segment("Late", "较早", 0, 1000))
    coordinator._summarize()
    assert coordinator._snapshot.topic == "当前主题"


def test_incremental_summary_batches_backlog_without_skipping_segments() -> None:
    segments = [Segment(f"English {i}", f"中文 {i}", i * 1000, (i + 1) * 1000) for i in range(5)]
    result = CourseResult("课", "网络", "mic", segments, "")
    processor = SummaryProcessor()
    coordinator = LiveSummaryCoordinator(
        result, processor, "课", "网络", lambda *_: None,
        max_context_segments=2,
    )
    coordinator._summarize()
    coordinator._summarize()
    coordinator._summarize()
    assert [call[0].splitlines() for call in processor.calls] == [
        ["English 0", "English 1"], ["English 2", "English 3"], ["English 4"]
    ]


def test_failed_summary_retries_same_new_segments_without_losing_previous_snapshot() -> None:
    class FlakyProcessor(SummaryProcessor):
        def summarize_live(self, previous, title, subject, original, translation):
            self.calls.append((original, translation))
            if len(self.calls) == 2:
                return "not JSON"
            return json.dumps({"topic": f"主题{len(self.calls)}"}, ensure_ascii=False)

    first = Segment("First", "第一")
    result = CourseResult("课", "网络", "mic", [first], "")
    processor = FlakyProcessor()
    coordinator = LiveSummaryCoordinator(result, processor, "课", "网络", lambda *_: None)
    coordinator._summarize()
    second = Segment("Second", "第二", 1000, 2000)
    result.segments.append(second)
    coordinator._summarize()
    assert coordinator._snapshot.topic == "主题1"
    coordinator._summarize()
    assert processor.calls[1:] == [("Second", "第二"), ("Second", "第二")]
    assert coordinator._snapshot.topic == "主题3"


def test_worker_retries_failed_batch_after_interval_without_new_speech() -> None:
    class FailOnceProcessor(SummaryProcessor):
        def summarize_live(self, previous, title, subject, original, translation):
            self.calls.append((original, translation))
            if len(self.calls) == 1:
                raise RuntimeError("temporary timeout")
            return json.dumps({"topic": "恢复的主题"}, ensure_ascii=False)

    segments = [Segment(f"Sentence {i}", f"句子 {i}", i * 1000, (i + 1) * 1000) for i in range(3)]
    result = CourseResult("课", "网络", "mic", segments, "")
    processor = FailOnceProcessor()
    updated = threading.Event()
    events: list[str] = []

    def receive(name, payload):
        events.append(name)
        if name == "summary_update":
            updated.set()

    coordinator = LiveSummaryCoordinator(
        result, processor, "课", "网络", receive,
        min_segments=3, min_interval=0,
    )
    coordinator.start()
    for segment in segments:
        coordinator.submit(segment)
    assert updated.wait(timeout=3)
    coordinator.close()
    assert len(processor.calls) == 2
    assert processor.calls[0] == processor.calls[1]
    assert events.count("warning") == 1


def test_summary_prompt_labels_new_input_instead_of_repeated_recent_history() -> None:
    instructions, payload = _live_summary_request("{}", "课", "网络", "New", "新")
    assert "本次新增" in instructions
    assert "本次新增英文字幕：\nNew" in payload


def test_budget_pause_keeps_existing_summary_without_new_request() -> None:
    segment = Segment("New English", "新中文", 0, 1000)
    result = CourseResult("课", "网络", "mic", [segment], "")
    processor = SummaryProcessor()
    processor.budget_guard = lambda phase: False
    coordinator = LiveSummaryCoordinator(result, processor, "课", "网络", lambda *_: None)
    coordinator.submit(segment)
    coordinator._summarize()
    assert coordinator.queue.empty()
    assert processor.calls == []
    assert coordinator._pending_count() == 1


def test_summary_waits_for_translation_and_catches_up_without_new_speech() -> None:
    segments = [Segment(f"English {i}", f"中文 {i}", i * 1000, (i + 1) * 1000) for i in range(3)]
    result = CourseResult("课", "网络", "mic", segments, "")
    processor = SummaryProcessor()
    busy = True
    paused = threading.Event()
    updated = threading.Event()
    states: list[str] = []

    def receive(name, payload):
        if name == "summary_status":
            states.append(payload["state"])
            if payload["state"] == "paused_translation":
                paused.set()
        if name == "summary_update":
            updated.set()

    coordinator = LiveSummaryCoordinator(
        result, processor, "课", "网络", receive,
        min_segments=3, min_interval=999,
        translation_busy=lambda: busy,
    )
    coordinator.start()
    for segment in segments:
        coordinator.submit(segment)
    assert paused.wait(timeout=2)
    assert processor.calls == []
    busy = False
    assert updated.wait(timeout=3)
    coordinator.close()
    assert processor.calls == [("English 0\nEnglish 1\nEnglish 2", "中文 0\n中文 1\n中文 2")]
    assert "updated" in states
