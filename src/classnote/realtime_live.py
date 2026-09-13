from __future__ import annotations

import base64
import queue
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from openai import OpenAI

from .config import Settings
from .courseware import CourseContext
from .exporter import export_markdown
from .live import LiveAudioMonitor, ResamplingRawInputStream
from .live_translation import LiveTranslationCoordinator
from .live_summary import LiveSummaryCoordinator
from .models import CourseResult, Segment
from .services import create_text_processor
from .storage import CourseRepository
from .temporary_audio import finish_temporary_audio, start_temporary_audio
from .usage import BudgetLimitReached, bind_course_usage


@dataclass(frozen=True)
class ParsedTranscriptEvent:
    kind: str
    item_id: str = ""
    text: str = ""
    audio_start_ms: int | None = None
    audio_end_ms: int | None = None
    message: str = ""


def _value(event: object, name: str, default: Any = None) -> Any:
    if isinstance(event, dict):
        return event.get(name, default)
    return getattr(event, name, default)


def parse_transcript_event(event: object) -> ParsedTranscriptEvent | None:
    """Normalize SDK models and raw dictionaries into testable app events."""
    event_type = str(_value(event, "type", ""))
    item_id = str(_value(event, "item_id", "") or "")
    if event_type == "conversation.item.input_audio_transcription.delta":
        return ParsedTranscriptEvent("delta", item_id, str(_value(event, "delta", "")))
    if event_type == "conversation.item.input_audio_transcription.completed":
        return ParsedTranscriptEvent("completed", item_id, str(_value(event, "transcript", "")))
    if event_type == "input_audio_buffer.speech_started":
        return ParsedTranscriptEvent(
            "speech_started", item_id, audio_start_ms=int(_value(event, "audio_start_ms", 0))
        )
    if event_type == "input_audio_buffer.speech_stopped":
        return ParsedTranscriptEvent(
            "speech_stopped", item_id, audio_end_ms=int(_value(event, "audio_end_ms", 0))
        )
    if event_type == "error":
        error = _value(event, "error", {})
        message = _value(error, "message", str(error))
        return ParsedTranscriptEvent("error", message=str(message))
    if event_type in {"session.created", "session.updated"}:
        return ParsedTranscriptEvent(event_type.replace(".", "_"))
    return None


class RealtimeLiveCourseSession:
    SAMPLE_RATE = 24000

    def __init__(
        self,
        title: str,
        subject: str,
        device: object,
        event: Callable[[str, object], None],
        course_context: CourseContext | None = None,
    ):
        self.settings = Settings.load()
        if not self.settings.api_key:
            raise RuntimeError("实时课堂需要 OPENAI_API_KEY，请先在 .env 中配置。")
        self.settings.ensure_directories()
        self.title = title
        self.subject = subject
        self.device = device
        self.event = event
        self.course_context = course_context or CourseContext()
        self.repository = CourseRepository(self.settings.database_path)
        device_name = str(getattr(device, "name", "microphone"))
        self.result = CourseResult(title, subject, f"realtime:{device_name}", [], "")
        self.client = OpenAI(api_key=self.settings.api_key)
        self.text_processor = create_text_processor(
            self.settings.text_provider,
            self.settings.text_model,
            self.settings.text_api_key,
            self.settings.text_base_url,
        )
        bind_course_usage(
            self.text_processor, self.repository, self.result.id,
            self.settings.text_provider, self.event, self.settings.class_budget_usd,
        )
        self.audio_queue: queue.Queue[bytes | None] = queue.Queue(maxsize=500)
        self.audio_monitor = LiveAudioMonitor(event)
        self.dropped_blocks = 0
        self.connection: Any = None
        self.connection_manager: Any = None
        self.stream: ResamplingRawInputStream | None = None
        self.sender_thread: threading.Thread | None = None
        self.receiver_thread: threading.Thread | None = None
        self.translations = LiveTranslationCoordinator(
            self.result,
            self.repository,
            self.text_processor,
            subject,
            event,
            terms=self.course_context.terms,
        )
        self.live_summary = LiveSummaryCoordinator(
            self.result, self.text_processor, title, subject, event
        )
        self.translations.on_translated = self.live_summary.submit
        self.stop_event = threading.Event()
        self.started_monotonic = 0.0
        self.partial_by_item: dict[str, str] = {}
        self.start_by_item: dict[str, int] = {}
        self.end_by_item: dict[str, int] = {}
        self.paused = False
        self.stopping = False
        self.finalized = False
        self.temporary_audio = None

    def start(self) -> None:
        self.repository.create_course(self.result)
        if self.settings.temporary_audio:
            self.temporary_audio = start_temporary_audio(
                self.settings.database_path, self.result.id, self.SAMPLE_RATE,
                lambda message: self.event("warning", message),
            )
        self.started_monotonic = time.monotonic()
        try:
            self.connection_manager = self.client.realtime.connect(
                model=self.settings.live_transcription_model,
                max_retries=5,
                initial_delay=0.5,
                max_delay=8.0,
                on_reconnecting=self._on_reconnecting,
            )
            self.connection = self.connection_manager.enter()
            self.connection.send(self._session_update())
            self.receiver_thread = threading.Thread(target=self._receive, daemon=True)
            self.sender_thread = threading.Thread(target=self._send_audio, daemon=True)
            self.receiver_thread.start()
            self.sender_thread.start()
            self.live_summary.start()
            self.translations.start()
            capture_rate = int(getattr(self.device, "default_samplerate", self.SAMPLE_RATE))
            self.stream = ResamplingRawInputStream(
                device_index=int(getattr(self.device, "index")),
                source_rate=capture_rate,
                target_rate=self.SAMPLE_RATE,
                target_blocksize=2400,
                callback=self._audio_callback,
            )
            self.stream.start()
            self.repository.set_course_state(self.result.id, "transcribing")
            self.event("status", "低延迟连接已建立，正在监听英文语音……")
        except Exception:
            self.stop_event.set()
            if self.stream is not None:
                try:
                    self.stream.stop()
                    self.stream.close()
                except Exception:
                    pass
                self.stream = None
            try:
                self.audio_queue.put_nowait(None)
            except queue.Full:
                pass
            self._close_transport()
            self.translations.close_and_wait(timeout=3)
            self.live_summary.close(timeout=1)
            if not self.result.segments:
                self.repository.delete_course(self.result.id)
            finish_temporary_audio(
                self.temporary_audio, self.repository, self.result.id,
                lambda message: self.event("warning", message),
            )
            raise

    def _session_update(self) -> dict[str, object]:
        prompt = f"An English classroom lecture about {self.subject}. Preserve technical terms and numbers."
        if self.course_context.hotwords:
            prompt += " Course terminology: " + self.course_context.hotword_prompt[:1800] + "."
        return {
            "type": "session.update",
            "session": {
                "type": "transcription",
                "audio": {
                    "input": {
                        "format": {"type": "audio/pcm", "rate": self.SAMPLE_RATE},
                        "transcription": {
                            "model": self.settings.live_transcription_model,
                            "prompt": prompt,
                            "languages": ["en"],
                            "delay": self.settings.live_transcription_delay,
                        },
                        "turn_detection": {
                            "type": "server_vad",
                            "threshold": 0.5,
                            "prefix_padding_ms": 300,
                            "silence_duration_ms": 650,
                        },
                    }
                },
            },
        }

    def _audio_callback(self, indata: bytes, frames: int, time_info: object, status: object) -> None:
        if self.stop_event.is_set() or self.paused:
            return
        pcm = bytes(indata)
        backup = getattr(self, "temporary_audio", None)
        if backup is not None:
            backup.submit(pcm)
        try:
            self.audio_queue.put_nowait(pcm)
        except queue.Full:
            # Never block PortAudio's callback; report once through status processing.
            self.dropped_blocks += 1
        block_ms = int(frames / self.SAMPLE_RATE * 1000)
        self.audio_monitor.observe(pcm, self.dropped_blocks * block_ms)

    def _send_audio(self) -> None:
        try:
            while True:
                pcm = self.audio_queue.get()
                if pcm is None:
                    return
                encoded = base64.b64encode(pcm).decode("ascii")
                self.connection.input_audio_buffer.append(audio=encoded)
        except Exception as exc:
            if not self.stop_event.is_set():
                self.event("warning", f"实时音频发送中断：{exc}")

    def _receive(self) -> None:
        try:
            for raw_event in self.connection:
                parsed = parse_transcript_event(raw_event)
                if parsed is not None:
                    self._handle_event(parsed)
                if self.stop_event.is_set() and self.stopping:
                    # Keep reading briefly; close_transport ends the iterator.
                    continue
        except Exception as exc:
            if not self.stop_event.is_set():
                self.event("warning", f"实时连接中断：{exc}。已完成字幕仍然安全保存。")

    def _handle_event(self, event: ParsedTranscriptEvent) -> None:
        if event.kind == "delta":
            self.partial_by_item[event.item_id] = self.partial_by_item.get(event.item_id, "") + event.text
            self.event("partial", (event.item_id, self.partial_by_item[event.item_id]))
        elif event.kind == "speech_started":
            self.start_by_item[event.item_id] = event.audio_start_ms or self._elapsed_ms()
        elif event.kind == "speech_stopped":
            self.end_by_item[event.item_id] = event.audio_end_ms or self._elapsed_ms()
        elif event.kind == "completed":
            text = event.text.strip()
            self.partial_by_item.pop(event.item_id, None)
            if text:
                start = self.start_by_item.pop(event.item_id, max(0, self._elapsed_ms() - 3000))
                end = self.end_by_item.pop(event.item_id, self._elapsed_ms())
                self.event("final_original", (event.item_id, text, start))
                self.translations.submit(text, start, end)
        elif event.kind == "error":
            self.event("warning", f"Realtime API：{event.message}")
        elif event.kind == "session_updated":
            self.event("status", "实时转写会话已就绪。")

    def pause(self) -> None:
        self.paused = True
        self.event("status", "实时录音已暂停。")

    def set_segment_marker(self, segment_id: str, marker: str) -> None:
        self.repository.set_segment_marker(segment_id, marker)
        for segment in self.result.segments:
            if segment.id == segment_id:
                segment.marker = marker
                return
        raise RuntimeError("没有找到需要标记的课堂字幕。")

    def retry_translation(self, segment_id: str) -> None:
        self.translations.retry(segment_id)

    def resume(self) -> None:
        self.paused = False
        self.event("status", "实时录音已继续。")

    def stop(self) -> None:
        if self.stopping:
            return
        self.stopping = True
        self.stop_event.set()
        self.event("status", "正在结束实时连接并处理剩余字幕……")
        threading.Thread(target=self._finish, daemon=True).start()

    def _finish(self) -> None:
        if self.stream is not None:
            self.stream.stop()
            self.stream.close()
            self.stream = None
        if not self._stop_audio_sender(timeout=3):
            self.event(
                "warning",
                "实时音频发送未能在结束时排空，最后一段语音可能缺失；已完成的字幕仍已保存。",
            )
        try:
            # Commit a sentence that is still in progress when the user clicks Stop.
            self.connection.input_audio_buffer.commit()
        except Exception:
            pass
        # Give server VAD and final transcript events a short grace period.
        time.sleep(2)
        self._close_transport()
        if self.receiver_thread is not None:
            self.receiver_thread.join(timeout=2)
        self.translations.close_and_wait(timeout=45)
        self.live_summary.close(timeout=2)
        try:
            self._finalize()
        finally:
            finish_temporary_audio(
                self.temporary_audio, self.repository, self.result.id,
                lambda message: self.event("warning", message),
            )

    def _stop_audio_sender(self, timeout: float) -> bool:
        """Bound both the final queue write and sender join during shutdown."""
        deadline = time.monotonic() + max(0.0, timeout)
        try:
            self.audio_queue.put(None, timeout=max(0.0, deadline - time.monotonic()))
        except queue.Full:
            return False
        if self.sender_thread is not None:
            self.sender_thread.join(timeout=max(0.0, deadline - time.monotonic()))
            return not self.sender_thread.is_alive()
        return True

    def _finalize(self) -> None:
        if self.finalized:
            return
        self.finalized = True
        if not self.result.segments:
            message = "没有得到有效语音内容，课程已停止。"
            self.repository.set_course_state(self.result.id, "failed", message)
            self.event("error", message)
            return
        try:
            self.event("status", "正在生成整节课的结构化笔记……")
            self.repository.set_course_state(self.result.id, "organizing")
            budget_exhausted = False
            try:
                self.result.notes_markdown = self.text_processor.organize(
                    self.title, self.subject,
                    self.result.organized_original_text,
                    self.result.organized_translated_text,
                )
            except BudgetLimitReached:
                budget_exhausted = True
                self.result.notes_markdown = "课后整理因课堂文本预算用完而暂停；英中课堂记录仍在下方。"
            self.repository.save_notes_draft(self.result.id, self.result.notes_markdown)
            topics = [
                (int(row["start_ms"]), str(row["title"]))
                for row in self.repository.get_course_topics(self.result.id)
            ]
            path: Path = export_markdown(self.result, self.settings.export_dir, topics).resolve()
            self.repository.finalize_course(
                self.result.id, self.result.notes_markdown, str(path)
            )
            remaining = self.repository.pending_segments(self.result.id)
            if remaining or budget_exhausted:
                message = (
                    f"文本预算已用完；{len(remaining)} 句中文待补译，课后整理可从课程库继续。"
                    if budget_exhausted else
                    f"仍有 {len(remaining)} 句中文待补译，英文和当前笔记已保存。"
                )
                self.repository.set_course_state(
                    self.result.id, "needs_attention", message, str(path)
                )
                self.event("warning", message)
            self.event("finished", (self.result, path))
        except Exception as exc:
            self.repository.set_course_state(
                self.result.id, "needs_attention", f"最终整理失败：{exc}"
            )
            self.event("error", f"字幕已保存，但最终整理失败：{exc}")

    def _on_reconnecting(self, reconnecting: object) -> None:
        attempt = getattr(reconnecting, "attempt", "?")
        self.event("status", f"网络连接中断，正在第 {attempt} 次重连；音频暂存在本地队列……")
        return None

    def _elapsed_ms(self) -> int:
        return int((time.monotonic() - self.started_monotonic) * 1000)

    def _close_transport(self) -> None:
        if self.connection is not None:
            try:
                self.connection.close()
            except Exception:
                pass
            self.connection = None
