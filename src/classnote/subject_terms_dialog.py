from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QMessageBox, QPushButton, QVBoxLayout, QWidget,
)

from .storage import CourseRepository


class SubjectTermsDialog(QDialog):
    """Manage the small local glossary used for one course subject."""

    def __init__(self, repository: CourseRepository, subject: str, parent: QWidget) -> None:
        super().__init__(parent)
        self.repository = repository
        self.subject = " ".join(subject.split()) or "通用课程"
        self.setWindowTitle(f"术语管理 · {self.subject}")
        self.setMinimumSize(560, 480)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(10)
        title = QLabel(f"{self.subject} · 术语")
        title.setObjectName("SectionTitle")
        hint = QLabel("仅保存在本机；同一课程领域的实时课堂和文件处理会自动使用。课件提取词不会自动保存。")
        hint.setObjectName("Muted")
        hint.setWordWrap(True)
        layout.addWidget(title)
        layout.addWidget(hint)
        self.list = QListWidget()
        self.list.currentItemChanged.connect(self._select)
        layout.addWidget(self.list, 1)
        fields = QHBoxLayout()
        self.english = QLineEdit()
        self.english.setPlaceholderText("英文术语，例如 Congestion Window")
        self.chinese = QLineEdit()
        self.chinese.setPlaceholderText("中文释义，例如 拥塞窗口")
        fields.addWidget(self.english, 1)
        fields.addWidget(self.chinese, 1)
        layout.addLayout(fields)
        self.status = QLabel("选择已有术语可修改；输入新英文可新增。")
        self.status.setObjectName("Muted")
        layout.addWidget(self.status)
        actions = QHBoxLayout()
        new_button = QPushButton("新增")
        new_button.clicked.connect(self.new_term)
        self.delete_button = QPushButton("删除选中")
        self.delete_button.clicked.connect(self.delete_selected)
        self.save_button = QPushButton("保存术语")
        self.save_button.setObjectName("Primary")
        self.save_button.clicked.connect(self.save_term)
        close_button = QPushButton("完成")
        close_button.clicked.connect(self.accept)
        actions.addWidget(new_button)
        actions.addWidget(self.delete_button)
        actions.addStretch()
        actions.addWidget(close_button)
        actions.addWidget(self.save_button)
        layout.addLayout(actions)
        self._reload()

    def _reload(self, select: str = "") -> None:
        self.list.blockSignals(True)
        self.list.clear()
        for english, chinese in self.repository.get_subject_terms(self.subject).items():
            item = QListWidgetItem(f"{english}  →  {chinese}")
            item.setData(Qt.ItemDataRole.UserRole, english)
            self.list.addItem(item)
            if english.casefold() == select.casefold():
                self.list.setCurrentItem(item)
        self.list.blockSignals(False)
        self.delete_button.setEnabled(self.list.currentItem() is not None)

    def _select(self, current: QListWidgetItem | None, _previous: QListWidgetItem | None) -> None:
        self.delete_button.setEnabled(current is not None)
        if current is None:
            return
        english = str(current.data(Qt.ItemDataRole.UserRole))
        self.english.setText(english)
        self.chinese.setText(self.repository.get_subject_terms(self.subject)[english])

    def new_term(self) -> None:
        self.list.clearSelection()
        self.list.setCurrentRow(-1)
        self.english.clear()
        self.chinese.clear()
        self.english.setFocus()

    def save_term(self) -> None:
        english = self.english.text().strip()
        chinese = self.chinese.text().strip()
        selected = self.list.currentItem()
        previous = str(selected.data(Qt.ItemDataRole.UserRole)) if selected else ""
        try:
            self.repository.save_subject_term(self.subject, english, chinese)
            if previous and previous.casefold() != english.casefold():
                self.repository.delete_subject_term(self.subject, previous)
        except Exception as exc:
            self.status.setText(str(exc))
            return
        self._reload(english)
        self.status.setText(f"已保存 · {english}")

    def delete_selected(self) -> None:
        item = self.list.currentItem()
        if item is None:
            return
        english = str(item.data(Qt.ItemDataRole.UserRole))
        answer = QMessageBox.question(
            self, "删除术语？", f"从“{self.subject}”中删除 {english}？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.repository.delete_subject_term(self.subject, english)
        self._reload()
        self.english.clear()
        self.chinese.clear()
        self.status.setText(f"已删除 · {english}")
