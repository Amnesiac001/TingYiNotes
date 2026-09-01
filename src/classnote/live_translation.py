from __future__ import annotations

import queue
import re
import threading
import time
from dataclasses import dataclass
from typing import Callable

from .models import CourseResult, Segment
from .services import TextProcessor
from .storage import CourseRepository


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
        self._retrying_ids: set[str] = set()

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
        # The English transcript is durable before any network request starts.
        self.repository.add_segment(self.result.id, segment, order, "pending")
        self.event("segment_original", segment)
        job = TranslationJob(segment=segment, queued_at=time.monotonic())
        if self._closed:
            self.repository.set_translation_state(
                segment.id,
                "retry",
                error="课堂正在结束，英文已保存，可在课程库中补译。",
            )
            self._emit_metrics()
            return segment
        try:
            self.queue.put_nowait(job)
        except queue.Full:
            self.repository.set_translation_state(
                segment.id,
                "retry",
                error="翻译队列已满，英文已保存，等待稍后补译。",
            )
            self.event("warning", "中文翻译暂时积压；英文已安全保存，稍后可以自动补译。")
        self._emit_metrics()
        return segment

    def close_and_wait(self, timeout: float = 30.0) -> bool:
        if self._closed:
            return all(not thread.is_alive() for thread in self.threads)
        self._closed = True
        for _ in self.threads:
            self.queue.put(None)
        deadline = time.monotonic() + timeout
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
                self.queue.put(None)
        # Publish the final shutdown snapshot (normally zero; timed-out workers may
        # still have only their termination sentinels queued).
        self._emit_metrics()
        return not alive

    def retry(self, segment_id: str) -> Segment:
        if self._closed:
            raise RuntimeError("课堂正在结束，失败译文可以稍后在课程库中补译。")
        with self._lock:
            if segment_id in self._retrying_ids:
                raise RuntimeError("这句内容已经在重新翻译。")
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
            job = self.queue.get()
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
                self.queue.task_done()
                self._emit_metrics()

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
            return self.text_processor.translate(segment.original_text, self.subject, self.terms).strip()
        parts: list[str] = []
        last_emit = 0.0
        for delta in stream_method(segment.original_text, self.subject, self.terms):
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
        self.event(
            "metrics",
            {
                "translation_queue": self.queue.qsize(),
                "translation_workers": self.workers,
                "translation_active": active_jobs,
                "last_translation_ms": last_translation_ms,
                "last_queue_wait_ms": last_queue_wait_ms,
            },
        )
