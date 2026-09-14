from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QWidget

from classnote.preflight_dialog import PreflightDialog


def test_preflight_dialog_routes_to_existing_tests(monkeypatch, tmp_path) -> None:
    app = QApplication.instance() or QApplication([])
    monkeypatch.setenv("CLASSNOTE_EXPORT_DIR", str(tmp_path / "notes"))
    monkeypatch.setattr("classnote.preflight.list_input_devices", lambda: [])
    targets: list[int] = []
    parent = QWidget()
    dialog = PreflightDialog(targets.append, parent)
    assert "需处理" in dialog.rows[0].text()
    assert "可写" in dialog.rows[3].text()
    dialog._go(1)
    assert targets == [1]
    dialog = PreflightDialog(
        targets.append, parent,
        run_audio_test=lambda: targets.append(10),
        run_model_warmup=lambda: targets.append(40),
        run_api_test=lambda: targets.append(41),
    )
    dialog._run(4, dialog.run_api_test)
    assert targets[-2:] == [4, 41]
    parent.close()
    app.processEvents()
