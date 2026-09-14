"""Entry point for the standalone Windows distribution."""

import json
import os
import sys
import traceback
from pathlib import Path

from classnote.config import application_data_dir


def run_self_check() -> dict[str, object]:
    """Exercise bundled imports and local backends without an API or model download."""
    from PySide6.QtCore import qVersion

    from classnote.config import Settings, verify_output_directory
    from classnote.live import list_input_devices
    from classnote.local_live import _prepare_nvidia_dlls

    _prepare_nvidia_dlls()
    import ctranslate2

    cuda_devices = ctranslate2.get_cuda_device_count()
    compute_types = sorted(ctranslate2.get_supported_compute_types("cuda")) if cuda_devices else []
    settings = Settings.load()
    settings.ensure_directories()
    verify_output_directory(settings.export_dir)
    inputs = list_input_devices()
    report: dict[str, object] = {
        "qt_version": qVersion(),
        "cuda_devices": cuda_devices,
        "cuda_compute_types": compute_types,
        "input_devices": len(inputs),
        "storage_ready": True,
        "model_inference_ready": False,
    }
    model_path = os.environ.get("TINGYI_SELF_CHECK_MODEL_PATH")
    if model_path:
        # An explicit local directory avoids a surprise network download.
        local_model = Path(model_path).resolve()
        if not all((local_model / name).is_file() for name in ("config.json", "model.bin", "tokenizer.json")):
            raise FileNotFoundError(f"Offline model files are incomplete: {local_model}")
        if not cuda_devices or "float16" not in compute_types:
            raise RuntimeError("CUDA FP16 is unavailable for the offline model check.")
        import numpy as np
        from faster_whisper import WhisperModel

        model = WhisperModel(str(local_model), device="cuda", compute_type="float16", local_files_only=True)
        segments, _ = model.transcribe(np.zeros(16000, dtype=np.float32), language="en", beam_size=1)
        list(segments)  # The iterator performs the actual CUDA inference.
        report["model_inference_ready"] = True
    return report


def run_release_smoke() -> dict[str, object]:
    """Check the frozen offline demo and its persisted output without an API call."""
    from classnote.app import process_course
    from classnote.config import Settings
    from classnote.storage import CourseRepository

    result, exported = process_course(
        Path("offline-demo"), "听译记发布验收", "计算机网络", demo=True,
    )
    saved = CourseRepository(Settings.load().database_path).get_course(result.id)
    markdown = exported.read_text(encoding="utf-8")
    if (
        saved is None
        or saved["status"] != "completed"
        or saved["source_path"]
        or len(result.segments) != 2
        or not result.segments[0].translated_text
        or not result.segments[1].translated_text
        or result.segments[0].translated_text == result.segments[1].translated_text
        or markdown.count("今天我们将讨论") != 1
    ):
        raise RuntimeError("离线演示未能完整保存课程和笔记。")
    return {"demo_course_saved": True, "demo_export_ready": True, "demo_segments": 2}


if getattr(sys, "frozen", False):
    app_data = application_data_dir()
    app_data.mkdir(parents=True, exist_ok=True)
    os.chdir(app_data)

if __name__ == "__main__":
    check_mode = "--self-check" in sys.argv
    release_smoke_mode = "--release-smoke" in sys.argv
    application_data_dir().mkdir(parents=True, exist_ok=True)
    diagnostic_name = (
        "self-check.json" if check_mode else
        "release-smoke.json" if release_smoke_mode else "startup.log"
    )
    diagnostic = application_data_dir() / diagnostic_name
    try:
        if check_mode:
            diagnostic.write_text(json.dumps(run_self_check(), ensure_ascii=False, indent=2), encoding="utf-8")
        elif release_smoke_mode:
            diagnostic.write_text(json.dumps(run_release_smoke(), ensure_ascii=False, indent=2), encoding="utf-8")
        else:
            diagnostic.write_text("Loading interface...\n", encoding="utf-8")
            from classnote.qt_gui import main  # noqa: E402

            diagnostic.write_text("Interface loaded; opening window...\n", encoding="utf-8")
            main()
    except Exception:
        failure = traceback.format_exc()
        diagnostic.write_text(
            json.dumps({"error": failure}, ensure_ascii=False, indent=2)
            if check_mode or release_smoke_mode else failure,
            encoding="utf-8",
        )
        if sys.platform == "win32" and not check_mode and not release_smoke_mode:
            import ctypes

            ctypes.windll.user32.MessageBoxW(
                None,
                f"听译记启动失败。详细错误已保存到：\n{diagnostic}",
                "听译记",
                0x10,
            )
        if check_mode or release_smoke_mode:
            raise SystemExit(1)
        raise
