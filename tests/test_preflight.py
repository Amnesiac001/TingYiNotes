from __future__ import annotations

from dataclasses import replace

from classnote.config import Settings
from classnote.live import AudioDevice
from classnote.preflight import run_quick_preflight


def test_preflight_checks_without_calling_model_or_api(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        "classnote.preflight.list_input_devices",
        lambda: [AudioDevice(0, "mic", 1, 16000)],
    )
    settings = replace(
        Settings.load(), export_dir=tmp_path / "notes", live_mode="local",
        text_api_key=None,
    )
    checks = run_quick_preflight(settings)
    assert [(check.name, check.state) for check in checks] == [
        ("录音设备", "通过"), ("英文识别", "待验证"),
        ("中文翻译", "需处理"), ("笔记输出", "通过"),
    ]
    assert (tmp_path / "notes").is_dir()
