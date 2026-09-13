from __future__ import annotations

import json
import queue
import threading
import time
from dataclasses import asdict, dataclass
from typing import Callable

from .models import CourseResult, Segment
from .services import TextProcessor


LiveEvent = Callable[[str, object], None]


@dataclass(frozen=True)
class LiveSummarySnapshot:
    topic: str = ""
    key_points: tuple[str, ...] = ()
    terms: tuple[str, ...] = ()
    questions: tuple[str, ...] = ()

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False, separators=(",", ":"))


def _clean_text(value: object, limit: int = 160) -> str:
    if not isinstance(value, str):
        return ""
    return " ".join(value.strip().split())[:limit]


def _clean_list(value: object, maximum: int) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    result: list[str] = []
    for item in value:
        text = _clean_text(item)
        if text and text not in result:
            result.append(text)
        if len(result) >= maximum:
            break
    return tuple(result)


def parse_live_summary(raw: str) -> LiveSummarySnapshot:
    """Parse the model's small JSON contract without allowing arbitrary rich text."""
    text = raw.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        raise RuntimeError("滚动摘要返回的 JSON 无法解析。") from exc
    if not isinstance(value, dict):
        raise RuntimeError("滚动摘要返回格式不正确。")
    snapshot = LiveSummarySnapshot(
        topic=_clean_text(value.get("topic"), 80),
        key_points=_clean_list(value.get("key_points"), 6),
        terms=_clean_list(value.get("terms"), 8),
        questions=_clean_list(value.get("questions"), 5),
    )
    if not any((snapshot.topic, snapshot.key_points, snapshot.terms, snapshot.questions)):
        raise RuntimeError("滚动摘要没有返回可用内容。")
    return snapshot


class LiveSummaryCoordinator:
    """Build low-frequency summaries on one background thread, off the translation path."""

    def __init__(
        self,
        result: CourseResult,
        text_processor: TextProcessor,
        title: str,
        subject: str,
        event: LiveEvent,
        min_segments: int = 6,
        min_interval: float = 45.0,
        max_context_segments: int = 24,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.result = result
        self.text_processor = text_processor
        self.title = title
        self.subject = subject
        self.event = event
        self.min_segments = max(1, min_segments)
        self.min_interval = max(0.0, min_interval)
        self.max_context_segments = max(1, max_context_segments)
        self.clock = clock
        self.queue: queue.Queue[Segment | None] = queue.Queue(maxsize=64)
        self.thread: threading.Thread | None = None
        self._closed = False
        self._last_run = self.clock()
        self._snapshot = LiveSummarySnapshot()
        self._summarized_ids: set[str] = set()
        self._latest_summarized_end_ms = -1
        self._warning_shown = False

    def start(self) -> None:
        if self.thread is not None:
            return
        self.thread = threading.Thread(
            target=self._worker, name="classnote-live-summary", daemon=True
        )
        self.thread.start()

    def submit(self, segment: Segment) -> None:
        if self._closed or not segment.translated_text.strip():
            return
        if self._budget_paused():
            return
        try:
            self.queue.put_nowait(segment)
        except queue.Full:
            # A notification can be dropped safely: every run reads the latest
            # sorted result window instead of relying on queue contents as data.
            pass

    def close(self, timeout: float = 2.0) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self.queue.put_nowait(None)
        except queue.Full:
            pass
        if self.thread is not None:
            self.thread.join(timeout=max(0.0, timeout))

    def _worker(self) -> None:
        completed = 0
        while True:
            try:
                item = self.queue.get(timeout=1.0)
            except queue.Empty:
                if self._closed:
                    return
                threshold = 1 if self._summarized_ids else 3
                if (
                    self.clock() - self._last_run >= self.min_interval
                    and self._pending_count() >= threshold
                ):
                    completed = 0
                    self._last_run = self.clock()
                    self._summarize()
                continue
            if item is None or self._closed:
                return
            if self._budget_paused():
                completed = 0
                continue
            completed += 1
            self.event(
                "summary_status",
                {
                    "state": "collecting",
                    "count": completed,
                    "target": self.min_segments,
                },
            )
            now = self.clock()
            due_by_count = completed >= self.min_segments
            due_by_time = completed >= 3 and now - self._last_run >= self.min_interval
            if not due_by_count and not due_by_time:
                continue
            completed = 0
            self._last_run = now
            self._summarize()

    def _pending_segments(self) -> list[Segment]:
        return sorted(
            (
                segment for segment in list(self.result.segments)
                if segment.translated_text.strip() and segment.id not in self._summarized_ids
            ),
            key=lambda segment: (segment.start_ms, segment.end_ms, segment.id),
        )

    def _pending_count(self) -> int:
        return sum(
            segment.translated_text.strip() != "" and segment.id not in self._summarized_ids
            for segment in list(self.result.segments)
        )

    def _summarize(self) -> None:
        if self._budget_paused():
            return
        method = getattr(self.text_processor, "summarize_live", None)
        if not callable(method):
            return
        segments = self._pending_segments()[:self.max_context_segments]
        if not segments:
            return
        original = "\n".join(
            f"{self.result._marker_prefix(segment.marker)}{segment.original_text}"
            for segment in segments
        )
        translation = "\n".join(
            f"{self.result._marker_prefix(segment.marker)}{segment.translated_text}"
            for segment in segments
        )
        try:
            self.event("summary_status", {"state": "working"})
            raw = method(
                self._snapshot.to_json(),
                self.title,
                self.subject,
                original,
                translation,
            )
            snapshot = parse_live_summary(raw)
            if self._closed:
                return
            latest_in_batch = max(segment.end_ms for segment in segments)
            if (
                latest_in_batch <= self._latest_summarized_end_ms
                and self._snapshot.topic
                and snapshot.topic != self._snapshot.topic
            ):
                # A late translation may add facts, but it must not rewind the
                # current-topic timeline to a section already covered earlier.
                snapshot = LiveSummarySnapshot(
                    topic=self._snapshot.topic,
                    key_points=snapshot.key_points,
                    terms=snapshot.terms,
                    questions=snapshot.questions,
                )
            self._snapshot = snapshot
            self._summarized_ids.update(segment.id for segment in segments)
            self._latest_summarized_end_ms = max(
                self._latest_summarized_end_ms, latest_in_batch
            )
            self._warning_shown = False
            self.event("summary_update", self._snapshot)
            self.event("summary_status", {"state": "paused_budget" if self._budget_paused() else "updated"})
        except Exception as exc:
            if not self._closed and not self._warning_shown:
                self._warning_shown = True
                self.event("warning", f"滚动摘要暂时未更新，不影响字幕和翻译：{exc}")
            if not self._closed:
                self.event("summary_status", {"state": "paused_budget" if self._budget_paused() else "waiting"})

    def _budget_paused(self) -> bool:
        guard = getattr(self.text_processor, "budget_guard", None)
        return callable(guard) and not guard("summary")
