from __future__ import annotations

from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .config import Settings
from .editing import correct_course_segment, undo_course_segment_correction
from .storage import CourseRepository


def _stamp(milliseconds: int) -> str:
    seconds = max(0, int(milliseconds) // 1000)
    if seconds >= 3600:
        return f"{seconds // 3600}:{(seconds % 3600) // 60:02d}:{seconds % 60:02d}"
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


class SegmentEditorDialog(QDialog):
    """Search and correct one saved classroom sentence at a time."""

    def __init__(
        self, repository: CourseRepository, course_id: str, title: str, parent: QWidget,
        settings: Settings | None = None,
    ) -> None:
        super().__init__(parent)
        self.repository = repository
        self.course_id = course_id
        self.settings = settings or Settings.load()
        self.saved_any = False
        self._loading = False
        self.rows = {
            str(row["id"]): dict(row)
            for row in repository.get_course_segments(course_id)
        }
        self.current_id = ""
        self.setWindowTitle(f"校对课堂记录 · {title}")
        self.setMinimumSize(920, 550)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(12)
        heading = QLabel("逐句校对")
        heading.setObjectName("SectionTitle")
        hint = QLabel("修改英文时，请同步校对中文，或清空中文以便稍后补译。保存后可在课程库重新整理笔记。")
        hint.setObjectName("Muted")
        hint.setWordWrap(True)
        layout.addWidget(heading)
        layout.addWidget(hint)
        body = QHBoxLayout()
        left = QVBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("搜索时间、英文或中文……")
        self.search.textChanged.connect(self._filter_rows)
        self.list = QListWidget()
        self.list.setMinimumWidth(330)
        self.list.currentItemChanged.connect(self._select_item)
        left.addWidget(self.search)
        left.addWidget(self.list, 1)
        body.addLayout(left, 2)
        editors = QVBoxLayout()
        self.location = QLabel("请选择一句记录")
        self.location.setObjectName("Muted")
        english_label = QLabel("英文原文")
        english_label.setObjectName("PaneTitle")
        self.english = QPlainTextEdit()
        self.english.setPlaceholderText("这句英文不能为空")
        chinese_label = QLabel("中文翻译")
        chinese_label.setObjectName("PaneTitle")
        self.chinese = QPlainTextEdit()
        self.chinese.setPlaceholderText("可以清空，稍后在课程库补译")
        self.keep_chinese = QCheckBox("我已核对：原中文仍准确，保留它")
        self.keep_chinese.hide()
        self.english.textChanged.connect(self._update_save_state)
        self.chinese.textChanged.connect(self._update_save_state)
        self.keep_chinese.toggled.connect(self._update_save_state)
        editors.addWidget(self.location)
        editors.addWidget(english_label)
        editors.addWidget(self.english, 1)
        editors.addWidget(chinese_label)
        editors.addWidget(self.chinese, 1)
        editors.addWidget(self.keep_chinese)
        body.addLayout(editors, 3)
        layout.addLayout(body, 1)
        self.status = QLabel("修改只保存到你的电脑，不会立即调用 API。")
        self.status.setObjectName("Muted")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        actions = QHBoxLayout()
        actions.addStretch()
        self.undo_button = QPushButton("撤销上次校对")
        self.undo_button.setEnabled(False)
        self.undo_button.clicked.connect(self.undo_current)
        close_button = QPushButton("关闭")
        close_button.clicked.connect(self.close)
        self.save_button = QPushButton("保存这句")
        self.save_button.setObjectName("Primary")
        self.save_button.setEnabled(False)
        self.save_button.clicked.connect(self.save_current)
        actions.addWidget(self.undo_button)
        actions.addWidget(close_button)
        actions.addWidget(self.save_button)
        layout.addLayout(actions)
        for row in self.rows.values():
            item = QListWidgetItem(self._item_text(row))
            item.setData(Qt.ItemDataRole.UserRole, str(row["id"]))
            item.setSizeHint(QSize(0, 62))
            self.list.addItem(item)
        if self.list.count():
            self.list.setCurrentRow(0)

    @staticmethod
    def _item_text(row: dict[str, object]) -> str:
        time_text = _stamp(int(row["start_ms"]))
        english = " ".join(str(row["original_text"]).split())[:65]
        chinese = " ".join(str(row["translated_text"]).split())[:45] or "待补译"
        return f"{time_text} · {english}\n{chinese}"

    def _filter_rows(self, query: str) -> None:
        needle = query.strip().casefold()
        for index in range(self.list.count()):
            item = self.list.item(index)
            row = self.rows[str(item.data(Qt.ItemDataRole.UserRole))]
            searchable = (
                f"{_stamp(int(row['start_ms']))} {_stamp(int(row['end_ms']))} "
                f"{row['original_text']} {row['translated_text']}"
            ).casefold()
            item.setHidden(bool(needle and needle not in searchable))

    def _is_dirty(self) -> bool:
        row = self.rows.get(self.current_id)
        return bool(row) and (
            self.english.toPlainText().strip(), self.chinese.toPlainText().strip()
        ) != (str(row["original_text"]), str(row["translated_text"]))

    def _update_save_state(self) -> None:
        if self._loading:
            return
        row = self.rows.get(self.current_id)
        needs_confirmation = bool(row) and (
            self.english.toPlainText().strip() != str(row["original_text"])
            and self.chinese.toPlainText().strip() == str(row["translated_text"])
            and bool(str(row["translated_text"]))
        )
        self.keep_chinese.setVisible(needs_confirmation)
        if not needs_confirmation:
            self.keep_chinese.setChecked(False)
        self.save_button.setEnabled(
            self._is_dirty() and (not needs_confirmation or self.keep_chinese.isChecked())
        )

    def _confirm_discard(self, message: str) -> bool:
        answer = QMessageBox.question(
            self, "放弃未保存的修改？", message,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        return answer == QMessageBox.StandardButton.Yes

    def _select_item(self, current: QListWidgetItem | None, previous: QListWidgetItem | None) -> None:
        if self._loading:
            return
        if self._is_dirty() and not self._confirm_discard("切换句子会丢弃尚未保存的文字。"):
            self._loading = True
            try:
                self.list.setCurrentItem(previous)
            finally:
                self._loading = False
            return
        self.current_id = str(current.data(Qt.ItemDataRole.UserRole)) if current else ""
        row = self.rows.get(self.current_id)
        self._loading = True
        try:
            self.english.setPlainText(str(row["original_text"]) if row else "")
            self.chinese.setPlainText(str(row["translated_text"]) if row else "")
            self.location.setText(
                f"{_stamp(int(row['start_ms']))}–{_stamp(int(row['end_ms']))}"
                if row else "请选择一句记录"
            )
            self.save_button.setEnabled(False)
            self.keep_chinese.setChecked(False)
            self.keep_chinese.hide()
            self.undo_button.setEnabled(
                bool(row) and self.repository.has_segment_corrections(self.current_id)
            )
        finally:
            self._loading = False

    def save_current(self) -> None:
        row = self.rows.get(self.current_id)
        if row is None:
            return
        try:
            outcome = correct_course_segment(
                self.repository, self.course_id, self.current_id,
                self.english.toPlainText(), self.chinese.toPlainText(),
                str(row["original_text"]), str(row["translated_text"]),
                settings=self.settings,
                keep_translation_confirmed=self.keep_chinese.isChecked(),
            )
        except Exception as exc:
            QMessageBox.warning(self, "未能保存修改", str(exc))
            return
        if not outcome.changed:
            self.save_button.setEnabled(False)
            return
        self._reload_current_row()
        self.saved_any = True
        if outcome.export_error:
            self.status.setStyleSheet("color:#B4232A;")
            self.status.setText(
                f"字幕已保存到课程库，但笔记文件未能更新：{outcome.export_error}。请检查输出目录后重新整理。"
            )
        else:
            self.status.setStyleSheet("")
            self.status.setText("字幕和对照笔记已更新；旧的 AI 整理笔记仍需在课程库重新整理。")

    def undo_current(self) -> None:
        row = self.rows.get(self.current_id)
        if row is None:
            return
        if self._is_dirty() and not self._confirm_discard("撤销校对会丢弃当前句尚未保存的文字。"):
            return
        try:
            outcome = undo_course_segment_correction(
                self.repository, self.course_id, self.current_id,
                str(row["original_text"]), str(row["translated_text"]),
                settings=self.settings,
            )
        except Exception as exc:
            QMessageBox.warning(self, "未能撤销校对", str(exc))
            return
        if not outcome.changed:
            self.undo_button.setEnabled(False)
            return
        self._reload_current_row()
        self.saved_any = True
        if outcome.export_error:
            self.status.setStyleSheet("color:#B4232A;")
            self.status.setText(
                f"原句已恢复到课程库，但笔记文件未能更新：{outcome.export_error}。请检查输出目录后重新整理。"
            )
        else:
            self.status.setStyleSheet("")
            self.status.setText("已恢复上次校对前的文字；对照笔记已更新，AI 整理笔记可重新生成。")

    def _reload_current_row(self) -> None:
        updated = next(
            (item for item in self.repository.get_course_segments(self.course_id)
             if str(item["id"]) == self.current_id),
            None,
        )
        if updated is None:
            raise RuntimeError("保存后未能重新读取这句记录，请刷新课程库。")
        self.rows[self.current_id] = dict(updated)
        current_item = self.list.currentItem()
        if current_item is not None:
            current_item.setText(self._item_text(self.rows[self.current_id]))
        self._loading = True
        try:
            self.english.setPlainText(str(updated["original_text"]))
            self.chinese.setPlainText(str(updated["translated_text"]))
            self.save_button.setEnabled(False)
            self.keep_chinese.setChecked(False)
            self.keep_chinese.hide()
            self.undo_button.setEnabled(
                self.repository.has_segment_corrections(self.current_id)
            )
        finally:
            self._loading = False

    def closeEvent(self, event) -> None:  # type: ignore[override]
        if self._is_dirty() and not self._confirm_discard("关闭窗口会丢弃当前句尚未保存的文字。"):
            event.ignore()
            return
        super().closeEvent(event)
