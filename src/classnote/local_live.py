from __future__ import annotations

import os
import queue
import site
import threading
import time
from pathlib import Path
from typing import Callable

import numpy as np

from .config import Settings
from .courseware import CourseContext
from .exporter import export_markdown
from .live import LiveAudioMonitor, ResamplingRawInputStream, windows_com_scope
from .live_translation import LiveTranslationCoordinator
from .live_summary import LiveSummaryCoordinator
from .models import CourseResult
from .services import create_text_processor
from .storage import CourseRepository


LiveEvent = Callable[[str, object], None]
_DLL_HANDLES: list[object] = []
_MODEL_CACHE: dict[tuple[str, str], object] = {}
_MODEL_CACHE_LOCK = threading.Lock()


class LoopbackPCMStream:
    """Small sounddevice-like wrapper around Windows WASAPI loopback capture."""

    def __init__(
        self,
        backend_id: str,
        samplerate: int,
        blocksize: int,
        callback: object,
        error_callback: Callable[[Exception], None] | None = None,
    ):
        self.backend_id = backend_id
        self.samplerate = samplerate
        self.blocksize = blocksize
        self.callback = callback
        self.error_callback = error_callback
        self.stop_event = threading.Event()
        self.ready_event = threading.Event()
        self.thread: threading.Thread | None = None
        self.error: Exception | None = None

    def start(self) -> None:
        self.thread = threading.Thread(target=self._run, name="classnote-loopback", daemon=True)
        self.thread.start()
        if not self.ready_event.wait(timeout=5):
            raise RuntimeError("系统声音设备启动超时。")
        if self.error is not None:
            raise self.error

    def _run(self) -> None:
        try:
            import soundcard as sc

            with windows_com_scope():
                microphone = sc.get_microphone(self.backend_id, include_loopback=True)
                with microphone.recorder(
                    samplerate=self.samplerate,
                    channels=1,
                    blocksize=self.blocksize,
                ) as recorder:
                    self.ready_event.set()
                    while not self.stop_event.is_set():
                        samples = recorder.record(numframes=self.blocksize)
                        mono = np.asarray(samples, dtype=np.float32).reshape(-1)
                        pcm = (np.clip(mono, -1.0, 1.0) * 32767).astype(np.int16).tobytes()
                        self.callback(pcm, self.blocksize, None, None)  # type: ignore[misc]
        except Exception as exc:
            opened = self.ready_event.is_set()
            self.error = exc
            self.ready_event.set()
            if opened and self.error_callback is not None and not self.stop_event.is_set():
                self.error_callback(exc)

    def stop(self) -> None:
        self.stop_event.set()
        if self.thread is not None:
            self.thread.join(timeout=3)

    def close(self) -> None:
        self.stop()


def _prepare_nvidia_dlls() -> None:
    """Make optional pip-installed CUDA libraries visible to CTranslate2 on Windows."""
    if os.name != "nt" or not hasattr(os, "add_dll_directory"):
        return
    roots = [Path(value) for value in site.getsitepackages()]
    discovered: list[str] = []
    for root in roots:
        for relative in ("nvidia/cublas/bin", "nvidia/cudnn/bin"):
            candidate = root / relative
            if candidate.is_dir():
                discovered.append(str(candidate))
                try:
                    _DLL_HANDLES.append(os.add_dll_directory(str(candidate)))
                except OSError:
                    pass
    if discovered:
        current = os.environ.get("PATH", "")
        os.environ["PATH"] = os.pathsep.join(discovered + [current])


def common_prefix(left: str, right: str) -> str:
    """Return the word prefix shared by consecutive live hypotheses."""
    first = left.split()
    second = right.split()
    size = 0
    for a, b in zip(first, second):
        if a.casefold().strip(".,!?;:") != b.casefold().strip(".,!?;:"):
            break
        size += 1
    return " ".join(second[:size])


class LocalLiveCourseSession:
    SAMPLE_RATE = 16000
    BLOCK_SIZE = 480  # 30 ms
    ENDPOINT_SILENCE_MS = 520
    SOFT_ENDPOINT_SILENCE_MS = 320
    MAX_UTTERANCE_SECONDS = 10
    AUDIO_BLOCK_MS = 30
    MAX_AUDIO_BUFFER_SECONDS = 300

    def __init__(
        self,
        title: str,
        subject: str,
        device: object,
        event: LiveEvent,
        course_context: CourseContext | None = None,
    ):
        self.settings = Settings.load()
        self.transcription_model_name = self.settings.local_transcription_model
        self.settings.ensure_directories()
        self.title = title
        self.subject = subject
        self.device = device
        self.event = event
        self.course_context = course_context or CourseContext()
        self.repository = CourseRepository(self.settings.database_path)
        device_name = str(getattr(device, "name", "microphone"))
        self.result = CourseResult(title, subject, f"local:{device_name}", [], "")
        self.text_processor = create_text_processor(
            self.settings.text_provider,
            self.settings.text_model,
            self.settings.text_api_key,
            self.settings.text_base_url,
        )
        self.live_summary = LiveSummaryCoordinator(
            self.result, self.text_processor, title, subject, event
        )
        self.translations = LiveTranslationCoordinator(
            self.result,
            self.repository,
            self.text_processor,
            subject,
            event,
            terms=self.course_context.terms,
            on_translated=self.live_summary.submit,
        )
        self.audio_queue: queue.Queue[tuple[bytes, int]] = queue.Queue(
            maxsize=self.MAX_AUDIO_BUFFER_SECONDS * 1000 // self.AUDIO_BLOCK_MS
        )
        self.audio_monitor = LiveAudioMonitor(event)
        self.stop_event = threading.Event()
        self.pause_event = threading.Event()
        self.ready_event = threading.Event()
        self.capture_ready_event = threading.Event()
        self.model_ready_event = threading.Event()
        self.stream: ResamplingRawInputStream | LoopbackPCMStream | None = None
        self.worker: threading.Thread | None = None
        self.started_monotonic = 0.0
        self.dropped_blocks = 0
        self._last_buffer_report_second = -1
        self.finalized = False

    def start(self) -> None:
        self.repository.create_course(self.result)
        self.live_summary.start()
        self.translations.start()
        self.started_monotonic = time.monotonic()
        self.event(
            "status",
            "正在打开音频设备；采集开始后会自动缓存开场内容……",
        )
        self.worker = threading.Thread(target=self._run, name="classnote-local-asr", daemon=True)
        self.worker.start()

    def pause(self) -> None:
        self.pause_event.set()
        self.event(
            "status",
            (
                "音频采集已暂停；本地模型仍在准备，已缓存的开场音频会保留。"
                if not self.model_ready_event.is_set()
                else "本地录音已暂停，正在保存暂停前的最后一句。"
            ),
        )

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
        self.pause_event.clear()
        self.event(
            "status",
            (
                "音频采集已继续，开场内容正在缓存；本地模型仍在准备。"
                if not self.model_ready_event.is_set()
                else "本地录音已继续。"
            ),
        )

    def stop(self) -> None:
        if self.stop_event.is_set():
            return
        self.stop_event.set()
        self.event(
            "status",
            (
                "正在等待本地模型处理已缓存的开场音频，然后安全结束课堂……"
                if not self.model_ready_event.is_set() and not self.audio_queue.empty()
                else "正在结束本地识别并处理剩余字幕……"
            ),
        )

    def _callback(self, indata: bytes, frames: int, time_info: object, status: object) -> None:
        if self.stop_event.is_set() or self.pause_event.is_set():
            return
        pcm = bytes(indata)
        captured_end_ms = self._elapsed_ms()
        try:
            self.audio_queue.put_nowait((pcm, captured_end_ms))
        except queue.Full:
            self.dropped_blocks += 1
            if self.dropped_blocks in {1, 10, 50}:
                milliseconds = self.dropped_blocks * self.AUDIO_BLOCK_MS
                reason = (
                    "本地模型准备时间过长，开场音频缓存已满"
                    if not self.model_ready_event.is_set()
                    else "本地识别暂时落后"
                )
                self.event("warning", f"{reason}，已跳过约 {milliseconds}ms 音频。")
        if not self.model_ready_event.is_set():
            buffered_seconds = self.audio_queue.qsize() * self.AUDIO_BLOCK_MS // 1000
            if self._last_buffer_report_second < 0 or buffered_seconds >= self._last_buffer_report_second + 5:
                self._last_buffer_report_second = buffered_seconds
                self.event(
                    "capture_buffer",
                    {
                        "buffered_ms": self.audio_queue.qsize() * self.AUDIO_BLOCK_MS,
                        "capacity_ms": self.MAX_AUDIO_BUFFER_SECONDS * 1000,
                    },
                )
        self.audio_monitor.observe(pcm, self.dropped_blocks * self.AUDIO_BLOCK_MS)

    def _capture_failed(self, exc: Exception) -> None:
        """Surface a capture failure that happens after the stream opened."""
        self.event(
            "error",
            "系统声音采集已中断。请检查播放设备是否被切换或断开；已识别的课堂内容仍会保留。"
            f"\n\n技术信息：{exc}",
        )
        self.stop_event.set()

    def _run(self) -> None:
        model_name = getattr(
            self, "transcription_model_name", self.settings.local_transcription_model
        )
        try:
            self.stream = self._create_capture_stream()
            self.stream.start()
            self.capture_ready_event.set()
            self.repository.set_course_state(self.result.id, "transcribing")
            self.event(
                "capture_started",
                {
                    "buffer_capacity_ms": self.MAX_AUDIO_BUFFER_SECONDS * 1000,
                    "source": "system" if bool(getattr(self.device, "is_loopback", False)) else "microphone",
                },
            )
            self.event(
                "status",
                f"音频采集已开始，开场内容正在缓存；正在准备本地模型 {model_name}……",
            )
            load_started = time.monotonic()
            model, get_speech_timestamps, vad_options, reused = (
                self._load_recognition_runtime()
            )
            load_seconds = time.monotonic() - load_started
            self.model_ready_event.set()
            self.ready_event.set()
            buffered_ms = self.audio_queue.qsize() * self.AUDIO_BLOCK_MS
            self.event(
                "model_ready",
                {
                    "model": model_name,
                    "load_seconds": load_seconds,
                    "buffered_ms": buffered_ms,
                    "reused": reused,
                },
            )
            self.event(
                "status",
                (
                    f"系统声音直采已就绪（{model_name}，{'已预热' if reused else f'加载 {load_seconds:.1f}s'}）。"
                    if bool(getattr(self.device, "is_loopback", False))
                    else f"本地识别已就绪（{model_name}，{'已预热' if reused else f'加载 {load_seconds:.1f}s'}）；正在处理已缓存的 {buffered_ms / 1000:.1f}s 音频。"
                ),
            )
            self._recognition_loop(model, get_speech_timestamps, vad_options)
        except Exception as exc:
            startup_failed = not self.ready_event.is_set()
            self.event("error", self._friendly_local_error(exc))
            self.stop_event.set()
            if startup_failed and not self.result.segments:
                self.repository.delete_course(self.result.id)
            else:
                self.repository.set_course_state(
                    self.result.id, "needs_attention", self._friendly_local_error(exc)
                )
        finally:
            if self.stream is not None:
                try:
                    self.stream.stop()
                    self.stream.close()
                except Exception:
                    pass
                self.stream = None
            self.translations.close_and_wait(timeout=45)
            self.live_summary.close(timeout=2)
            if self.ready_event.is_set():
                self._finalize()

    def _create_capture_stream(self) -> ResamplingRawInputStream | LoopbackPCMStream:
        if bool(getattr(self.device, "is_loopback", False)):
            return LoopbackPCMStream(
                str(getattr(self.device, "backend_id")),
                self.SAMPLE_RATE,
                self.BLOCK_SIZE,
                self._callback,
                self._capture_failed,
            )
        capture_rate = int(getattr(self.device, "default_samplerate", self.SAMPLE_RATE))
        return ResamplingRawInputStream(
            device_index=int(getattr(self.device, "index")),
            source_rate=capture_rate,
            target_rate=self.SAMPLE_RATE,
            target_blocksize=self.BLOCK_SIZE,
            callback=self._callback,
        )

    def _load_recognition_runtime(self) -> tuple[object, object, object, bool]:
        _prepare_nvidia_dlls()
        from faster_whisper import WhisperModel
        from faster_whisper.vad import VadOptions, get_speech_timestamps

        key = (
            self.settings.local_transcription_model,
            self.settings.local_compute_type,
        )
        with _MODEL_CACHE_LOCK:
            model = _MODEL_CACHE.get(key)
            reused = model is not None
            if model is None:
                # Keep only one GPU model alive. If the user changes models in
                # Settings, release the previous cache reference before loading
                # the new one instead of accumulating VRAM across classes.
                _MODEL_CACHE.clear()
                model = WhisperModel(
                    self.settings.local_transcription_model,
                    device="cuda",
                    compute_type=self.settings.local_compute_type,
                    num_workers=1,
                    download_root=str(Path("data/models").resolve()),
                )
                _MODEL_CACHE[key] = model
        vad_options = VadOptions(
            threshold=0.5,
            min_speech_duration_ms=160,
            min_silence_duration_ms=350,
            speech_pad_ms=220,
        )
        return model, get_speech_timestamps, vad_options, reused

    def _recognition_loop(self, model: object, vad_function: object, vad_options: object) -> None:
        buffer = bytearray()
        buffer_start_ms: int | None = None
        buffer_end_ms = 0
        hypothesis = ""
        previous_hypothesis = ""
        utterance_start_ms: int | None = None
        last_inference = 0.0
        refresh_seconds = self.settings.local_refresh_ms / 1000

        while True:
            stopping = self.stop_event.is_set()
            final_pass = stopping and self.audio_queue.empty()
            # Stop must drain buffered opening audio even if the user paused
            # before the local model finished loading.
            # Drain audio captured just before Pause as one utterance, then
            # flush it. Otherwise a queued opening sentence waits for Resume.
            pause_flush = (
                self.pause_event.is_set()
                and not stopping
                and self.audio_queue.empty()
            )
            if pause_flush and not buffer:
                time.sleep(0.08)
                continue
            try:
                data, captured_end_ms = self.audio_queue.get(
                    timeout=0.0 if final_pass else 0.1
                )
                data_duration_ms = int(
                    len(data) / (2 * self.SAMPLE_RATE) * 1000
                )
                data_start_ms = max(0, captured_end_ms - data_duration_ms)
                if buffer_start_ms is None:
                    buffer_start_ms = data_start_ms
                elif data_start_ms > buffer_end_ms + self.AUDIO_BLOCK_MS:
                    # Preserve an intentional pause that happened while the model
                    # was loading. Silence makes VAD close the earlier sentence and
                    # keeps delayed transcript timestamps aligned to wall time.
                    gap_ms = min(
                        data_start_ms - buffer_end_ms,
                        self.MAX_AUDIO_BUFFER_SECONDS * 1000,
                    )
                    buffer.extend(
                        bytes(int(self.SAMPLE_RATE * gap_ms / 1000) * 2)
                    )
                buffer.extend(data)
                buffer_end_ms = captured_end_ms
            except queue.Empty:
                data = b""
            if not buffer:
                if final_pass:
                    break
                continue
            audio = np.frombuffer(buffer, dtype=np.int16).astype(np.float32) / 32768.0
            speech = vad_function(audio, vad_options)
            if not speech:
                if pause_flush:
                    buffer.clear()
                    buffer_start_ms = None
                elif len(audio) > self.SAMPLE_RATE * 2:
                    buffer = bytearray(buffer[-self.SAMPLE_RATE * 2 :])
                    buffer_start_ms = max(0, buffer_end_ms - 2000)
                if final_pass:
                    break
                continue
            if utterance_start_ms is None:
                leading_ms = int(speech[0]["start"] / self.SAMPLE_RATE * 1000)
                utterance_start_ms = max(0, (buffer_start_ms or 0) + leading_ms)
            silence_ms = int((len(audio) - speech[-1]["end"]) / self.SAMPLE_RATE * 1000)
            duration_seconds = len(audio) / self.SAMPLE_RATE
            now = time.monotonic()
            due = now - last_inference >= refresh_seconds and duration_seconds >= 0.75
            endpoint = silence_ms >= self.ENDPOINT_SILENCE_MS
            if (
                hypothesis.rstrip().endswith((".", "?", "!"))
                and silence_ms >= self.SOFT_ENDPOINT_SILENCE_MS
            ):
                endpoint = True
            forced = duration_seconds >= self.MAX_UTTERANCE_SECONDS
            if due or endpoint or forced or final_pass or pause_flush:
                inference_started = time.monotonic()
                hypothesis = self._transcribe(model, audio)
                inference_ms = int((time.monotonic() - inference_started) * 1000)
                last_inference = time.monotonic()
                stable = common_prefix(previous_hypothesis, hypothesis)
                previous_hypothesis = hypothesis
                if hypothesis:
                    self.event("partial", ("local-current", hypothesis))
                    self.event(
                        "asr_metrics",
                        {
                            "inference_ms": inference_ms,
                            "audio_ms": int(duration_seconds * 1000),
                            "stable_words": len(stable.split()),
                            "dropped_ms": self.dropped_blocks * self.AUDIO_BLOCK_MS,
                        },
                    )
            if hypothesis and (endpoint or forced or final_pass or pause_flush):
                start_ms = (
                    utterance_start_ms
                    if utterance_start_ms is not None
                    else max(0, self._elapsed_ms() - int(duration_seconds * 1000))
                )
                end_ms = max(
                    start_ms,
                    (buffer_start_ms or 0)
                    + int(speech[-1]["end"] / self.SAMPLE_RATE * 1000),
                )
                self.translations.submit(hypothesis, start_ms, end_ms)
                self.event("partial_clear", "local-current")
                # Keep trailing audio only at a natural silence boundary. A forced
                # 10-second split can end on speech; carrying it forward duplicated words.
                trailing_samples = (
                    min(int(self.SAMPLE_RATE * 0.2), len(audio))
                    if endpoint and not forced and not pause_flush
                    else 0
                )
                buffer = bytearray(
                    (audio[-trailing_samples:] * 32768).astype(np.int16).tobytes()
                    if trailing_samples
                    else b""
                )
                buffer_start_ms = (
                    max(0, buffer_end_ms - int(trailing_samples / self.SAMPLE_RATE * 1000))
                    if trailing_samples
                    else None
                )
                hypothesis = ""
                previous_hypothesis = ""
                utterance_start_ms = None
            elif pause_flush:
                # Do not keep retrying an untranscribable paused fragment.
                buffer.clear()
                buffer_start_ms = None
                hypothesis = ""
                previous_hypothesis = ""
                utterance_start_ms = None
            if final_pass:
                break

    def _transcribe(self, model: object, audio: np.ndarray) -> str:
        segments, _ = model.transcribe(  # type: ignore[attr-defined]
            audio,
            language="en",
            task="transcribe",
            beam_size=1,
            best_of=1,
            temperature=0.0,
            # Endpointing already runs Silero VAD above, and live rendering does not
            # consume word timestamps. Avoid doing both pieces of work twice.
            word_timestamps=False,
            vad_filter=False,
            condition_on_previous_text=False,
            hotwords=self.course_context.hotword_prompt or None,
        )
        return " ".join(segment.text.strip() for segment in segments if segment.text.strip()).strip()

    def _finalize(self) -> None:
        if self.finalized:
            return
        self.finalized = True
        if not self.result.segments:
            message = "没有检测到有效英文语音。请检查音源和输入音量。"
            self.repository.set_course_state(self.result.id, "failed", message)
            self.event("error", message)
            return
        try:
            self.event("status", "字幕已安全保存，正在生成整节课笔记……")
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
            remaining = self.repository.pending_segments(self.result.id)
            if remaining:
                message = f"仍有 {len(remaining)} 句中文待补译，英文和当前笔记已保存。"
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

    def _elapsed_ms(self) -> int:
        return int((time.monotonic() - self.started_monotonic) * 1000)

    @staticmethod
    def _friendly_local_error(exc: Exception) -> str:
        text = str(exc)
        lowered = text.lower()
        if isinstance(exc, ImportError) or "faster_whisper" in lowered:
            return "本地识别组件尚未安装。请重新运行安装命令，或在设置中暂时选择云端实时识别。"
        if "cublas" in lowered or "cudnn" in lowered or "cuda" in lowered:
            return f"RTX 显卡已检测到，但 CUDA 运行库未就绪：{text}"
        if "invalid sample rate" in lowered or "-9997" in lowered:
            return "麦克风不支持当前采样率。请刷新音频设备后重试，软件会优先使用设备原生采样率。"
        return f"本地实时识别启动失败：{text}"
