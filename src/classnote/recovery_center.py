from __future__ import annotations

from pathlib import Path
from typing import Callable

from PySide6.QtWidgets import (
    QDialog, QHBoxLayout, QLabel, QListWidget, QListWidgetItem,
    QPushButton, QVBoxLayout, QWidget,
)

from .recovery_inventory import RecoveryCandidate, list_recovery_candidates
from .storage import CourseRepository


class RecoveryCenterDialog(QDialog):
    """Choose between resuming saved text and reprocessing retained audio."""

    def __init__(
        self, repository: CourseRepository,
        resume_course: Callable[[str], None],
        open_audio: Callable[[Path, str, str], None],
        parent: QWidget,
    ) -> None:
        super().__init__(parent)
        self.repository = repository
        self.resume_course = resume_course
        self.open_audio = open_audio
        self.candidates: list[RecoveryCandidate] = []
        self.setWindowTitle("恢复未完成课堂")
        self.setMinimumSize(660, 510)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(11)
        title = QLabel("继续上次课堂")
        title.setObjectName("SectionTitle")
        hint = QLabel("已保存的英文字幕可直接补译整理；临时 WAV 可以作为一条新课程重新处理。进入恢复中心不会自动调用 API。")
        hint.setObjectName("Muted")
        hint.setWordWrap(True)
        layout.addWidget(title)
        layout.addWidget(hint)
        self.list = QListWidget()
        self.list.currentRowChanged.connect(self._selection_changed)
        layout.addWidget(self.list, 1)
        self.detail = QLabel()
        self.detail.setWordWrap(True)
        self.detail.setObjectName("Muted")
        layout.addWidget(self.detail)
        actions = QHBoxLayout()
        self.refresh_button = QPushButton("刷新")
        self.refresh_button.clicked.connect(self.reload)
        self.audio_button = QPushButton("用录音重新处理")
        self.audio_button.setToolTip("进入文件转写页，确认后创建一条新课程；不会覆盖已有字幕")
        self.audio_button.clicked.connect(self._open_audio)
        self.resume_button = QPushButton("补译并整理已有字幕")
        self.resume_button.setObjectName("Primary")
        self.resume_button.clicked.connect(self._resume)
        close_button = QPushButton("关闭")
        close_button.clicked.connect(self.reject)
        actions.addWidget(self.refresh_button)
        actions.addStretch()
        actions.addWidget(self.audio_button)
        actions.addWidget(self.resume_button)
        actions.addWidget(close_button)
        layout.addLayout(actions)
        self.reload()

    def reload(self) -> None:
        self.candidates = list_recovery_candidates(self.repository)
        self.list.clear()
        for candidate in self.candidates:
            status = {
                "interrupted": "上次中断", "needs_attention": "需要处理",
                "failed": "未完成", "audio_only": "仅有录音",
            }.get(candidate.status, candidate.status)
            detail = f"{candidate.segment_count} 句英文"
            if candidate.pending_count:
                detail += f" · {candidate.pending_count} 句待补译"
            if candidate.audio_path is not None:
                detail += " · 有本机录音"
            item = QListWidgetItem(f"{candidate.title}  ·  {status}\n{candidate.subject} · {detail}")
            self.list.addItem(item)
        if self.candidates:
            self.list.setCurrentRow(0)
        else:
            self._selection_changed(-1)

    def _selection_changed(self, index: int) -> None:
        candidate = self.candidates[index] if 0 <= index < len(self.candidates) else None
        self.resume_button.setEnabled(bool(candidate and candidate.can_resume))
        self.audio_button.setEnabled(bool(candidate and candidate.audio_path is not None))
        if candidate is None:
            self.detail.setText("没有可继续处理的课堂。已完成课程不会出现在这里。")
        elif candidate.audio_path is not None:
            self.detail.setText(
                f"本机录音：{candidate.audio_path.resolve()}\n"
                "选择录音会进入文件转写页，确认后创建新记录；原课程和原字幕不会被覆盖。"
            )
        else:
            self.detail.setText("英文字幕已保存在课程库，可继续补译并重新整理，无需重新转写音频。")

    def _resume(self) -> None:
        index = self.list.currentRow()
        if not (0 <= index < len(self.candidates)):
            return
        candidate = self.candidates[index]
        if not candidate.can_resume or candidate.course_id is None:
            return
        self.accept()
        self.resume_course(candidate.course_id)

    def _open_audio(self) -> None:
        index = self.list.currentRow()
        if not (0 <= index < len(self.candidates)):
            return
        candidate = self.candidates[index]
        path = candidate.audio_path
        if path is None or not path.is_file():
            self.detail.setText("录音文件已被移动或删除，请刷新列表。")
            self.audio_button.setEnabled(False)
            return
        self.accept()
        self.open_audio(path, candidate.title, candidate.subject)
