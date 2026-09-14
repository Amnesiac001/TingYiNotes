r"""Measure the Windows local ASR path on an explicitly chosen classroom WAV.

Run from a source checkout:
    .venv\Scripts\python.exe tools\benchmark_local_asr.py sample.wav

The WAV must be mono, 16-bit PCM, 16 kHz (the app's temporary-audio format).
Only local model inference is used; audio is not uploaded or copied.
"""

from __future__ import annotations

import argparse
from itertools import chain
import math
from pathlib import Path
import re
import sys
import time
import wave

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from classnote.config import Settings
from classnote.local_live import preload_local_model, transcribe_local_audio, warmup_local_model


SAMPLE_RATE = 16000


def read_windows(path: Path, window_seconds: float):
    """Yield float32 PCM windows without retaining a whole lecture in memory."""
    if not math.isfinite(window_seconds) or window_seconds <= 0:
        raise ValueError("窗口时长必须是大于 0 的有限秒数。")
    try:
        reader = wave.open(str(path), "rb")
    except (OSError, wave.Error) as exc:
        raise ValueError(f"无法读取 WAV：{exc}") from exc
    with reader:
        if (reader.getnchannels(), reader.getsampwidth(), reader.getframerate(), reader.getcomptype()) != (
            1, 2, SAMPLE_RATE, "NONE"
        ):
            raise ValueError("请先导出为单声道、16-bit PCM、16 kHz 的 WAV。")
        frame_count = max(1, round(window_seconds * SAMPLE_RATE))
        while raw := reader.readframes(frame_count):
            samples = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
            yield samples


def word_error_rate(reference: str, hypothesis: str) -> float | None:
    """Case/punctuation-insensitive English WER; None for an empty reference."""
    expected = re.findall(r"[a-z0-9]+(?:'[a-z0-9]+)?", reference.lower())
    actual = re.findall(r"[a-z0-9]+(?:'[a-z0-9]+)?", hypothesis.lower())
    if not expected:
        return None
    previous = list(range(len(actual) + 1))
    for row, expected_word in enumerate(expected, 1):
        current = [row]
        for column, actual_word in enumerate(actual, 1):
            current.append(min(
                current[-1] + 1,
                previous[column] + 1,
                previous[column - 1] + (expected_word != actual_word),
            ))
        previous = current
    return previous[-1] / len(expected)


def percentile(values: list[float], percent: float) -> float:
    if not values:
        raise ValueError("没有可统计的音频窗口。")
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, max(0, math.ceil(percent * len(ordered)) - 1))]


def benchmark_windows(model: object, windows, *, clock=time.perf_counter) -> dict:
    durations: list[float] = []
    inference_times: list[float] = []
    transcript: list[str] = []
    for samples in windows:
        if not len(samples):
            continue
        started = clock()
        text = transcribe_local_audio(model, samples)
        inference_times.append(clock() - started)
        durations.append(len(samples) / SAMPLE_RATE)
        if text:
            transcript.append(text)
    if not durations:
        raise ValueError("WAV 没有音频帧。")
    audio_seconds = sum(durations)
    inference_seconds = sum(inference_times)
    return {
        "windows": len(durations),
        "audio_seconds": audio_seconds,
        "inference_seconds": inference_seconds,
        "rtf": inference_seconds / audio_seconds,
        "window_p50_seconds": percentile(inference_times, 0.50),
        "window_p95_seconds": percentile(inference_times, 0.95),
        "slow_windows": sum(elapsed > duration for elapsed, duration in zip(inference_times, durations)),
        "transcript": " ".join(transcript),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="用本地课堂 WAV 测量 RTX 英文转写速度")
    parser.add_argument("audio", type=Path, help="单声道 16-bit PCM、16 kHz WAV")
    parser.add_argument("--reference", type=Path, help="可选：对应英文原文 .txt，计算近似词错率")
    parser.add_argument("--window-seconds", type=float, default=10.0, help="连续片段长度，默认 10 秒")
    parser.add_argument("--show-text", action="store_true", help="在终端打印识别文本")
    args = parser.parse_args(argv)
    try:
        windows = read_windows(args.audio, args.window_seconds)
        first = next(windows, None)
        if first is None:
            raise ValueError("WAV 没有音频帧。")
        settings = Settings.load()
        print(f"加载本地模型 {settings.local_transcription_model}；若尚未缓存，首次运行可能下载模型……", flush=True)
        load_started = time.perf_counter()
        model, reused = preload_local_model(
            settings.local_transcription_model,
            settings.local_compute_type,
            settings.database_path.parent / "models",
        )
        load_seconds = time.perf_counter() - load_started
        warmup_started = time.perf_counter()
        warmup_local_model(model)
        warmup_seconds = time.perf_counter() - warmup_started
        result = benchmark_windows(model, chain((first,), windows))
        reference = args.reference.read_text(encoding="utf-8") if args.reference else None
    except (OSError, ValueError, RuntimeError) as exc:
        parser.exit(2, f"基准测试失败：{exc}\n")
    print(f"模型加载：{load_seconds:.2f}s{'（缓存实例）' if reused else ''}；预热：{warmup_seconds:.2f}s")
    print(f"音频：{result['audio_seconds']:.2f}s / {result['windows']} 段；推理：{result['inference_seconds']:.2f}s")
    print(f"RTF：{result['rtf']:.3f}（<1 表示整体快于播放速度）")
    print(f"单段耗时 P50/P95：{result['window_p50_seconds']:.2f}s / {result['window_p95_seconds']:.2f}s")
    print(f"超过该段音频时长的窗口：{result['slow_windows']}/{result['windows']}")
    if reference is not None:
        wer = word_error_rate(reference, result["transcript"])
        print(f"近似 WER：{wer:.1%}" if wer is not None else "参考文本为空，无法计算 WER。")
        print("注意：固定窗口可能截断句子；WER 也受参考文本标注方式影响。")
    if args.show_text:
        print(f"识别文本：\n{result['transcript']}")
    print("此结果仅测本地 ASR，不含麦克风采集、VAD、翻译和摘要延迟。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
