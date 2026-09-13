from __future__ import annotations

import platform
import threading

import numpy as np

from .local_live import LocalLiveCourseSession


_MLX_MODEL_READY: str | None = None
_MLX_MODEL_LOCK = threading.Lock()


def note_mlx_model_used(model: str) -> None:
    """Track the one model retained by mlx-whisper's in-process holder."""
    global _MLX_MODEL_READY
    with _MLX_MODEL_LOCK:
        _MLX_MODEL_READY = model


def preload_mlx_model(model: str) -> bool:
    """Warm the selected model unless it is the most recently used model."""
    global _MLX_MODEL_READY
    if platform.machine().lower() not in {"arm64", "aarch64"}:
        raise RuntimeError("本地 MLX 识别需要 Apple 芯片（M1 或更新机型）。")
    import mlx_whisper

    with _MLX_MODEL_LOCK:
        reused = model == _MLX_MODEL_READY
        if not reused:
            # MLX keeps the most recently loaded model in-process and Hugging Face
            # keeps downloaded weights on disk for subsequent launches.
            mlx_whisper.transcribe(
                np.zeros(16000, dtype=np.float32),
                path_or_hf_repo=model,
                language="en",
                task="transcribe",
                temperature=0.0,
                verbose=None,
            )
            _MLX_MODEL_READY = model
    return reused


class EnergyVadOptions:
    """Small dependency-free endpoint detector used before MLX transcription."""

    threshold: float = 0.004
    frame_ms: int = 30
    speech_pad_ms: int = 180


def get_energy_speech_timestamps(
    audio: np.ndarray, options: EnergyVadOptions
) -> list[dict[str, int]]:
    if audio.size == 0:
        return []
    frame_size = max(1, int(16000 * options.frame_ms / 1000))
    usable = audio[: audio.size - (audio.size % frame_size)]
    if usable.size == 0:
        usable = np.pad(audio, (0, frame_size - audio.size))
    frames = usable.reshape(-1, frame_size)
    rms = np.sqrt(np.mean(np.square(frames), axis=1) + 1e-12)
    noise_floor = float(np.percentile(rms, 20)) if rms.size >= 5 else 0.0
    # Keep quiet lecturers detectable while still lifting the threshold above
    # steady room/fan noise. Never let the adaptive threshold become too high.
    threshold = max(options.threshold, min(0.008, noise_floor * 2.2))
    active = np.flatnonzero(rms >= threshold)
    if active.size == 0:
        return []
    pad = int(16000 * options.speech_pad_ms / 1000)
    start = max(0, int(active[0]) * frame_size - pad)
    end = min(audio.size, (int(active[-1]) + 1) * frame_size + pad)
    return [{"start": start, "end": end}]


class MacLocalLiveCourseSession(LocalLiveCourseSession):
    """Apple Silicon live transcription using Apple's MLX Whisper runtime."""

    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)
        self.transcription_model_name = self.settings.mac_transcription_model

    def _load_recognition_runtime(self) -> tuple[object, object, object, bool]:
        import mlx_whisper

        model = self.transcription_model_name
        reused = preload_mlx_model(model)
        return mlx_whisper, get_energy_speech_timestamps, EnergyVadOptions(), reused

    def _transcribe(self, model: object, audio: np.ndarray) -> str:
        result = model.transcribe(  # type: ignore[attr-defined]
            audio,
            path_or_hf_repo=self.transcription_model_name,
            language="en",
            task="transcribe",
            temperature=0.0,
            condition_on_previous_text=False,
            initial_prompt=self.course_context.hotword_prompt or None,
            verbose=None,
        )
        return str(result.get("text", "")).strip()  # type: ignore[attr-defined]

    @staticmethod
    def _friendly_local_error(exc: Exception) -> str:
        text = str(exc)
        lowered = text.lower()
        if isinstance(exc, ImportError) or "mlx" in lowered and "no module" in lowered:
            return "Mac 本地识别组件尚未安装。请运行“安装听译记-macOS.command”后重试。"
        if "permission" in lowered or "not permitted" in lowered or "-9986" in lowered:
            return "没有麦克风权限。请到“系统设置 → 隐私与安全性 → 麦克风”允许听译记或终端访问。"
        if "invalid sample rate" in lowered or "-9997" in lowered:
            return "麦克风不支持当前采样率。请刷新音频设备后重试。"
        return f"Mac 本地实时识别启动失败：{text}"
