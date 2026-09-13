from __future__ import annotations

import queue
import re
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Callable

from .courseware import relevant_terms
from .models import CourseResult, Segment
from .services import TextProcessor
from .storage import CourseRepository
from .usage import BudgetLimitReached


LiveEvent = Callable[[str, object], None]


@dataclass
class TranslationJob:
    segment: Segment
    attempt: int = 0
    queued_at: float = 0.0


def protected_tokens(text: str) -> set[str]:
    """Extract values that a classroom translation should never silently change."""
    pattern = r"(?:\b[A-Z][A-Z0-9_-]{1,}\b)|(?:\b\d+(?:\.\d+)?%?\b)"
    return {value.casefold() for value in re.findall(pattern, text)}


class LiveTranslationCoordinator:
    """Persist English first, then translate concurrently without blocking ASR."""

    def __init__(
        self,
        result: CourseResult,
        repository: CourseRepository,
        text_processor: TextProcessor,
        subject: str,
        event: LiveEvent,
        terms: dict[str, str] | None = None,
        workers: int = 2,
        max_queue: int = 64,
        on_translated: Callable[[Segment], None] | None = None,
        catchup_quiet_seconds: float = 2.0,
    ) -> None:
        self.result = result
        self.repository = repository
        self.text_processor = text_processor
        self.subject = subject
        self.event = event
        self.terms = terms or {}
        self.queue: queue.Queue[TranslationJob | None] = queue.Queue(maxsize=max_queue)
        self.workers = max(1, workers)
        self.threads: list[threading.Thread] = []
        self._lock = threading.Lock()
        self._order = 0
        self._closed = False
        self._active_jobs = 0
        self._last_translation_ms = 0
        self._last_queue_wait_ms = 0
        self.on_translated = on_translated
        self.catchup_quiet_seconds = max(0.0, catchup_quiet_seconds)
        self._retrying_ids: set[str] = set()
        self._last_overflow_notice = 0.0
        self._deferred: deque[TranslationJob] = deque()
        self._deferred_ids: set[str] = set()
        self._catchup_active = False
        self._last_submit_at = time.monotonic()

    def start(self) -> None:
        for index in range(self.workers):
            thread = threading.Thread(
                target=self._worker,
                name=f"classnote-translation-{index + 1}",
                daemon=True,
            )
            thread.start()
            self.threads.append(thread)

    def submit(self, original: str, start_ms: int, end_ms: int) -> Segment:
        text = original.strip()
        if not text:
            raise ValueError("不能提交空字幕。")
        segment = Segment(text, "", start_ms, max(start_ms, end_ms))
        with self._lock:
            order = self._order
            self._order += 1
            self.result.segments.append(segment)
            self._last_submit_at = time.monotonic()
        # The English transcript is durable before any network request starts.
        self.repository.add_segment(self.result.id, segment, order, "pending")
        self.event("segment_original", segment)
        job = TranslationJob(segment=segment, queued_at=time.monotonic())
        if self._closed:
            message = "课堂正在结束，英文已保存，可在课程库中补译。"
            self.repository.set_translation_state(
                segment.id, "retry", error=message,
            )
            self.event("translation_failed", (segment.id, message))
            self._emit_metrics()
            return segment
        guard = getattr(self.text_processor, "budget_guard", None)
        if callable(guard) and not guard("translation"):
            message = "本节课文本预算已用完，英文已保存，可在课程库中补译。"
            self.repository.set_translation_state(segment.id, "retry", error=message)
            self.event("translation_failed", (segment.id, message))
            self._emit_metrics()
            return segment
        try:
            self.queue.put_nowait(job)
        except queue.Full:
            message = "翻译队列已满，英文已保存；课堂短暂停顿后会尝试自动补译。"
            self.repository.set_translation_state(
                segment.id, "retry", error=message,
            )
            with self._lock:
                self._deferred.append(job)
                self._deferred_ids.add(segment.id)
            self.event("translation_failed", (segment.id, message))
            now = time.monotonic()
            if now - self._last_overflow_notice >= 15:
                self._last_overflow_notice = now
                self.event("warning", "中文翻译队列已满；英文已保存，课堂短暂停顿后会自动补译，也可课后从课程库继续。")
        self._emit_metrics()
        return segment

    def close_and_wait(self, timeout: float = 30.0) -> bool:
        if self._closed:
            return all(not thread.is_alive() for thread in self.threads)
        self._closed = True
        deadline = time.monotonic() + max(0.0, timeout)
        for _ in self.threads:
            try:
                self.queue.put(None, timeout=max(0.0, deadline - time.monotonic()))
            except queue.Full:
                # A full backlog must not turn a bounded shutdown into an
                # unbounded wait for a stalled network translation.
                break
        for thread in self.threads:
            thread.join(timeout=max(0.0, deadline - time.monotonic()))
        alive = [thread for thread in self.threads if thread.is_alive()]
        if alive:
            # The normal path drains all submitted work. If the deadline is reached,
            # preserve anything not yet started as explicit retry work rather than
            # silently exporting an incomplete translation as finished.
            while True:
                try:
                    queued = self.queue.get_nowait()
                except queue.Empty:
                    break
                if queued is not None:
                    self.repository.set_translation_state(
                        queued.segment.id,
                        "retry",
                        error="课堂结束时尚未翻译，可在课程库中补译。",
                    )
                self.queue.task_done()
            for _ in alive:
                try:
                    self.queue.put_nowait(None)
                except queue.Full:
                    break
        # Publish the final shutdown snapshot (normally zero; timed-out workers may
        # still have only their termination sentinels queued).
        self._emit_metrics()
        return not alive

    def retry(self, segment_id: str) -> Segment:
        if self._closed:
            raise RuntimeError("课堂正在结束，失败译文可以稍后在课程库中补译。")
        guard = getattr(self.text_processor, "budget_guard", None)
        if callable(guard) and not guard("translation"):
            raise BudgetLimitReached()
        with self._lock:
            if segment_id in self._retrying_ids:
                raise RuntimeError("这句内容已经在重新翻译。")
            if segment_id in self._deferred_ids:
                raise RuntimeError("这句已在等待课堂空闲时自动补译，英文已保存。")
            segment = next(
                (item for item in self.result.segments if item.id == segment_id), None
            )
            if segment is not None:
                self._retrying_ids.add(segment_id)
        if segment is None:
            raise RuntimeError("没有找到需要重试的课堂字幕。")
        segment.translated_text = ""
        self.repository.set_translation_state(segment.id, "retry", error="")
        try:
            self.queue.put_nowait(
                TranslationJob(segment=segment, attempt=0, queued_at=time.monotonic())
            )
        except queue.Full as exc:
            with self._lock:
                self._retrying_ids.discard(segment_id)
            self.repository.set_translation_state(
                segment.id, "failed", error="翻译队列暂时已满，请稍后再试。"
            )
            raise RuntimeError("翻译队列暂时已满，请稍后再试。") from exc
        self.event("segment_retrying", segment.id)
        self._emit_metrics()
        return segment

    def _worker(self) -> None:
        while True:
            from_deferred = False
            try:
                job = self.queue.get(timeout=0.2)
            except queue.Empty:
                if self._closed:
                    return
                job = self._take_deferred()
                if job is None:
                    continue
                from_deferred = True
                self.event("segment_retrying", job.segment.id)
            if job is None:
                self.queue.task_done()
                return
            started = time.monotonic()
            with self._lock:
                self._active_jobs += 1
                self._last_queue_wait_ms = int(max(0.0, started - job.queued_at) * 1000)
            self._emit_metrics()
            try:
                self._translate(job)
            finally:
                with self._lock:
                    self._active_jobs = max(0, self._active_jobs - 1)
                    self._last_translation_ms = int((time.monotonic() - started) * 1000)
                if from_deferred:
                    with self._lock:
                        self._catchup_active = False
                else:
                    self.queue.task_done()
                self._emit_metrics()

    def _take_deferred(self) -> TranslationJob | None:
        guard = getattr(self.text_processor, "budget_guard", None)
        if callable(guard) and not guard("translation"):
            return None
        with self._lock:
            if (
                self._closed or self._catchup_active or not self._deferred
                or not self.queue.empty()
                or (self.workers > 1 and self._active_jobs >= self.workers - 1)
                or time.monotonic() - self._last_submit_at < self.catchup_quiet_seconds
            ):
                return None
            while self._deferred:
                job = self._deferred.popleft()
                self._deferred_ids.discard(job.segment.id)
                if job.segment.translated_text or job.segment.id in self._retrying_ids:
                    continue
                self._retrying_ids.add(job.segment.id)
                self._catchup_active = True
                return job
        return None

    def _translate(self, job: TranslationJob) -> None:
        segment = job.segment
        self.repository.set_translation_state(segment.id, "translating")
        try:
            translated = self._stream_or_translate(segment)
            if not translated:
                raise RuntimeError("翻译服务没有返回内容。")
            segment.translated_text = translated
            self.repository.set_translation_state(segment.id, "completed", translated)
            with self._lock:
                self._retrying_ids.discard(segment.id)
            self.event("segment_update", segment)
            if self.on_translated is not None:
                try:
                    self.on_translated(segment)
                except Exception as exc:
                    self.event("warning", f"滚动摘要暂时不可用，不影响翻译：{exc}")
            missing = protected_tokens(segment.original_text) - protected_tokens(translated)
            if missing:
                values = "、".join(sorted(missing)[:5])
                self.event("warning", f"已保留英文原文；请留意本句中的数字或缩写：{values}")
        except BudgetLimitReached as exc:
            self.repository.set_translation_state(segment.id, "retry", error=str(exc))
            with self._lock:
                self._retrying_ids.discard(segment.id)
            self.event("translation_failed", (segment.id, str(exc)))
        except Exception as exc:
            if job.attempt < 1 and not self._closed:
                self.repository.set_translation_state(segment.id, "retry", error=str(exc))
                time.sleep(0.4)
                try:
                    self.queue.put_nowait(
                        TranslationJob(segment, job.attempt + 1, time.monotonic())
                    )
                    return
                except queue.Full:
                    pass
            self.repository.set_translation_state(segment.id, "failed", error=str(exc))
            with self._lock:
                self._retrying_ids.discard(segment.id)
            self.event("translation_failed", (segment.id, str(exc)))
            self.event("warning", f"一句中文翻译失败，英文已保存：{exc}")

    def _stream_or_translate(self, segment: Segment) -> str:
        stream_method = getattr(self.text_processor, "translate_stream", None)
        if not callable(stream_method):
            return self.text_processor.translate(
                segment.original_text, self.subject,
                relevant_terms(segment.original_text, self.terms),
            ).strip()
        parts: list[str] = []
        last_emit = 0.0
        for delta in stream_method(
            segment.original_text, self.subject,
            relevant_terms(segment.original_text, self.terms),
        ):
            parts.append(str(delta))
            now = time.monotonic()
            if now - last_emit >= 0.05:
                self.event("translation_delta", (segment.id, "".join(parts)))
                last_emit = now
        translated = "".join(parts).strip()
        if translated:
            self.event("translation_delta", (segment.id, translated))
        return translated

    def _emit_metrics(self) -> None:
        with self._lock:
            active_jobs = self._active_jobs
            last_translation_ms = self._last_translation_ms
            last_queue_wait_ms = self._last_queue_wait_ms
            deferred = len(self._deferred)
        self.event(
            "metrics",
            {
                "translation_queue": self.queue.qsize() + deferred,
                "translation_deferred": deferred,
                "translation_workers": self.workers,
                "translation_active": active_jobs,
                "last_translation_ms": last_translation_ms,
                "last_queue_wait_ms": last_queue_wait_ms,
            },
        )
