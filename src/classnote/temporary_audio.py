from __future__ import annotations

import os
import queue
import threading
import time
import wave
from pathlib import Path
from uuid import UUID
from typing import Callable

from .storage import CourseRepository


class TemporaryAudioRecorder:
    """Best-effort WAV backup that never blocks the audio capture callback."""

    def __init__(
        self, database_path: Path, course_id: str, sample_rate: int,
        warning: Callable[[str], None],
    ) -> None:
        self.course_id = str(UUID(course_id))
        self.path = database_path.parent / "temporary-audio" / f"{self.course_id}.wav"
        self.sample_rate = sample_rate
        self.warning = warning
        self.queue: queue.Queue[bytes | None] = queue.Queue(maxsize=3000)
        self.thread: threading.Thread | None = None
        self._failed = False
        self._warned = False
        self._closed = False

    def start(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.thread = threading.Thread(target=self._write, name="classnote-audio-backup", daemon=True)
        self.thread.start()

    def submit(self, pcm: bytes) -> None:
        if self._closed or self._failed or not pcm:
            return
        try:
            self.queue.put_nowait(bytes(pcm))
        except queue.Full:
            if not self._warned:
                self._warned = True
                self.warning("本地临时音频写入跟不上录音，部分备份音频可能缺失；课堂字幕仍会继续。")

    def finish(self, *, delete: bool) -> Path | None:
        self._closed = True
        if self.thread is not None:
            try:
                self.queue.put(None, timeout=2)
            except queue.Full:
                self.warning("本地临时音频尚未完全写入；录音文件会保留，请稍后检查。")
            self.thread.join(timeout=10)
            if self.thread.is_alive():
                self.warning("本地临时音频仍在写入，本次不会自动删除。")
                return self.path
        if delete and not self._failed:
            try:
                self.path.unlink(missing_ok=True)
            except OSError as exc:
                self.warning(f"课堂已完成，但临时音频删除失败：{exc}")
                return self.path
            return None
        return self.path if self.path.exists() else None

    def _write(self) -> None:
        try:
            # writeframes updates the WAV length after every batch. Even if the
            # process crashes, the last flushed portion remains playable.
            with self.path.open("wb", buffering=0) as handle:
                with wave.open(handle, "wb") as wav:
                    wav.setnchannels(1)
                    wav.setsampwidth(2)
                    wav.setframerate(self.sample_rate)
                    pending = bytearray()
                    last_sync = time.monotonic()
                    while True:
                        try:
                            block = self.queue.get(timeout=0.5)
                        except queue.Empty:
                            block = b""
                        if block is None:
                            break
                        pending.extend(block)
                        if len(pending) >= self.sample_rate * 2 or (pending and not block):
                            wav.writeframes(bytes(pending))
                            pending.clear()
                        if time.monotonic() - last_sync >= 5:
                            os.fsync(handle.fileno())
                            last_sync = time.monotonic()
                    if pending:
                        wav.writeframes(bytes(pending))
                    os.fsync(handle.fileno())
        except Exception as exc:
            self._failed = True
            self.warning(f"本地临时音频写入失败：{exc}；课堂字幕仍会继续。")


def start_temporary_audio(
    database_path: Path, course_id: str, sample_rate: int,
    warning: Callable[[str], None],
) -> TemporaryAudioRecorder | None:
    try:
        recorder = TemporaryAudioRecorder(database_path, course_id, sample_rate, warning)
        recorder.start()
        return recorder
    except Exception as exc:
        warning(f"无法开启本地临时音频：{exc}；课堂字幕仍会继续。")
        return None


def finish_temporary_audio(
    recorder: TemporaryAudioRecorder | None, repository: CourseRepository,
    course_id: str, warning: Callable[[str], None],
) -> None:
    if recorder is None:
        return
    try:
        row = repository.get_course(course_id)
        delete = row is None or str(row["status"]) == "completed"
    except Exception as exc:
        delete = False
        warning(f"无法确认课堂是否已完成，临时音频将保留：{exc}")
    try:
        retained = recorder.finish(delete=delete)
        if retained is not None:
            warning(f"本次临时音频保留在：{retained.resolve()}。确认不再需要后可手动删除。")
    except Exception as exc:
        warning(f"临时音频收尾失败，请检查 {recorder.path}：{exc}")
