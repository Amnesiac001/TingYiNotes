import sys
from types import SimpleNamespace

import classnote.config as config
import classnote.live as live
import classnote.local_live as local_live
import windows.launcher as launcher


def test_bundled_self_check_prepares_cuda_and_probes_storage(monkeypatch, tmp_path) -> None:
    order: list[str] = []
    monkeypatch.delenv("TINGYI_SELF_CHECK_MODEL_PATH", raising=False)
    monkeypatch.setattr(local_live, "_prepare_nvidia_dlls", lambda: order.append("dlls"))
    monkeypatch.setitem(
        sys.modules,
        "ctranslate2",
        SimpleNamespace(
            get_cuda_device_count=lambda: order.append("cuda") or 1,
            get_supported_compute_types=lambda _device: {"float16", "float32"},
        ),
    )
    monkeypatch.setattr(
        config.Settings,
        "load",
        classmethod(lambda _cls: SimpleNamespace(
            export_dir=tmp_path,
            ensure_directories=lambda: order.append("storage"),
        )),
    )
    monkeypatch.setattr(config, "verify_output_directory", lambda path: order.append("export") or path)
    monkeypatch.setattr(live, "list_input_devices", lambda: [object(), object()])

    report = launcher.run_self_check()

    assert order == ["dlls", "cuda", "storage", "export"]
    assert report["qt_version"]
    assert report["cuda_devices"] == 1
    assert report["cuda_compute_types"] == ["float16", "float32"]
    assert report["input_devices"] == 2
    assert report["storage_ready"] is True
    assert report["model_inference_ready"] is False

    model_dir = tmp_path / "offline-model"
    model_dir.mkdir()
    for name in ("config.json", "model.bin", "tokenizer.json"):
        (model_dir / name).touch()
    monkeypatch.setenv("TINGYI_SELF_CHECK_MODEL_PATH", str(model_dir))

    class FakeWhisperModel:
        def __init__(self, path, **kwargs):
            assert path == str(model_dir)
            assert kwargs == {"device": "cuda", "compute_type": "float16", "local_files_only": True}
            order.append("model")

        def transcribe(self, audio, **kwargs):
            assert audio.shape == (16000,)
            assert kwargs == {"language": "en", "beam_size": 1}

            def segments():
                order.append("inference")
                yield object()

            return segments(), None

    monkeypatch.setitem(sys.modules, "faster_whisper", SimpleNamespace(WhisperModel=FakeWhisperModel))
    report = launcher.run_self_check()
    assert report["model_inference_ready"] is True
    assert order[-2:] == ["model", "inference"]
