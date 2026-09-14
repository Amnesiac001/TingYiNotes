from __future__ import annotations

import wave
from pathlib import Path

import numpy as np

from .recovery_inventory import course_temporary_audio_path
from .storage import CourseRepository


def review_reasons(row: dict[str, object]) -> list[str]:
    """Show observable warnings, not an invented speech-recognition confidence."""
    reasons: list[str] = []
    if not str(row.get("original_text") or "").strip():
        reasons.append("英文为空")
    if str(row.get("translation_status") or "completed") != "completed" or not str(
        row.get("translated_text") or ""
    ).strip():
        reasons.append("中文待补译")
    start, end = int(row["start_ms"]), int(row["end_ms"])
    if end <= start:
        reasons.append("时间戳异常")
    elif end - start < 500 and len(str(row.get("original_text") or "").split()) >= 5:
        reasons.append("语速与时间戳不匹配")
    return reasons


def load_review_clip(
    repository: CourseRepository, course_id: str, start_ms: int, end_ms: int,
    *, padding_ms: int = 750,
) -> tuple[np.ndarray, int]:
    """Read a bounded local PCM clip around a saved sentence; never upload it."""
    path: Path | None = course_temporary_audio_path(repository, course_id)
    if path is None:
        raise FileNotFoundError("这堂课没有可回听的本地录音。请在设置中提前开启音频保留。")
    with wave.open(str(path), "rb") as wav:
        if wav.getnchannels() != 1 or wav.getsampwidth() != 2 or wav.getcomptype() != "NONE":
            raise ValueError("回听只支持本软件录制的单声道 16 位 WAV。")
        rate = wav.getframerate()
        first = min(wav.getnframes(), max(0, start_ms - padding_ms) * rate // 1000)
        last = min(
            wav.getnframes(),
            max(start_ms + 1000, end_ms + padding_ms) * rate // 1000,
            first + 20 * rate,
        )
        wav.setpos(first)
        samples = np.frombuffer(wav.readframes(max(0, last - first)), dtype="<i2").copy()
    if samples.size == 0:
        raise ValueError("所选句子的时间位置超出了本地录音长度。")
    return samples, rate
