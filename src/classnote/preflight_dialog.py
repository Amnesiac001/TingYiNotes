from __future__ import annotations

from collections.abc import Callable

from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from .config import Settings
from .preflight import run_quick_preflight


class PreflightDialog(QDialog):
    def __init__(
        self, navigate: Callable[[int], None], parent: QWidget,
        *, run_audio_test: Callable[[], None] | None = None,
        run_model_warmup: Callable[[], None] | None = None,
        run_api_test: Callable[[], None] | None = None,
    ) -> None:
        super().__init__(parent)
        self.navigate = navigate
        self.run_audio_test = run_audio_test
        self.run_model_warmup = run_model_warmup
        self.run_api_test = run_api_test
        self.setWindowTitle("课前检查")
        self.setMinimumWidth(650)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 20)
        layout.setSpacing(12)
        heading = QLabel("课前检查")
        heading.setObjectName("SectionTitle")
        layout.addWidget(heading)
        hint = QLabel("设备与输出会立即检查。下面三项是主动操作：音源测试需要说话或播放声音；首次模型预热会下载；API 测试可能产生少量费用。")
        hint.setWordWrap(True)
        hint.setObjectName("Muted")
        layout.addWidget(hint)
        self.rows: list[QLabel] = []
        for _ in range(4):
            label = QLabel()
            label.setWordWrap(True)
            layout.addWidget(label)
            self.rows.append(label)
        actions = QHBoxLayout()
        refresh = QPushButton("重新检查")
        refresh.clicked.connect(self.refresh)
        actions.addWidget(refresh)
        actions.addStretch()
        audio = QPushButton("测试当前音源")
        audio.clicked.connect(lambda: self._run(1, self.run_audio_test))
        model = QPushButton("下载并预热模型")
        model.clicked.connect(lambda: self._run(4, self.run_model_warmup))
        api = QPushButton("测试文本 API")
        api.setToolTip("会向当前文本服务发送一条测试请求，可能产生少量费用")
        api.clicked.connect(lambda: self._run(4, self.run_api_test))
        actions.addWidget(audio)
        actions.addWidget(model)
        actions.addWidget(api)
        layout.addLayout(actions)
        self.refresh()

    def refresh(self) -> None:
        for label, check in zip(self.rows, run_quick_preflight(Settings.load())):
            label.setText(f"{check.name} · {check.state}\n{check.detail}")

    def _go(self, index: int) -> None:
        self.accept()
        self.navigate(index)

    def _run(self, index: int, action: Callable[[], None] | None) -> None:
        self._go(index)
        if action is not None:
            action()
