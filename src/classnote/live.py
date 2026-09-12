from __future__ import annotations

import ctypes
import os
import queue
import tempfile
import threading
import time
import wave
import sys
from contextlib import contextmanager
from dataclasses import dataclass
from math import log10
from pathlib import Path
from typing import Callable

import numpy as np
import sounddevice as sd
from openai import OpenAI

from .config import Settings
from .courseware import CourseContext
from .exporter import export_markdown
from .live_summary import LiveSummaryCoordinator
from .models import CourseResult, Segment
from .services import OpenAITranscriber, create_text_processor
from .storage import CourseRepository


LiveEvent = Callable[[str, object], None]


@contextmanager
def windows_com_scope():
    """Initialize COM for Windows audio work performed on a background thread."""
    initialized = False
    if os.name == "nt":
        ole32 = ctypes.windll.ole32
        ole32.CoInitializeEx.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
        ole32.CoInitializeEx.restype = ctypes.c_long
        result = int(ole32.CoInitializeEx(None, 0))
        # Both S_OK and S_FALSE are successful and require a matching release.
        initialized = result in (0, 1)
    try:
        yield
    finally:
        if initialized:
            ctypes.windll.ole32.CoUninitialize()


@dataclass(frozen=True)
class AudioDevice:
    index: int
    name: str
    channels: int
    default_samplerate: int
    backend: str = "sounddevice"
    backend_id: str = ""
    hostapi: str = ""

    @property
    def is_loopback(self) -> bool:
        return self.backend == "soundcard-loopback"

    @property
    def label(self) -> str:
        kind = "系统声音" if self.is_loopback else "麦克风"
        return f"{kind} · {self.name}"


@dataclass(frozen=True)
class AudioLevelResult:
    status: str
    peak_dbfs: float
    rms_dbfs: float
    clipping_percent: float


def analyze_audio_level(samples: np.ndarray) -> AudioLevelResult:
    values = np.asarray(samples, dtype=np.float32).reshape(-1)
    if values.size == 0:
        return AudioLevelResult("silent", -120.0, -120.0, 0.0)
    absolute = np.abs(values)
    peak = float(np.max(absolute))
    rms = float(np.sqrt(np.mean(values * values)))
    peak_dbfs = 20 * log10(max(peak, 1e-6))
    rms_dbfs = 20 * log10(max(rms, 1e-6))
    clipping = float(np.mean(absolute >= 0.98) * 100)
    if peak_dbfs < -50 or rms_dbfs < -60:
        status = "silent"
    elif clipping >= 0.1 or peak_dbfs > -0.2:
        status = "clipping"
    elif peak_dbfs < -24 or rms_dbfs < -42:
        status = "low"
    else:
        status = "good"
    return AudioLevelResult(status, peak_dbfs, rms_dbfs, clipping)


class LiveAudioMonitor:
    """Emit throttled, backend-independent input health metrics from PCM16 audio."""

    def __init__(
        self,
        event: LiveEvent,
        interval_seconds: float = 0.25,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.event = event
        self.interval_seconds = max(0.0, interval_seconds)
        self.clock = clock
        self.last_emit = -1e9
        self.silent_since: float | None = None
        self.has_signal = False

    def observe(self, pcm: bytes, dropped_ms: int = 0) -> None:
        if not pcm:
            return
        values = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
        level = analyze_audio_level(values)
        now = self.clock()
        if level.status == "silent":
            if self.silent_since is None:
                self.silent_since = now
        else:
            self.silent_since = None
            self.has_signal = True
        if now - self.last_emit < self.interval_seconds:
            return
        self.last_emit = now
        self.event(
            "audio_metrics",
            {
                "status": level.status,
                "peak_dbfs": level.peak_dbfs,
                "rms_dbfs": level.rms_dbfs,
                "clipping_percent": level.clipping_percent,
                "silent_seconds": (
                    max(0.0, now - self.silent_since) if self.silent_since is not None else 0.0
                ),
                "has_signal": self.has_signal,
                "dropped_ms": max(0, dropped_ms),
            },
        )


def resample_pcm16(pcm: bytes, source_rate: int, target_rate: int) -> bytes:
    """Convert mono PCM16 between device and ASR rates without an extra dependency."""
    if source_rate <= 0 or target_rate <= 0:
        raise ValueError("音频采样率必须大于零。")
    if source_rate == target_rate or not pcm:
        return pcm
    samples = np.frombuffer(pcm, dtype=np.int16).astype(np.float32)
    if samples.size == 0:
        return b""
    if source_rate > target_rate and source_rate % target_rate == 0:
        ratio = source_rate // target_rate
        usable = samples.size - samples.size % ratio
        if usable <= 0:
            return b""
        converted = samples[:usable].reshape(-1, ratio).mean(axis=1)
    else:
        output_size = max(1, int(round(samples.size * target_rate / source_rate)))
        source_positions = np.arange(samples.size, dtype=np.float64)
        target_positions = np.arange(output_size, dtype=np.float64) * source_rate / target_rate
        converted = np.interp(target_positions, source_positions, samples)
    return np.clip(np.rint(converted), -32768, 32767).astype(np.int16).tobytes()


class ResamplingRawInputStream:
    """Open a microphone at its native rate and deliver PCM16 at the ASR rate."""

    def __init__(
        self,
        device_index: int,
        source_rate: int,
        target_rate: int,
        target_blocksize: int,
        callback: Callable[[bytes, int, object, object], None],
    ) -> None:
        self.source_rate = source_rate
        self.target_rate = target_rate
        self.callback = callback
        source_blocksize = max(1, int(round(target_blocksize * source_rate / target_rate)))
        self.stream = sd.RawInputStream(
            samplerate=source_rate,
            blocksize=source_blocksize,
            device=device_index,
            channels=1,
            dtype="int16",
            callback=self._callback,
        )

    def _callback(self, indata: bytes, frames: int, time_info: object, status: object) -> None:
        pcm = resample_pcm16(bytes(indata), self.source_rate, self.target_rate)
        self.callback(pcm, len(pcm) // 2, time_info, status)

    def start(self) -> None:
        self.stream.start()

    def stop(self) -> None:
        self.stream.stop()

    def close(self) -> None:
        self.stream.close()


def capture_audio_level(device: AudioDevice, seconds: float = 1.8) -> AudioLevelResult:
    """Record a short diagnostic sample without starting a classroom session."""
    if device.is_loopback:
        import soundcard as sc

        with windows_com_scope():
            microphone = sc.get_microphone(device.backend_id, include_loopback=True)
            sample_rate = 48000
            with microphone.recorder(samplerate=sample_rate, channels=1, blocksize=2048) as recorder:
                samples = recorder.record(numframes=max(1, int(sample_rate * seconds)))
    else:
        sample_rate = min(48000, max(16000, device.default_samplerate))
        samples = sd.rec(
            max(1, int(sample_rate * seconds)),
            samplerate=sample_rate,
            channels=1,
            dtype="float32",
            device=device.index,
            blocking=True,
        )
    return analyze_audio_level(np.asarray(samples))


def list_input_devices() -> list[AudioDevice]:
    devices: list[AudioDevice] = []
    seen: set[tuple[str, str]] = set()
    hostapis = sd.query_hostapis()
    indexed_devices = list(enumerate(sd.query_devices()))
    priority = {
        "Core Audio": 0,
        "Windows WASAPI": 0,
        "Windows WDM-KS": 1,
        "Windows DirectSound": 2,
        "MME": 3,
    }
    indexed_devices.sort(
        key=lambda value: priority.get(
            str(hostapis[int(value[1].get("hostapi", 0))].get("name", "")), 9
        )
    )
    has_wasapi_input = sys.platform == "win32" and any(
        int(info.get("max_input_channels", 0)) > 0
        and str(hostapis[int(info.get("hostapi", 0))].get("name", "")) == "Windows WASAPI"
        for _, info in indexed_devices
    )
    for index, info in indexed_devices:
        channels = int(info.get("max_input_channels", 0))
        if channels > 0:
            name = str(info.get("name", f"设备 {index}"))
            hostapi_index = int(info.get("hostapi", 0))
            hostapi = str(hostapis[hostapi_index].get("name", ""))
            if has_wasapi_input and hostapi != "Windows WASAPI":
                continue
            key = ("microphone", name.casefold())
            if key in seen:
                continue
            seen.add(key)
            devices.append(
                AudioDevice(
                    index=index,
                    name=name,
                    channels=channels,
                    default_samplerate=int(info.get("default_samplerate", 16000)),
                    hostapi=hostapi,
                )
            )
    try:
        if sys.platform != "win32":
            raise ImportError("Windows loopback capture is unavailable on this platform")
        import soundcard as sc

        with windows_com_scope():
            for microphone in sc.all_microphones(include_loopback=True):
                if not bool(getattr(microphone, "isloopback", False)):
                    continue
                name = str(microphone.name)
                backend_id = str(getattr(microphone, "id", getattr(microphone, "_id", name)))
                key = ("loopback", backend_id)
                if key in seen:
                    continue
                seen.add(key)
                devices.append(
                    AudioDevice(
                        index=-1000 - len(devices),
                        name=name,
                        channels=2,
                        default_samplerate=48000,
                        backend="soundcard-loopback",
                        backend_id=backend_id,
                        hostapi="Windows WASAPI",
                    )
                )
    except Exception:
        # Microphones remain usable even if WASAPI loopback enumeration fails.
        pass
    devices.sort(key=lambda value: (value.is_loopback, value.name.casefold()))
    return devices


class ChunkedMicrophoneRecorder:
    """Capture mono PCM and emit independent WAV chunks from a worker thread."""

    def __init__(
        self,
        device: AudioDevice,
        chunk_seconds: int,
        on_chunk: Callable[[Path, int, int], None],
        on_audio: Callable[[bytes], None] | None = None,
    ):
        self.device = device
        self.chunk_seconds = chunk_seconds
        self.on_chunk = on_chunk
        self.on_audio = on_audio
        self.sample_rate = min(48000, max(16000, device.default_samplerate))
        self.audio_queue: queue.Queue[bytes] = queue.Queue()
        self.stop_event = threading.Event()
        self.pause_event = threading.Event()
        self.thread: threading.Thread | None = None
        self.stream: sd.RawInputStream | None = None
        self.started_at = 0.0

    def start(self) -> None:
        self.started_at = time.monotonic()
        self.stream = sd.RawInputStream(
            samplerate=self.sample_rate,
            blocksize=0,
            device=self.device.index,
            channels=1,
            dtype="int16",
            callback=self._callback,
        )
        self.stream.start()
        self.thread = threading.Thread(target=self._collect, daemon=True)
        self.thread.start()

    def _callback(self, indata: bytes, frames: int, time_info: object, status: object) -> None:
        if not self.stop_event.is_set() and not self.pause_event.is_set():
            pcm = bytes(indata)
            if self.on_audio is not None:
                self.on_audio(pcm)
            self.audio_queue.put(pcm)

    def _collect(self) -> None:
        target_bytes = self.sample_rate * 2 * self.chunk_seconds
        buffer = bytearray()
        chunk_start_ms = 0
        while not self.stop_event.is_set() or not self.audio_queue.empty():
            try:
                data = self.audio_queue.get(timeout=0.2)
            except queue.Empty:
                continue
            buffer.extend(data)
            while len(buffer) >= target_bytes:
                chunk = bytes(buffer[:target_bytes])
                del buffer[:target_bytes]
                chunk_end_ms = chunk_start_ms + self.chunk_seconds * 1000
                self._emit(chunk, chunk_start_ms, chunk_end_ms)
                chunk_start_ms = chunk_end_ms
        if len(buffer) >= self.sample_rate * 2:  # 忽略不足一秒的尾音。
            duration_ms = int(len(buffer) / (self.sample_rate * 2) * 1000)
            self._emit(bytes(buffer), chunk_start_ms, chunk_start_ms + duration_ms)

    def _emit(self, pcm: bytes, start_ms: int, end_ms: int) -> None:
        handle = tempfile.NamedTemporaryFile(prefix="classnote-", suffix=".wav", delete=False)
        path = Path(handle.name)
        handle.close()
        with wave.open(str(path), "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(self.sample_rate)
            wav.writeframes(pcm)
        self.on_chunk(path, start_ms, end_ms)

    def pause(self) -> None:
        self.pause_event.set()

    def resume(self) -> None:
        self.pause_event.clear()

    def stop(self) -> None:
        self.stop_event.set()
        if self.stream is not None:
            self.stream.stop()
            self.stream.close()
        if self.thread is not None:
            self.thread.join(timeout=self.chunk_seconds + 3)


class ChunkedLiveCourseSession:
    def __init__(
        self,
        title: str,
        subject: str,
        device: AudioDevice,
        event: LiveEvent,
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
        self.result = CourseResult(title, subject, f"microphone:{device.name}", [], "")
        self.client = OpenAI(api_key=self.settings.api_key)
        self.transcriber = OpenAITranscriber(self.client, self.settings.transcription_model)
        self.text_processor = create_text_processor(
            self.settings.text_provider,
            self.settings.text_model,
            self.settings.text_api_key,
            self.settings.text_base_url,
            self.client if self.settings.text_provider == "openai" else None,
        )
        self.live_summary = LiveSummaryCoordinator(
            self.result, self.text_processor, title, subject, event
        )
        self.pending: queue.Queue[tuple[Path, int, int] | None] = queue.Queue()
        self.audio_monitor = LiveAudioMonitor(event)
        self.processor = threading.Thread(target=self._process_chunks, daemon=True)
        self.recorder = ChunkedMicrophoneRecorder(
            device,
            self.settings.live_chunk_seconds,
            lambda path, start, end: self.pending.put((path, start, end)),
            self.audio_monitor.observe,
        )
        self.stopping = False

    def start(self) -> None:
        self.repository.create_course(self.result)
        try:
            self.recorder.start()
        except Exception:
            self.repository.delete_course(self.result.id)
            raise
        self.repository.set_course_state(self.result.id, "transcribing")
        self.live_summary.start()
        self.processor.start()
        self.event("status", "正在录音；第一段字幕将在约 10 秒后出现。")

    def pause(self) -> None:
        self.recorder.pause()
        self.event("status", "录音已暂停。")

    def set_segment_marker(self, segment_id: str, marker: str) -> None:
        self.repository.set_segment_marker(segment_id, marker)
        for segment in self.result.segments:
            if segment.id == segment_id:
                segment.marker = marker
                return
        raise RuntimeError("没有找到需要标记的课堂字幕。")

    def resume(self) -> None:
        self.recorder.resume()
        self.event("status", "录音已继续。")

    def stop(self) -> None:
        if self.stopping:
            return
        self.stopping = True
        self.event("status", "正在结束录音并处理最后片段……")
        threading.Thread(target=self._finish_recording, daemon=True).start()

    def _finish_recording(self) -> None:
        self.recorder.stop()
        self.pending.put(None)

    def _process_chunks(self) -> None:
        while True:
            item = self.pending.get()
            if item is None:
                break
            path, start_ms, end_ms = item
            try:
                self.event("status", f"正在处理 {start_ms // 1000}～{end_ms // 1000} 秒……")
                segments = self.transcriber.transcribe(path, self.subject)
                original = " ".join(segment.original_text for segment in segments).strip()
                if not original:
                    continue
                translation = self.text_processor.translate(
                    original, self.subject, self.course_context.terms
                )
                segment = Segment(original, translation, start_ms, end_ms)
                self.result.segments.append(segment)
                self.repository.add_segment(self.result.id, segment, len(self.result.segments) - 1)
                self.event("segment", segment)
                self.live_summary.submit(segment)
            except Exception as exc:
                self.event("warning", f"片段 {start_ms // 1000} 秒处理失败：{exc}")
            finally:
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    pass
        self.live_summary.close(timeout=2)
        self._finalize()

    def _finalize(self) -> None:
        if not self.result.segments:
            message = "没有得到有效语音内容，课程已停止。"
            self.repository.set_course_state(self.result.id, "failed", message)
            self.event("error", message)
            return
        try:
            self.event("status", "正在生成整节课的结构化笔记……")
            self.repository.set_course_state(self.result.id, "organizing")
            self.result.notes_markdown = self.text_processor.organize(
                self.title,
                self.subject,
                self.result.organized_original_text,
                self.result.organized_translated_text,
            )
            self.repository.save_notes_draft(self.result.id, self.result.notes_markdown)
            topics = [
                (int(row["start_ms"]), str(row["title"]))
                for row in self.repository.get_course_topics(self.result.id)
            ]
            path = export_markdown(self.result, self.settings.export_dir, topics).resolve()
            self.repository.finalize_course(
                self.result.id, self.result.notes_markdown, str(path)
            )
            self.event("finished", (self.result, path))
        except Exception as exc:
            # 分段已安全入库；整理失败不会丢失原文和译文。
            self.repository.set_course_state(
                self.result.id, "needs_attention", f"最终整理失败：{exc}"
            )
            self.event("error", f"字幕已保存，但最终整理失败：{exc}")


class LiveCourseSession:
    """Select the low-latency or compatibility backend from environment settings."""

    def __new__(
        cls,
        title: str,
        subject: str,
        device: AudioDevice,
        event: LiveEvent,
        course_context: CourseContext | None = None,
    ) -> object:
        settings = Settings.load()
        if settings.live_mode == "local":
            if sys.platform == "darwin":
                from .mac_live import MacLocalLiveCourseSession

                return MacLocalLiveCourseSession(title, subject, device, event, course_context)
            from .local_live import LocalLiveCourseSession

            return LocalLiveCourseSession(title, subject, device, event, course_context)
        if settings.live_mode == "chunked":
            return ChunkedLiveCourseSession(title, subject, device, event, course_context)
        if settings.live_mode != "realtime":
            raise ValueError("LIVE_MODE 只能是 local、realtime 或 chunked。")
        from .realtime_live import RealtimeLiveCourseSession

        return RealtimeLiveCourseSession(title, subject, device, event, course_context)
