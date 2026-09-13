from __future__ import annotations

import html
import os
import sys
import threading
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QLockFile, QObject, QSize, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import (
    QColor,
    QDragEnterEvent,
    QDropEvent,
    QFont,
    QIcon,
    QKeyEvent,
    QKeySequence,
    QShortcut,
    QDesktopServices,
)
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QStackedWidget,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from .app import process_course
from .branding import APP_NAME, APP_TITLE
from .config import Settings, save_env_settings
from .courseware import CourseContext, build_course_context, merge_subject_context
from .live import AudioDevice, AudioLevelResult, LiveCourseSession, capture_audio_level, list_input_devices
from .live_summary import LiveSummarySnapshot
from .marked_context import MarkedContext, build_marked_contexts, marked_contexts_markdown
from .models import CourseResult, Segment
from .paragraphs import group_segments, paragraph_time_bounds, should_start_new_paragraph
from .recent_index import RecentSegmentIndex
from .recovery_center import RecoveryCenterDialog
from .recovery_inventory import list_recovery_candidates
from .segment_editor import SegmentEditorDialog
from .subject_terms_dialog import SubjectTermsDialog
from .usage import format_usage_summary
from .platforms import (
    IS_APPLE_SILICON,
    IS_MACOS,
    local_speech_name,
    local_speech_option,
    microphone_permission_hint,
)
from .storage import CourseRepository


COLORS = {
    "bg": "#F7F7F5",
    "surface": "#FFFFFF",
    "surface_alt": "#FAFAF8",
    "text": "#1F1F1F",
    "muted": "#6B6B6B",
    "border": "#E6E6E3",
    "primary": "#2F65B0",
    "primary_hover": "#285792",
    "primary_soft": "#EEF3FA",
    "danger": "#C42B1C",
    "success": "#107C10",
}

COURSE_STATUS_LABELS = {
    "recording": "录制中",
    "transcribing": "识别中",
    "translating": "翻译中",
    "organizing": "整理中",
    "completed": "已完成",
    "needs_attention": "需要处理",
    "interrupted": "上次中断",
    "failed": "未完成",
}

READING_MODES = {
    "chinese": "中文优先",
    "bilingual": "双语对照",
    "english": "英文优先",
}

UI_FONT_FAMILY = "PingFang SC" if IS_MACOS else "Microsoft YaHei UI"
UI_FONT_STACK = (
    '"PingFang SC", "SF Pro Text", "Helvetica Neue"'
    if IS_MACOS
    else '"Microsoft YaHei UI", "Segoe UI"'
)


def format_duration(milliseconds: int) -> str:
    seconds = max(0, int(milliseconds) // 1000)
    if seconds >= 3600:
        return f"{seconds // 3600}:{(seconds % 3600) // 60:02d}:{seconds % 60:02d}"
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


STYLE = f"""
* {{ font-family: {UI_FONT_STACK}; color: {COLORS['text']}; }}
QMainWindow, QWidget#Root {{ background: {COLORS['bg']}; }}
QFrame#Sidebar {{ background: {COLORS['surface']}; border-right: 1px solid {COLORS['border']}; }}
QFrame#Topbar {{ background: {COLORS['surface']}; border-bottom: 1px solid {COLORS['border']}; }}
QLabel#TopBrand {{ font-size: 15px; font-weight: 650; }}
QLabel#BrandMark {{ color: {COLORS['text']}; font-size: 15px; font-weight: 700; }}
QLabel#PageTitle {{ font-size: 21px; font-weight: 650; }}
QLabel#PageSubtitle, QLabel#Muted {{ color: {COLORS['muted']}; font-size: 13px; }}
QLabel#SectionTitle {{ font-size: 16px; font-weight: 650; }}
QPushButton {{ border: 1px solid {COLORS['border']}; border-radius: 5px; padding: 8px 13px; background: {COLORS['surface']}; font-size: 13px; }}
QPushButton:hover {{ background: #EFF1F6; }}
QPushButton:disabled {{ background: #F1F2F5; color: #B7BAC5; }}
QPushButton#Primary {{ background: {COLORS['primary']}; color: white; border-color: {COLORS['primary']}; font-weight: 600; }}
QPushButton#Primary:hover {{ background: {COLORS['primary_hover']}; }}
QPushButton#Danger {{ background: {COLORS['surface']}; color: {COLORS['danger']}; border-color: #E6B8B3; font-weight: 600; }}
QPushButton#Nav {{ padding: 8px 4px; background: transparent; border: none; border-radius: 0; color: {COLORS['muted']}; font-size: 12px; }}
QPushButton#Nav:hover {{ background: {COLORS['surface_alt']}; color: {COLORS['text']}; }}
QPushButton#Nav:checked {{ background: transparent; color: {COLORS['primary']}; font-weight: 650; border-bottom: 2px solid {COLORS['primary']}; }}
QFrame#Card {{ background: {COLORS['surface']}; border: 1px solid {COLORS['border']}; border-radius: 6px; }}
QFrame#Hero {{ background: {COLORS['surface']}; border: 1px solid {COLORS['border']}; border-radius: 6px; }}
QLabel#HeroTitle {{ color: {COLORS['text']}; font-size: 17px; font-weight: 650; }}
QLabel#HeroText {{ color: {COLORS['muted']}; font-size: 13px; }}
QLabel#Chip {{ color: {COLORS['muted']}; padding: 2px 0; }}
QFrame#Transcript {{ background: transparent; border: none; border-bottom: 1px solid {COLORS['border']}; }}
QFrame#WorkspacePane {{ background: {COLORS['surface']}; border: none; }}
QFrame#ContextPane {{ background: {COLORS['surface_alt']}; border: none; border-right: 1px solid {COLORS['border']}; }}
QFrame#AssistantPane {{ background: {COLORS['surface_alt']}; border: none; border-left: 1px solid {COLORS['border']}; }}
QFrame#NowSpeaking {{ background: #F8FAFC; border: none; border-left: 3px solid {COLORS['primary']}; }}
QFrame#RecentReview {{ background: {COLORS['surface_alt']}; border: 1px solid {COLORS['border']}; border-radius: 5px; }}
QFrame#QuickReview {{ background: #F8FAFC; border: 1px solid {COLORS['border']}; border-radius: 5px; }}
QFrame#QualityStrip {{ background: {COLORS['surface']}; border: none; border-bottom: 1px solid {COLORS['border']}; }}
QFrame#ControlBar {{ background: {COLORS['surface']}; border: none; border-top: 1px solid {COLORS['border']}; }}
QLabel#QualityLabel {{ color: {COLORS['muted']}; font-size: 12px; }}
QLabel#PaneTitle {{ font-size: 14px; font-weight: 650; }}
QLabel#LiveEnglish {{ color: {COLORS['muted']}; font-size: 17px; line-height: 1.55; }}
QLabel#LiveChinese {{ color: {COLORS['text']}; font-size: 20px; font-weight: 550; line-height: 1.6; }}
QSplitter::handle {{ background: transparent; }}
QFrame#ActionCard {{ background: {COLORS['surface']}; border: 1px solid {COLORS['border']}; border-radius: 16px; }}
QFrame#ActionCard:hover {{ border: 1px solid #C9C3FF; }}
QLineEdit, QComboBox, QPlainTextEdit, QTextBrowser {{ background: {COLORS['surface']}; border: 1px solid {COLORS['border']}; border-radius: 5px; padding: 8px 10px; selection-background-color: {COLORS['primary']}; }}
QLineEdit:focus, QComboBox:focus, QPlainTextEdit:focus {{ border: 1px solid {COLORS['primary']}; }}
QComboBox::drop-down {{ border: none; width: 26px; }}
QScrollArea {{ border: none; background: transparent; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}
QListWidget {{ background: {COLORS['surface']}; border: 1px solid {COLORS['border']}; border-radius: 12px; padding: 5px; outline: 0; }}
QListWidget::item {{ border-radius: 8px; padding: 10px; margin: 2px; }}
QListWidget::item:selected {{ background: {COLORS['primary_soft']}; color: {COLORS['primary']}; }}
QProgressBar {{ border: none; background: #EAECF0; border-radius: 3px; height: 6px; text-align: center; color: transparent; }}
QProgressBar::chunk {{ background: {COLORS['primary']}; border-radius: 3px; }}
QMessageBox {{ background: {COLORS['surface']}; }}
QMessageBox QLabel {{ color: {COLORS['text']}; min-width: 360px; font-size: 13px; }}
QMessageBox QPushButton {{ min-width: 78px; background: {COLORS['surface_alt']}; color: {COLORS['text']}; }}
QMessageBox QPushButton:hover {{ background: {COLORS['primary_soft']}; }}
QFrame#NoticeError {{ background: #FFF1F1; border: 1px solid #FFD8D8; border-radius: 12px; }}
QFrame#NoticeWarning {{ background: #FFF8E8; border: 1px solid #F7E0AA; border-radius: 12px; }}
QLabel#NoticeErrorText {{ color: #B4232A; }}
QLabel#NoticeWarningText {{ color: #8A5A00; }}
"""


class Bridge(QObject):
    event = Signal(str, object)


class RuntimeCheckBridge(QObject):
    finished = Signal(bool, str, str)


def friendly_error(error: object) -> str:
    """Turn provider/runtime exceptions into short, actionable Chinese messages."""
    message = str(error).strip()
    lowered = message.lower()
    if "openai_api_key" in lowered or "api key" in lowered or "api_key" in lowered:
        return "尚未配置语音服务密钥。请到“设置”中打开配置文件，填写 OPENAI_API_KEY 后重启软件。"
    if "401" in lowered or "unauthorized" in lowered or "invalid api" in lowered:
        return "API 密钥无效或已失效。请检查密钥是否完整，并确认对应账户可以使用当前模型。"
    if "429" in lowered or "rate limit" in lowered:
        return "服务请求过于频繁或账户额度不足。课堂内容不会因此被删除，请稍后重试或检查账户余额。"
    if "timeout" in lowered or "timed out" in lowered:
        return "连接服务超时。请检查网络，稍后重试；如果正在上课，建议先保留当前记录。"
    if "connection" in lowered or "network" in lowered:
        return "无法连接到服务。请检查网络、代理和服务地址，然后重试。"
    if "model" in lowered and ("not found" in lowered or "does not exist" in lowered):
        return "当前模型不可用。请在配置中检查模型名称或更换有权限的模型。"
    if "device unavailable" in lowered or "invalidated" in lowered:
        return "音频设备已断开或被系统切换。请重新选择音源，再开始课堂；已经记录的内容不会丢失。"
    if "device" in lowered and ("busy" in lowered or "occupied" in lowered):
        return (
            "音频设备正被其他程序占用。请关闭占用它的软件后重试。"
            if IS_MACOS
            else "音频设备正被其他程序独占。请关闭占用它的软件，或在 Windows 声音设置中关闭独占模式。"
        )
    if "permission" in lowered or "access denied" in lowered or "not permitted" in lowered:
        return f"没有麦克风访问权限。{microphone_permission_hint()}"
    if "invalid sample rate" in lowered or "-9997" in lowered:
        return "麦克风不支持请求的采样率。请刷新设备后重试；软件将使用麦克风原生采样率并自动转换。"
    return message or "发生了未知错误，请重试。"


def show_message(parent: QWidget, icon: QMessageBox.Icon, title: str, text: str) -> None:
    box = QMessageBox(parent)
    box.setIcon(icon)
    box.setWindowTitle(title)
    box.setText(text)
    box.setStandardButtons(QMessageBox.StandardButton.Ok)
    box.button(QMessageBox.StandardButton.Ok).setText("知道了")
    box.exec()


def confirm_message(parent: QWidget, title: str, text: str) -> bool:
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Icon.Question)
    box.setWindowTitle(title)
    box.setText(text)
    box.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
    box.button(QMessageBox.StandardButton.Yes).setText("确定退出")
    box.button(QMessageBox.StandardButton.No).setText("继续课堂")
    box.setDefaultButton(QMessageBox.StandardButton.No)
    return box.exec() == QMessageBox.StandardButton.Yes


def confirm_course_delete(parent: QWidget, title: str, segment_count: int) -> bool:
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Icon.Warning)
    box.setWindowTitle("删除课程记录")
    box.setText(f"确定删除《{title}》吗？")
    box.setInformativeText(
        f"软件内的课程和 {segment_count} 句字幕会被删除。已经导出的 Markdown 笔记文件会保留；若启用了临时音频，保留的 WAV 也需手动删除。"
    )
    box.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
    box.button(QMessageBox.StandardButton.Yes).setText("删除记录")
    box.button(QMessageBox.StandardButton.No).setText("取消")
    box.setDefaultButton(QMessageBox.StandardButton.No)
    return box.exec() == QMessageBox.StandardButton.Yes


def clear_layout(layout: QVBoxLayout) -> None:
    while layout.count():
        item = layout.takeAt(0)
        widget = item.widget()
        if widget is not None:
            widget.deleteLater()


class Page(QWidget):
    def __init__(self, title: str, subtitle: str = "") -> None:
        super().__init__()
        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(24, 20, 24, 20)
        self.layout.setSpacing(12)
        self.title_label = QLabel(title)
        self.title_label.setObjectName("PageTitle")
        self.layout.addWidget(self.title_label)
        if subtitle:
            self.subtitle_label = QLabel(subtitle)
            self.subtitle_label.setObjectName("PageSubtitle")
            self.layout.addWidget(self.subtitle_label)


class NoticeBar(QFrame):
    action_clicked = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("NoticeWarning")
        row = QHBoxLayout(self)
        row.setContentsMargins(14, 10, 12, 10)
        self.text = QLabel()
        self.text.setObjectName("NoticeWarningText")
        self.text.setWordWrap(True)
        self.action = QPushButton("处理")
        self.action.clicked.connect(self.action_clicked.emit)
        close = QPushButton("×")
        close.setFixedSize(32, 32)
        close.setStyleSheet("padding:0; font-size:18px; background:transparent;")
        close.setToolTip("关闭提示")
        close.clicked.connect(self.hide)
        row.addWidget(self.text, 1)
        row.addWidget(self.action)
        row.addWidget(close)
        self.hide()

    def show_notice(self, text: str, action: str = "", error: bool = False) -> None:
        self.setObjectName("NoticeError" if error else "NoticeWarning")
        self.text.setObjectName("NoticeErrorText" if error else "NoticeWarningText")
        self.text.setText(text)
        self.action.setText(action)
        self.action.setVisible(bool(action))
        self.style().unpolish(self)
        self.style().polish(self)
        self.text.style().unpolish(self.text)
        self.text.style().polish(self.text)
        self.show()


class ActionCard(QFrame):
    clicked = Signal()

    def __init__(self, icon: str, title: str, description: str, accent: str) -> None:
        super().__init__()
        self.setObjectName("ActionCard")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 22, 22, 22)
        layout.setSpacing(10)
        mark = QLabel(icon)
        mark.setFixedSize(44, 44)
        mark.setAlignment(Qt.AlignmentFlag.AlignCenter)
        mark.setStyleSheet(f"background:{accent}; color:white; border-radius:12px; font-size:20px;")
        title_label = QLabel(title)
        title_label.setObjectName("SectionTitle")
        desc = QLabel(description)
        desc.setObjectName("Muted")
        desc.setWordWrap(True)
        layout.addWidget(mark)
        layout.addWidget(title_label)
        layout.addWidget(desc)
        layout.addStretch()

    def mousePressEvent(self, event) -> None:  # type: ignore[override]
        self.clicked.emit()
        super().mousePressEvent(event)


class HomePage(Page):
    def __init__(
        self,
        navigate: Callable[[int], None],
        repository: CourseRepository,
        settings: Settings,
        open_course: Callable[[str], None],
        open_recovery_center: Callable[[], None],
    ) -> None:
        super().__init__("最近课程")
        self.navigate = navigate
        self.repository = repository
        self.open_course = open_course
        self.open_recovery_center = open_recovery_center
        toolbar = QHBoxLayout()
        start = QPushButton("新建课堂")
        start.setObjectName("Primary")
        start.clicked.connect(lambda: navigate(1))
        import_button = QPushButton("导入录音或视频")
        import_button.clicked.connect(lambda: navigate(2))
        self.service = QLabel()
        self.service.setObjectName("Muted")
        toolbar.addWidget(start)
        toolbar.addWidget(import_button)
        toolbar.addStretch()
        toolbar.addWidget(self.service)
        self.layout.addLayout(toolbar)

        self.recovery_card = QFrame()
        self.recovery_card.setObjectName("Card")
        recovery_row = QHBoxLayout(self.recovery_card)
        recovery_row.setContentsMargins(16, 12, 16, 12)
        self.recovery_label = QLabel()
        self.recovery_label.setWordWrap(True)
        recovery_button = QPushButton("打开恢复中心")
        recovery_button.clicked.connect(self.open_recovery_center)
        recovery_row.addWidget(self.recovery_label, 1)
        recovery_row.addWidget(recovery_button)
        self.layout.addWidget(self.recovery_card)

        recent = QFrame()
        recent.setObjectName("Card")
        self.recent_layout = QVBoxLayout(recent)
        self.recent_layout.setContentsMargins(0, 0, 0, 0)
        self.recent_layout.setSpacing(0)
        self.reload()
        self.layout.addWidget(recent)
        self.layout.addStretch()

    def reload(self) -> None:
        settings = Settings.load()
        provider_names = {"deepseek": "DeepSeek", "openai": "OpenAI", "compatible": "兼容服务"}
        speech = local_speech_name() if settings.live_mode == "local" else (
            "OpenAI" if settings.api_key else "未配置"
        )
        translation = (
            provider_names.get(settings.text_provider, settings.text_provider)
            if settings.text_api_key
            else "未配置"
        )
        self.service.setText(f"转写：{speech}    翻译：{translation}")
        recoverable = list_recovery_candidates(self.repository)
        self.recovery_card.setVisible(bool(recoverable))
        if recoverable:
            count = len(recoverable)
            audio_count = sum(item.audio_path is not None for item in recoverable)
            self.recovery_label.setText(
                f"有 {count} 项课堂内容可以继续处理"
                + (f" · 其中 {audio_count} 项留有本机录音" if audio_count else "")
            )
        clear_layout(self.recent_layout)
        rows = self.repository.list_courses(limit=4)
        if not rows:
            empty = QLabel("暂无课程记录")
            empty.setObjectName("Muted")
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            empty.setMinimumHeight(130)
            self.recent_layout.addWidget(empty)
        for row in rows:
            item = QWidget()
            item.setMinimumHeight(62)
            item_layout = QHBoxLayout(item)
            item_layout.setContentsMargins(16, 8, 16, 8)
            title = QLabel(str(row["title"]))
            title.setStyleSheet("font-weight:600; font-size:14px;")
            subject = QLabel(str(row["subject"]))
            subject.setObjectName("Muted")
            status = COURSE_STATUS_LABELS.get(str(row["status"]), str(row["status"]))
            pending = int(row["pending_count"])
            meta_text = (
                f"{row['segment_count']} 句 · {format_duration(int(row['duration_ms']))} · {status}"
                + (f" · {pending} 句待补译" if pending else "")
            )
            meta = QLabel(meta_text)
            meta.setObjectName("Muted")
            item_layout.addWidget(title, 2)
            item_layout.addWidget(subject, 1)
            item_layout.addWidget(meta, 1)
            open_button = QPushButton("查看")
            course_id = str(row["id"])
            open_button.clicked.connect(
                lambda checked=False, selected_id=course_id: self.open_course(selected_id)
            )
            item_layout.addWidget(open_button)
            self.recent_layout.addWidget(item)
            line = QFrame()
            line.setFixedHeight(1)
            line.setStyleSheet(f"background:{COLORS['border']};")
            self.recent_layout.addWidget(line)


class ParagraphCard(QFrame):
    retry_requested = Signal(str)
    marker_requested = Signal(str, str)

    def __init__(self, segment: Segment) -> None:
        super().__init__()
        self.segments: list[Segment] = [segment]
        self.failed_ids: set[str] = set()
        self.reading_mode = "chinese"
        self.setObjectName("Transcript")
        self.review_highlight_timer = QTimer(self)
        self.review_highlight_timer.setSingleShot(True)
        self.review_highlight_timer.timeout.connect(self.clear_review_highlight)
        layout = QVBoxLayout(self)
        self.body_layout = layout
        layout.setContentsMargins(4, 12, 8, 15)
        layout.setSpacing(7)
        heading = QHBoxLayout()
        heading.setSpacing(7)
        self.meta = QLabel()
        self.meta.setStyleSheet(f"font-size:11px;color:{COLORS['muted']};")
        self.marker_badge = QLabel()
        self.marker_badge.setStyleSheet(
            f"font-size:11px;color:{COLORS['primary']};font-weight:650;"
        )
        self.retry_button = QPushButton("重试失败")
        self.retry_button.setToolTip("重新提交本段中第一条失败的中文翻译")
        self.retry_button.clicked.connect(self._request_retry)
        self.copy_button = QPushButton("复制")
        self.copy_button.setToolTip("复制本段英文和中文")
        self.copy_button.clicked.connect(self.copy_text)
        self.mark_button = QPushButton("标记")
        self.mark_button.setToolTip("补标这段记录；标记会落在本段最后一句并立即保存")
        marker_menu = QMenu(self.mark_button)
        self.important_action = marker_menu.addAction("标为重点")
        self.question_action = marker_menu.addAction("标为疑问")
        self.clear_marker_action = marker_menu.addAction("取消段末标记")
        self.important_action.triggered.connect(
            lambda checked=False: self._request_marker("important")
        )
        self.question_action.triggered.connect(
            lambda checked=False: self._request_marker("question")
        )
        self.clear_marker_action.triggered.connect(
            lambda checked=False: self._request_marker("")
        )
        self.mark_button.setMenu(marker_menu)
        self.english_button = QPushButton("显示英文")
        self.english_button.setToolTip("显示或隐藏本段英文原文")
        self.english_button.clicked.connect(self.toggle_english)
        self.detail_button = QPushButton("展开逐句")
        self.detail_button.setToolTip("查看本段中每句话的时间和翻译")
        self.detail_button.clicked.connect(self.toggle_details)
        for button in (
            self.retry_button,
            self.copy_button,
            self.mark_button,
            self.english_button,
            self.detail_button,
        ):
            button.setStyleSheet(
                f"QPushButton{{border:none;background:transparent;color:{COLORS['muted']};"
                "padding:2px 5px;font-size:11px;}"
                f"QPushButton:hover{{color:{COLORS['primary']};background:{COLORS['primary_soft']};}}"
            )
        heading.addWidget(self.meta)
        heading.addWidget(self.marker_badge)
        heading.addStretch()
        heading.addWidget(self.retry_button)
        heading.addWidget(self.copy_button)
        heading.addWidget(self.mark_button)
        heading.addWidget(self.english_button)
        heading.addWidget(self.detail_button)
        self.english = QLabel()
        self.english.setWordWrap(True)
        self.english.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.english.setStyleSheet(f"font-size:14px;color:{COLORS['muted']};line-height:1.5;")
        self.chinese = QLabel()
        self.chinese.setWordWrap(True)
        self.chinese.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.chinese.setStyleSheet("font-size:18px; font-weight:500; line-height:1.6;")
        self.details = QLabel()
        self.details.setWordWrap(True)
        self.details.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.details.setStyleSheet(
            f"background:{COLORS['surface_alt']};color:{COLORS['muted']};"
            "padding:9px;font-size:12px;line-height:1.5;"
        )
        self.details.hide()
        layout.addLayout(heading)
        layout.addWidget(self.chinese)
        layout.addWidget(self.english)
        layout.addWidget(self.details)
        self.english.hide()
        self.set_reading_mode("chinese")
        self.render()

    def set_reading_mode(self, mode: str) -> None:
        if mode not in READING_MODES:
            raise ValueError("未知的课堂阅读模式。")
        self.reading_mode = mode
        self.body_layout.removeWidget(self.english)
        if mode == "chinese":
            self.body_layout.insertWidget(2, self.english)
            self.english.hide()
            self.chinese.setStyleSheet("font-size:18px;font-weight:500;line-height:1.6;")
            self.english.setStyleSheet(f"font-size:14px;color:{COLORS['muted']};line-height:1.5;")
        else:
            self.body_layout.insertWidget(1, self.english)
            self.english.show()
            if mode == "english":
                self.english.setStyleSheet(f"font-size:18px;color:{COLORS['text']};font-weight:500;line-height:1.6;")
                self.chinese.setStyleSheet(f"font-size:14px;color:{COLORS['muted']};line-height:1.5;")
            else:
                self.english.setStyleSheet(f"font-size:16px;color:{COLORS['muted']};line-height:1.5;")
                self.chinese.setStyleSheet("font-size:18px;font-weight:500;line-height:1.6;")
        self.english_button.setText("显示英文" if self.english.isHidden() else "隐藏英文")

    def add_segment(self, segment: Segment) -> None:
        if any(item.id == segment.id for item in self.segments):
            return
        self.segments.append(segment)
        self.render()

    def update_translation(
        self, segment_id: str, text: str, failed: bool = False, final: bool = True
    ) -> None:
        for segment in self.segments:
            if segment.id == segment_id:
                segment.translated_text = text
                break
        if failed:
            self.failed_ids.add(segment_id)
        elif text:
            self.failed_ids.discard(segment_id)
        self.render(update_details=final)

    def render(self, update_details: bool = True) -> None:
        last = self.segments[-1]
        start_ms, end_ms = paragraph_time_bounds(self.segments)
        start = self._time(start_ms)
        end = self._time(end_ms)
        self.meta.setText(f"{start}–{end}  ·  {len(self.segments)} 句")
        markers = {item.marker for item in self.segments}
        badges = []
        if "important" in markers:
            badges.append("★ 重点")
        if "question" in markers:
            badges.append("? 疑问")
        self.marker_badge.setText("  ·  ".join(badges))
        tail_marker = last.marker
        self.important_action.setText("取消段末重点" if tail_marker == "important" else "段末标为重点")
        self.question_action.setText("取消段末疑问" if tail_marker == "question" else "段末标为疑问")
        self.clear_marker_action.setEnabled(bool(tail_marker))
        self.english.setText(" ".join(item.original_text.strip() for item in self.segments))
        translated = [item.translated_text.strip() for item in self.segments if item.translated_text.strip()]
        waiting = any(not item.translated_text.strip() and item.id not in self.failed_ids for item in self.segments)
        failed = any(item.id in self.failed_ids for item in self.segments)
        chinese = " ".join(translated)
        if waiting:
            chinese = f"{chinese}  正在翻译……".strip()
        if failed:
            chinese = f"{chinese}  部分句子翻译失败，英文已保存。".strip()
        self.retry_button.setVisible(failed)
        self.chinese.setText(chinese or "正在翻译……")
        self.detail_button.setVisible(len(self.segments) > 1)
        if update_details or not self.details.isHidden():
            self._render_details()

    def _render_details(self) -> None:
        rows = []
        for item in self.segments:
            translation = item.translated_text.strip()
            if item.id in self.failed_ids:
                translation = "翻译失败，英文已保存"
            elif not translation:
                translation = "正在翻译……"
            rows.append(
                f"<p><span style='color:#777'>{self._time(item.start_ms)}</span>&nbsp;&nbsp;"
                f"{html.escape(item.original_text)}<br>"
                f"<span style='color:#555'>{html.escape(translation)}</span></p>"
            )
        self.details.setText("".join(rows))

    def toggle_details(self) -> None:
        visible = self.details.isHidden()
        if visible:
            self._render_details()
        self.details.setVisible(visible)
        self.detail_button.setText("收起逐句" if visible else "展开逐句")

    def toggle_english(self) -> None:
        visible = self.english.isHidden()
        self.english.setVisible(visible)
        self.english_button.setText("隐藏英文" if visible else "显示英文")

    def marker_for(self, segment_id: str) -> str:
        for segment in self.segments:
            if segment.id == segment_id:
                return segment.marker
        return ""

    def set_marker(self, segment_id: str, marker: str) -> None:
        for segment in self.segments:
            if segment.id == segment_id:
                segment.marker = marker
                self.render(update_details=False)
                return

    def _request_marker(self, marker: str) -> None:
        anchor = self.segments[-1]
        updated = "" if anchor.marker == marker else marker
        self.marker_requested.emit(anchor.id, updated)

    def highlight_for_review(self) -> None:
        self.setStyleSheet(
            f"QFrame#Transcript{{background:{COLORS['primary_soft']};"
            f"border:none;border-left:3px solid {COLORS['primary']};"
            f"border-bottom:1px solid {COLORS['border']};}}"
        )
        self.review_highlight_timer.start(2500)

    def clear_review_highlight(self) -> None:
        self.setStyleSheet("")

    def set_retrying(self, segment_id: str) -> None:
        self.failed_ids.discard(segment_id)
        for segment in self.segments:
            if segment.id == segment_id:
                segment.translated_text = ""
                break
        self.render()

    def _request_retry(self) -> None:
        if self.failed_ids:
            self.retry_requested.emit(next(iter(self.failed_ids)))

    def copy_text(self) -> None:
        english = " ".join(item.original_text.strip() for item in self.segments)
        chinese = " ".join(item.translated_text.strip() for item in self.segments if item.translated_text.strip())
        QApplication.clipboard().setText(f"{english}\n\n{chinese}".strip())

    @staticmethod
    def _time(milliseconds: int) -> str:
        seconds = max(0, milliseconds // 1000)
        return f"{seconds // 60:02d}:{seconds % 60:02d}"


class RecentReviewPane(QFrame):
    """Compactly show what the lecturer covered during the last two minutes."""

    WINDOW_MS = 120_000
    MAX_SEGMENTS = 12
    MAX_CHARS = 680

    def __init__(self) -> None:
        super().__init__()
        self.topic = ""
        self.segments: list[Segment] = []
        self.segment_index = RecentSegmentIndex()
        self.setObjectName("RecentReview")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(15, 11, 15, 13)
        layout.setSpacing(6)
        heading = QHBoxLayout()
        heading.setSpacing(8)
        self.title = QLabel("最近几分钟")
        self.title.setStyleSheet("font-size:13px;font-weight:650;")
        self.meta = QLabel("等待稳定中文")
        self.meta.setStyleSheet(f"font-size:11px;color:{COLORS['muted']};")
        heading.addWidget(self.title)
        heading.addStretch()
        heading.addWidget(self.meta)
        self.body = QLabel("翻译完成后，这里会把老师刚讲的内容合并成一段，方便快速跟上课堂。")
        self.body.setWordWrap(True)
        self.body.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.body.setStyleSheet(f"font-size:15px;line-height:1.65;color:{COLORS['text']};")
        layout.addLayout(heading)
        layout.addWidget(self.body)

    def set_topic(self, topic: str) -> None:
        self.topic = " ".join(topic.strip().split())
        self._render()

    def set_segments(self, segments: list[Segment]) -> None:
        self.segments = segments
        self.segment_index.update(segments)
        self._render()

    def reset(self) -> None:
        self.topic = ""
        self.segments = []
        self.segment_index.clear()
        self._render()

    def _recent_segments(self) -> list[Segment]:
        selected: list[Segment] = []
        characters = 0
        for item in reversed(self.segment_index.recent(self.WINDOW_MS)):
            if not item.translated_text.strip():
                continue
            length = len(item.translated_text.strip())
            if selected and characters + length > self.MAX_CHARS:
                break
            selected.append(item)
            characters += length
            if len(selected) >= self.MAX_SEGMENTS:
                break
        return list(reversed(selected))

    def _render(self) -> None:
        recent = self._recent_segments()
        self.title.setText(self.topic or "最近几分钟")
        if not recent:
            self.meta.setText("等待最近中文" if self.segments else "等待稳定中文")
            self.body.setText(
                "最近两分钟暂无稳定中文；英文已保存在下方记录，可用“回顾刚才”查看。"
                if self.segments else
                "翻译完成后，这里会把老师刚讲的内容合并成一段，方便快速跟上课堂。"
            )
            return
        start = ParagraphCard._time(recent[0].start_ms)
        end = ParagraphCard._time(max(item.end_ms for item in recent))
        self.meta.setText(f"{start}–{end} · {len(recent)} 句")
        self.body.setText(" ".join(item.translated_text.strip() for item in recent))


class QuickReviewPane(QFrame):
    """Read recent saved subtitles without pausing or making another API call."""

    jump_requested = Signal(int)
    close_requested = Signal()
    WINDOWS = (30_000, 60_000, 120_000)
    MAX_SEGMENTS = 16
    MAX_CHARS = 900

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("QuickReview")
        self.segments: list[Segment] = []
        self.segment_index = RecentSegmentIndex()
        self.selected: list[Segment] = []
        self.topic = ""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(15, 12, 15, 13)
        layout.setSpacing(8)
        toolbar = QHBoxLayout()
        title = QLabel("回顾刚才")
        title.setObjectName("PaneTitle")
        self.range_choice = QComboBox()
        self.range_choice.addItems(["最近 30 秒", "最近 1 分钟", "最近 2 分钟"])
        self.range_choice.setCurrentIndex(1)
        self.range_choice.currentIndexChanged.connect(self.refresh)
        self.close_button = QPushButton("返回实时")
        self.close_button.clicked.connect(lambda checked=False: self.close_requested.emit())
        toolbar.addWidget(title)
        toolbar.addWidget(self.range_choice)
        toolbar.addStretch()
        toolbar.addWidget(self.close_button)
        self.meta = QLabel("等待课堂记录")
        self.meta.setObjectName("Muted")
        self.body = QLabel()
        self.body.setWordWrap(True)
        self.body.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.body.setStyleSheet("font-size:15px;line-height:1.6;")
        self.english = QLabel()
        self.english.setWordWrap(True)
        self.english.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.english.setStyleSheet(f"font-size:13px;line-height:1.5;color:{COLORS['muted']};")
        self.english.hide()
        self.content_scroll = QScrollArea()
        self.content_scroll.setWidgetResizable(True)
        self.content_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.content_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.content_scroll.setMaximumHeight(240)
        content_host = QWidget()
        content_layout = QVBoxLayout(content_host)
        content_layout.setContentsMargins(0, 0, 5, 0)
        content_layout.setSpacing(10)
        content_layout.addWidget(self.body)
        content_layout.addWidget(self.english)
        content_layout.addStretch()
        self.content_scroll.setWidget(content_host)
        actions = QHBoxLayout()
        self.english_button = QPushButton("查看英文原文")
        self.english_button.clicked.connect(self.toggle_english)
        self.jump_button = QPushButton("跳到完整记录")
        self.jump_button.clicked.connect(self._request_jump)
        actions.addWidget(self.english_button)
        actions.addWidget(self.jump_button)
        actions.addStretch()
        layout.addLayout(toolbar)
        layout.addWidget(self.meta)
        layout.addWidget(self.content_scroll)
        layout.addLayout(actions)
        self.refresh()

    def set_segments(self, segments: list[Segment]) -> None:
        self.segments = segments
        self.segment_index.update(segments)
        self.refresh()

    def set_topic(self, topic: str) -> None:
        self.topic = " ".join(topic.split())
        self.refresh()

    def reset(self) -> None:
        self.segments = []
        self.segment_index.clear()
        self.topic = ""
        self.range_choice.setCurrentIndex(1)
        self.english.hide()
        self.english_button.setText("查看英文原文")
        self.refresh()

    def refresh(self, *_: object) -> None:
        self.selected = []
        candidates = self.segment_index.recent(self.WINDOWS[self.range_choice.currentIndex()])
        if candidates:
            characters = 0
            for item in reversed(candidates):
                text = item.translated_text.strip() or item.original_text.strip()
                if self.selected and characters + len(text) > self.MAX_CHARS:
                    break
                self.selected.append(item)
                characters += len(text)
                if len(self.selected) >= self.MAX_SEGMENTS:
                    break
            self.selected.reverse()
        available = bool(self.selected)
        self.english_button.setEnabled(available)
        self.jump_button.setEnabled(available)
        if not available:
            self.meta.setText("等待课堂记录")
            self.body.setText("还没有可回顾的稳定字幕；录音会继续进行。")
            self.english.setText("")
            return
        start = format_duration(self.selected[0].start_ms)
        end = format_duration(max(item.end_ms for item in self.selected))
        topic = f"当前主题：{self.topic} · " if self.topic else ""
        self.meta.setText(f"{topic}{start}–{end} · {len(self.selected)} 句 · 已保存内容摘录")
        self.body.setText(" ".join(
            item.translated_text.strip() or f"[待翻译] {item.original_text.strip()}"
            for item in self.selected
        ))
        self.english.setText(" ".join(item.original_text.strip() for item in self.selected))

    def toggle_english(self) -> None:
        visible = self.english.isHidden()
        self.english.setVisible(visible)
        self.english_button.setText("隐藏英文原文" if visible else "查看英文原文")

    def _request_jump(self) -> None:
        if self.selected:
            self.jump_requested.emit(self.selected[0].start_ms)


class MaterialsPane(QFrame):
    materials_changed = Signal(bool, int)

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("ContextPane")
        self.paths: list[str] = []
        layout = QVBoxLayout(self)
        layout.setContentsMargins(15, 16, 15, 16)
        layout.setSpacing(10)
        title = QLabel("课程资料")
        title.setObjectName("PaneTitle")
        hint = QLabel("导入课件，帮助整理本节课的资料来源。")
        hint.setObjectName("Muted")
        hint.setWordWrap(True)
        self.list = QListWidget()
        self.list.setStyleSheet(
            "QListWidget{background:transparent;border:none;padding:0;}"
            "QListWidget::item{background:#FAFAFC;border-radius:9px;padding:11px 9px;margin:3px 0;}"
            "QListWidget::item:selected{background:#E7E5FF;color:#5548E8;}"
        )
        self.list.setToolTip("本节课堂已关联的课件；双击可在系统中打开")
        self.list.itemDoubleClicked.connect(self.open_item)
        actions = QHBoxLayout()
        add = QPushButton("继续导入")
        add.clicked.connect(self.choose_files)
        clear = QPushButton("移除全部")
        clear.setToolTip("移除本节课堂关联的课件，并把左侧空间还给字幕")
        clear.clicked.connect(self.clear_materials)
        actions.addWidget(add)
        actions.addWidget(clear)
        self.state = QLabel("尚未导入课件")
        self.state.setObjectName("Muted")
        self.state.setWordWrap(True)
        layout.addWidget(title)
        layout.addWidget(hint)
        layout.addLayout(actions)
        layout.addWidget(self.list, 1)
        layout.addWidget(self.state)

    def choose_files(self) -> None:
        from PySide6.QtWidgets import QFileDialog

        paths, _ = QFileDialog.getOpenFileNames(
            self,
            "选择课程课件",
            "",
            "课程资料 (*.pdf *.ppt *.pptx *.doc *.docx *.txt *.md);;所有文件 (*)",
        )
        self.add_paths(paths)

    def add_paths(self, paths: list[str]) -> None:
        for path in paths:
            if path in self.paths:
                continue
            self.paths.append(path)
            item = QListWidgetItem(Path(path).name)
            item.setToolTip(path)
            self.list.addItem(item)
        if self.paths:
            self.state.setText(f"已关联 {len(self.paths)} 份资料 · 正在用于术语和热词提示")
        self.materials_changed.emit(bool(self.paths), len(self.paths))

    def clear_materials(self) -> None:
        self.paths.clear()
        self.list.clear()
        self.state.setText("尚未导入课件")
        self.materials_changed.emit(False, 0)

    @property
    def has_materials(self) -> bool:
        return bool(self.paths)

    def open_item(self, item: QListWidgetItem) -> None:
        path = item.toolTip()
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(path)):
            show_message(self, QMessageBox.Icon.Critical, "无法打开课件", "系统没有找到可用于打开该课件的应用。")


class TopicTimelinePane(QFrame):
    topic_selected = Signal(int)
    MIN_TOPIC_MS = 90_000

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("ContextPane")
        self.entries: list[tuple[int, str]] = []
        layout = QVBoxLayout(self)
        layout.setContentsMargins(15, 16, 12, 16)
        layout.setSpacing(10)
        title = QLabel("课堂脉络")
        title.setObjectName("PaneTitle")
        self.hint = QLabel("主题会随课堂进行逐渐出现；点击可回看当时记录。")
        self.hint.setWordWrap(True)
        self.hint.setObjectName("Muted")
        self.list = QListWidget()
        self.list.setStyleSheet(
            "QListWidget{background:transparent;border:none;padding:0;}"
            "QListWidget::item{padding:9px 7px;margin:2px 0;border-radius:4px;}"
            "QListWidget::item:selected{background:#EEF3FA;color:#285792;}"
        )
        self.list.itemClicked.connect(self._select_item)
        layout.addWidget(title)
        layout.addWidget(self.hint)
        layout.addWidget(self.list, 1)

    def add_topic(self, start_ms: int, title: str) -> bool:
        title = " ".join(title.split())[:80]
        if not title:
            return False
        start_ms = max(0, int(start_ms))
        if self.entries:
            previous_ms, previous_title = self.entries[-1]
            if title.casefold() == previous_title.casefold():
                return False
            if start_ms - previous_ms < self.MIN_TOPIC_MS:
                return False
        self.entries.append((start_ms, title))
        item = QListWidgetItem(f"{format_duration(start_ms)}  {title}")
        item.setData(Qt.ItemDataRole.UserRole, start_ms)
        item.setToolTip(f"跳转到 {format_duration(start_ms)} 附近的课堂记录")
        self.list.addItem(item)
        self.list.setCurrentItem(item)
        self.hint.setText(f"已记录 {len(self.entries)} 个主题 · 点击回看")
        return True

    def reset(self) -> None:
        self.entries.clear()
        self.list.clear()
        self.hint.setText("主题会随课堂进行逐渐出现；点击可回看当时记录。")

    def _select_item(self, item: QListWidgetItem) -> None:
        self.topic_selected.emit(int(item.data(Qt.ItemDataRole.UserRole)))


class SummaryPane(QFrame):
    marker_activated = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("AssistantPane")
        self.snapshot = LiveSummarySnapshot()
        self.reference_terms: list[str] = []
        layout = QVBoxLayout(self)
        layout.setContentsMargins(17, 16, 15, 16)
        layout.setSpacing(12)
        title_row = QHBoxLayout()
        title = QLabel("本节课堂")
        title.setObjectName("PaneTitle")
        title_row.addWidget(title)
        title_row.addStretch()
        self.hint = QLabel("等待字幕")
        self.hint.setObjectName("Muted")
        title_row.addWidget(self.hint)
        layout.addLayout(title_row)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        host = QWidget()
        body = QVBoxLayout(host)
        body.setContentsMargins(0, 2, 4, 4)
        body.setSpacing(18)
        self.overview = self._add_section(body, "当前主题")
        self.points_view = self._add_section(body, "关键要点")
        self.markers = self._add_section(body, "手动重点与疑问")
        self.markers.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
            | Qt.TextInteractionFlag.LinksAccessibleByMouse
        )
        self.markers.linkActivated.connect(self._marker_link_activated)
        self.terms = self._add_section(body, "术语")
        self.questions = self._add_section(body, "待复习")
        body.addStretch()
        scroll.setWidget(host)
        layout.addWidget(scroll, 1)
        self.reset()

    @staticmethod
    def _add_section(layout: QVBoxLayout, title: str) -> QLabel:
        section = QWidget()
        section_layout = QVBoxLayout(section)
        section_layout.setContentsMargins(0, 0, 0, 0)
        section_layout.setSpacing(7)
        heading = QLabel(title)
        heading.setStyleSheet("font-size:12px;font-weight:650;color:#4F4F4F;")
        content = QLabel()
        content.setWordWrap(True)
        content.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        content.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        content.setStyleSheet("font-size:13px;line-height:1.6;color:#373737;")
        section_layout.addWidget(heading)
        section_layout.addWidget(content)
        layout.addWidget(section)
        return content

    @staticmethod
    def _list_html(values: tuple[str, ...] | list[str], placeholder: str) -> str:
        if not values:
            return f"<p style='color:#777;line-height:1.6'>{html.escape(placeholder)}</p>"
        items = "".join(
            f"<li style='margin-bottom:8px'>{html.escape(value)}</li>" for value in values
        )
        return f"<ul style='line-height:1.55;padding-left:16px'>{items}</ul>"

    def observe_segment(self, segment: Segment) -> None:
        text = (segment.translated_text or segment.original_text).strip()
        if not text:
            return
        if not self.snapshot.topic:
            self.overview.setText(
                "<p style='color:#777;margin-bottom:6px'>正在形成滚动摘要…</p>"
                f"<p style='line-height:1.65'>{html.escape(text)}</p>"
            )

    def apply_snapshot(self, snapshot: LiveSummarySnapshot) -> None:
        self.snapshot = snapshot
        if snapshot.topic:
            self.overview.setText(
                f"<p style='line-height:1.65'>{html.escape(snapshot.topic)}</p>"
            )
        self.points_view.setText(
            self._list_html(snapshot.key_points, "内容积累到一定程度后生成可靠要点。")
        )
        self.questions.setText(
            self._list_html(snapshot.questions, "暂时没有明确的待复习问题。")
        )
        self._render_terms()

    def set_reference_terms(self, values: list[str]) -> None:
        self.reference_terms = values
        self._render_terms()

    def set_marked_contexts(self, contexts: list[MarkedContext]) -> None:
        if not contexts:
            self.markers.setText(
                "<p style='color:#777;line-height:1.6'>按 Ctrl+1 标重点、Ctrl+2 标疑问；这里会保留前后约 20 秒语境。</p>"
            )
            return
        entries = []
        for context in contexts[-4:][::-1]:
            content = (context.chinese or context.english).strip()
            if context.chinese and context.pending_english:
                content += f"  ·  {context.pending_english}（待翻译）"
            if len(content) > 220:
                content = content[:219].rstrip() + "…"
            entries.append(
                f"<p style='margin-bottom:12px;line-height:1.55'>"
                f"<a href='segment:{html.escape(context.anchor_id)}'>{html.escape(context.label)} ↗</a>"
                f" · {format_duration(context.start_ms)}–{format_duration(context.end_ms)}<br>"
                f"{html.escape(content)}</p>"
            )
        self.markers.setText("".join(entries))

    def _marker_link_activated(self, href: str) -> None:
        if href.startswith("segment:"):
            self.marker_activated.emit(href.removeprefix("segment:"))

    def _render_terms(self) -> None:
        merged: list[str] = []
        for value in [*self.reference_terms, *self.snapshot.terms]:
            if value and value not in merged:
                merged.append(value)
            if len(merged) >= 12:
                break
        self.terms.setText(
            self._list_html(merged, "导入课件或形成摘要后显示课程术语。")
        )

    def set_status(self, payload: object) -> None:
        if not isinstance(payload, dict):
            return
        state = str(payload.get("state", ""))
        if state == "collecting":
            count = int(payload.get("count", 0))
            target = int(payload.get("target", 6))
            self.hint.setText(f"积累中 {min(count, target)}/{target}")
        elif state == "working":
            self.hint.setText("正在整理…")
        elif state == "updated":
            self.hint.setText("刚刚更新")
        elif state == "waiting":
            self.hint.setText("等待下次更新")
        elif state == "paused_budget":
            self.hint.setText("预算节省中 · 摘要暂停")

    def reset(self) -> None:
        self.snapshot = LiveSummarySnapshot()
        self.reference_terms = []
        self.hint.setText("等待字幕")
        self.overview.setText(
            "<p style='color:#777;line-height:1.6'>开始课堂后，这里会显示当前主题。</p>"
        )
        self.points_view.setText(
            "<p style='color:#777;line-height:1.6'>每积累几句字幕，后台更新一次关键要点。</p>"
        )
        self.set_marked_contexts([])
        self._render_terms()
        self.questions.setText(
            "<p style='color:#777;line-height:1.6'>摘要会提取尚待解释或值得复习的问题。</p>"
        )


class LivePage(Page):
    VISIBLE_PARAGRAPHS = 160
    PAGE_PARAGRAPHS = 100

    def __init__(
        self,
        bridge: Bridge,
        navigate: Callable[[int], None],
        set_immersive: Callable[[bool], None],
        toggle_fullscreen: Callable[[], None],
        repository: CourseRepository,
    ) -> None:
        super().__init__("实时课堂", "低延迟英文字幕、中文翻译和自动课堂整理。")
        self.bridge = bridge
        self.navigate = navigate
        self.set_immersive = set_immersive
        self.toggle_fullscreen = toggle_fullscreen
        self.repository = repository
        self.session: object | None = None
        self.transcript_cards: dict[str, ParagraphCard] = {}
        self.paragraph_cards: list[ParagraphCard] = []
        self.paragraph_groups: list[list[Segment]] = []
        self.group_by_segment: dict[str, int] = {}
        self.segments_by_id: dict[str, Segment] = {}
        self.failed_segment_ids: set[str] = set()
        self.visible_start = 0
        self._view_dirty = False
        self.live_segments: list[Segment] = []
        self.latest_segment_id = ""
        self.auto_follow = True
        self.unseen_segments = 0
        self._programmatic_scroll = False
        self.saved_segment_count = 0
        self.stop_armed = False
        self._live_status_before_stop = ""
        self.course_context = CourseContext()
        self.paused = False
        self.elapsed = 0
        saved_reading_mode = os.getenv("CLASSNOTE_READING_MODE", "chinese").strip().lower()
        self.reading_mode = saved_reading_mode if saved_reading_mode in READING_MODES else "chinese"
        self.devices: list[AudioDevice] = []
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.follow_timer = QTimer(self)
        self.follow_timer.setSingleShot(True)
        self.follow_timer.timeout.connect(self._follow_latest_if_enabled)
        self.stop_arm_timer = QTimer(self)
        self.stop_arm_timer.setSingleShot(True)
        self.stop_arm_timer.setInterval(4000)
        self.stop_arm_timer.timeout.connect(self._disarm_stop)

        self.preparation_host = QWidget()
        preparation_layout = QVBoxLayout(self.preparation_host)
        preparation_layout.setContentsMargins(0, 0, 0, 0)
        preparation_layout.setSpacing(10)
        preparation_layout.addStretch(1)

        self.info = QFrame()
        self.info.setObjectName("Card")
        self.info.setMaximumWidth(1120)
        self.info.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        info_layout = QVBoxLayout(self.info)
        info_layout.setContentsMargins(22, 20, 22, 20)
        info_layout.setSpacing(12)
        ready_title = QLabel("课堂准备")
        ready_title.setObjectName("SectionTitle")
        ready_hint = QLabel("确认课程信息和录音来源，然后开始记录。")
        ready_hint.setObjectName("Muted")
        fields = QHBoxLayout()
        fields.setSpacing(8)
        self.title_input = QLineEdit("英语课堂")
        self.title_input.setPlaceholderText("课程名称")
        self.subject_input = QLineEdit("通用课程")
        self.subject_input.setPlaceholderText("课程领域")
        self.subject_input.editingFinished.connect(self.update_course_context_preview)
        self.device_combo = QComboBox()
        refresh = QPushButton("刷新设备")
        refresh.setToolTip("重新读取系统中可用的录音输入设备")
        refresh.clicked.connect(self.refresh_devices)
        self.test_audio_button = QPushButton("测试音源")
        self.test_audio_button.setToolTip("录制约两秒，只分析音量，不保存测试音频")
        self.test_audio_button.clicked.connect(self.test_audio_source)
        fields.addWidget(self.title_input, 2)
        fields.addWidget(self.subject_input, 1)
        fields.addWidget(self.device_combo, 2)
        fields.addWidget(refresh)
        fields.addWidget(self.test_audio_button)
        info_layout.addWidget(ready_title)
        info_layout.addWidget(ready_hint)
        info_layout.addLayout(fields)
        self.audio_test_status = QLabel("选择麦克风或“系统声音”，开始前可先测试音量。")
        self.audio_test_status.setObjectName("Muted")
        info_layout.addWidget(self.audio_test_status)
        material_row = QHBoxLayout()
        self.material_state = QLabel("未导入课件 · 字幕将使用更宽的阅读区域")
        self.material_state.setObjectName("Muted")
        self.import_materials_button = QPushButton("导入课件（可选）")
        self.import_materials_button.setToolTip("导入 PDF、PPT、Word 或文本资料；未导入时不会占用课堂空间")
        self.import_materials_button.clicked.connect(lambda: self.materials.choose_files())
        material_row.addWidget(self.material_state, 1)
        self.terms_button = QPushButton("术语管理")
        self.terms_button.setToolTip("为当前课程领域保存中英术语，下次课堂自动使用")
        self.terms_button.clicked.connect(self.manage_subject_terms)
        material_row.addWidget(self.terms_button)
        material_row.addWidget(self.import_materials_button)
        info_layout.addLayout(material_row)
        self.start_button = QPushButton("开始课堂")
        self.start_button.setObjectName("Primary")
        self.start_button.setToolTip("检查音源和服务配置，然后进入专注课堂模式")
        self.start_button.clicked.connect(self.start)
        info_layout.addWidget(self.start_button, 0, Qt.AlignmentFlag.AlignRight)
        preparation_layout.addWidget(self.info, 0, Qt.AlignmentFlag.AlignHCenter)

        self.notice = NoticeBar()
        self.notice.action_clicked.connect(lambda: self.navigate(4))

        self.status = QLabel("准备就绪")
        self.status.setObjectName("Muted")
        self.status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        preparation_layout.addWidget(self.status)
        preparation_layout.addStretch(2)
        self.layout.addWidget(self.preparation_host, 1)
        self.layout.addWidget(self.notice)

        self.quality_strip = QFrame()
        self.quality_strip.setObjectName("QualityStrip")
        quality_layout = QHBoxLayout(self.quality_strip)
        quality_layout.setContentsMargins(10, 6, 10, 7)
        quality_layout.setSpacing(11)
        self.record_dot = QLabel("●")
        self.record_dot.setStyleSheet(f"color:{COLORS['muted']}; font-size:15px;")
        self.runtime_title = QLabel("英语课堂")
        self.runtime_title.setStyleSheet("font-size:13px;font-weight:650;")
        self.time_label = QLabel("00:00:00")
        self.time_label.setStyleSheet("font-size:12px;font-weight:600;font-variant-numeric:tabular-nums;")
        self.live_status = QLabel("正在准备课堂")
        self.live_status.setObjectName("QualityLabel")
        self.audio_meter = QProgressBar()
        self.audio_meter.setRange(0, 100)
        self.audio_meter.setValue(0)
        self.audio_meter.setTextVisible(False)
        self.audio_meter.setFixedWidth(76)
        self.audio_meter.setFixedHeight(6)
        self.audio_quality = QLabel("音源 等待")
        self.asr_quality = QLabel("识别 等待")
        self.translation_quality = QLabel("翻译 等待")
        self.drop_quality = QLabel("音频完整")
        self.save_quality = QLabel("已保存 0 句")
        self.save_quality.setToolTip("英文字幕会先写入本地课程库，再开始网络翻译")
        self.usage_quality = QLabel("文本用量 等待")
        self.usage_quality.setToolTip("仅统计文本服务返回的 Token；费用为单价快照估算，不含云端语音")
        self.budget_quality = QLabel("课堂预算 关闭")
        self.budget_quality.setToolTip("可在设置中开启文本预算；不包含云端语音费用")
        self.budget_quality.hide()
        for label in (
            self.audio_quality,
            self.asr_quality,
            self.translation_quality,
            self.drop_quality,
            self.save_quality,
            self.usage_quality,
            self.budget_quality,
        ):
            label.setObjectName("QualityLabel")
        quality_layout.addWidget(self.record_dot)
        quality_layout.addWidget(self.runtime_title)
        quality_layout.addWidget(self.time_label)
        quality_layout.addWidget(self.live_status, 1)
        quality_layout.addWidget(self.audio_meter)
        quality_layout.addWidget(self.audio_quality)
        quality_layout.addWidget(self.asr_quality)
        quality_layout.addWidget(self.translation_quality)
        quality_layout.addWidget(self.drop_quality)
        quality_layout.addWidget(self.save_quality)
        quality_layout.addWidget(self.usage_quality)
        quality_layout.addWidget(self.budget_quality)
        quality_layout.addStretch()
        self.quality_strip.hide()
        self.layout.addWidget(self.quality_strip)

        workspace = QSplitter(Qt.Orientation.Horizontal)
        self.workspace = workspace
        workspace.setChildrenCollapsible(True)
        workspace.setHandleWidth(6)
        self.timeline = TopicTimelinePane()
        self.timeline.topic_selected.connect(self.jump_to_topic)
        self.materials = MaterialsPane()
        self.materials.materials_changed.connect(self.on_materials_changed)

        center = QFrame()
        self.center_pane = center
        center.setObjectName("WorkspacePane")
        center_layout = QVBoxLayout(center)
        center_layout.setContentsMargins(16, 14, 16, 10)
        center_layout.setSpacing(10)
        self.now_speaking = QFrame()
        self.now_speaking.setObjectName("NowSpeaking")
        now_layout = QVBoxLayout(self.now_speaking)
        now_layout.setContentsMargins(16, 12, 16, 14)
        now_layout.setSpacing(6)
        english_head = QLabel("老师正在讲 · 英文")
        english_head.setStyleSheet(f"color:{COLORS['muted']};font-size:12px;font-weight:600;")
        self.partial = QLabel("等待英文语音……")
        self.partial.setWordWrap(True)
        self.partial.setObjectName("LiveEnglish")
        self.partial.setMinimumHeight(42)
        self.partial.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        chinese_head = QLabel("中文")
        chinese_head.setStyleSheet(f"color:{COLORS['muted']};font-size:11px;font-weight:650;")
        self.current_translation = QLabel("翻译会显示在这里。")
        self.current_translation.setWordWrap(True)
        self.current_translation.setObjectName("LiveChinese")
        self.current_translation.setMinimumHeight(50)
        self.current_translation.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        now_layout.addWidget(english_head)
        now_layout.addWidget(self.partial)
        now_layout.addWidget(chinese_head)
        now_layout.addWidget(self.current_translation)
        self.recent_review = RecentReviewPane()
        self.quick_review = QuickReviewPane()
        self.quick_review.close_requested.connect(self.close_quick_review)
        self.quick_review.jump_requested.connect(self.jump_from_quick_review)
        self.quick_review.hide()
        history_row = QHBoxLayout()
        self.history_head = QLabel("课堂记录 · 按段落整理")
        self.history_head.setObjectName("PaneTitle")
        self.older_button = QPushButton("更早")
        self.older_button.setToolTip("加载前一页课堂段落；录音仍会继续")
        self.older_button.clicked.connect(self.show_older_paragraphs)
        self.older_button.hide()
        self.newer_button = QPushButton("较新")
        self.newer_button.setToolTip("加载后一页课堂段落")
        self.newer_button.clicked.connect(self.show_newer_paragraphs)
        self.newer_button.hide()
        for button in (self.older_button, self.newer_button):
            button.setStyleSheet(
                f"QPushButton{{border:none;background:transparent;color:{COLORS['muted']};"
                "padding:4px 7px;font-size:11px;}"
                f"QPushButton:hover{{color:{COLORS['primary']};background:{COLORS['primary_soft']};}}"
            )
        self.new_items_button = QPushButton("回到实时")
        self.new_items_button.setToolTip("你正在阅读较早内容；点击返回最新字幕")
        self.new_items_button.setStyleSheet(
            f"QPushButton{{color:{COLORS['primary']};background:{COLORS['primary_soft']};"
            "border:0;border-radius:7px;padding:5px 10px;font-size:12px;}"
        )
        self.new_items_button.clicked.connect(self._return_to_live)
        self.new_items_button.hide()
        history_row.addWidget(self.history_head)
        history_row.addStretch()
        history_row.addWidget(self.older_button)
        history_row.addWidget(self.newer_button)
        history_row.addWidget(self.new_items_button)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll = scroll
        self.scroll.verticalScrollBar().valueChanged.connect(self._on_transcript_scroll)
        self.cards_host = QWidget()
        self.cards_layout = QVBoxLayout(self.cards_host)
        self.cards_layout.setContentsMargins(2, 2, 8, 2)
        self.cards_layout.setSpacing(10)
        self.empty_state = QFrame()
        self.empty_state.setObjectName("Transcript")
        empty_layout = QVBoxLayout(self.empty_state)
        empty_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_icon = QLabel("CC")
        empty_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_icon.setFixedSize(52, 36)
        empty_icon.setStyleSheet(
            f"background:{COLORS['primary_soft']}; color:{COLORS['primary']}; "
            "border-radius:10px; font-weight:700;"
        )
        empty_title = QLabel("完整课堂记录会保存在这里")
        empty_title.setStyleSheet("font-size:16px; font-weight:650;")
        empty_hint = QLabel("中文按段落合并显示；需要核对时再展开英文原文和逐句记录。")
        empty_hint.setObjectName("Muted")
        empty_layout.addWidget(empty_icon, 0, Qt.AlignmentFlag.AlignCenter)
        empty_layout.addSpacing(5)
        empty_layout.addWidget(empty_title, 0, Qt.AlignmentFlag.AlignCenter)
        empty_layout.addWidget(empty_hint, 0, Qt.AlignmentFlag.AlignCenter)
        self.empty_state.setMinimumHeight(150)
        self.cards_layout.addWidget(self.empty_state)
        self.cards_layout.addStretch()
        scroll.setWidget(self.cards_host)
        center_layout.addWidget(self.now_speaking)
        center_layout.addWidget(self.recent_review)
        center_layout.addWidget(self.quick_review)
        center_layout.addLayout(history_row)
        center_layout.addWidget(scroll, 1)

        self.summary = SummaryPane()
        self.summary.marker_activated.connect(self.jump_to_segment)
        workspace.addWidget(self.timeline)
        workspace.addWidget(self.materials)
        workspace.addWidget(center)
        workspace.addWidget(self.summary)
        workspace.setStretchFactor(0, 0)
        workspace.setStretchFactor(1, 0)
        workspace.setStretchFactor(2, 1)
        workspace.setStretchFactor(3, 0)
        workspace.setSizes([0, 0, 820, 300])
        self.timeline.hide()
        self.materials.hide()
        self.layout.addWidget(workspace, 1)
        workspace.hide()
        self.latest_shortcut = QShortcut(QKeySequence(Qt.Key.Key_End), workspace)
        self.latest_shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self.latest_shortcut.activated.connect(self._return_to_live)

        controls = QFrame()
        self.controls = controls
        controls.setObjectName("ControlBar")
        control_layout = QHBoxLayout(controls)
        self.control_layout = control_layout
        control_layout.setContentsMargins(10, 7, 10, 7)
        self.pause_button = QPushButton("暂停")
        self.pause_button.setToolTip("暂时停止发送音频，再次点击可以继续")
        self.pause_button.setEnabled(False)
        self.stop_button = QPushButton("结束并整理")
        self.stop_button.setObjectName("Danger")
        self.stop_button.setToolTip("首次点击只会请求确认；4 秒内再次点击才会结束课堂")
        self.stop_button.setEnabled(False)
        self.reading_choice = QComboBox()
        self.reading_choice.setToolTip("切换课堂字幕与历史段落的阅读方式；选择会保存在本机")
        for value, label in READING_MODES.items():
            self.reading_choice.addItem(label, value)
        self.reading_choice.setCurrentIndex(list(READING_MODES).index(self.reading_mode))
        self.reading_choice.currentIndexChanged.connect(self._reading_choice_changed)
        self.review_button = QPushButton("回顾刚才")
        self.review_button.setToolTip("查看最近 30 秒、1 分钟或 2 分钟的已保存内容；录音不会暂停")
        self.review_button.clicked.connect(self.toggle_quick_review)
        self.material_toggle = QPushButton("课件")
        self.material_toggle.setToolTip("在课堂脉络与已导入课件之间切换")
        self.material_toggle.clicked.connect(self.toggle_materials)
        self.material_toggle.hide()
        self.fullscreen_button = QPushButton("全屏")
        self.fullscreen_button.setToolTip("切换全屏；全屏状态下也可以按 Esc 退出")
        self.fullscreen_button.clicked.connect(self.toggle_fullscreen)
        self.fullscreen_button.hide()
        self.important_button = QPushButton("☆ 重点")
        self.important_button.setToolTip("标记或取消当前句重点（Ctrl+1）")
        self.question_button = QPushButton("? 疑问")
        self.question_button.setToolTip("标记或取消当前句疑问（Ctrl+2）")
        self.important_button.clicked.connect(
            lambda checked=False: self.toggle_latest_marker("important")
        )
        self.question_button.clicked.connect(
            lambda checked=False: self.toggle_latest_marker("question")
        )
        self.important_shortcut = QShortcut(QKeySequence("Ctrl+1"), self)
        self.important_shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self.important_shortcut.activated.connect(
            lambda: self.toggle_latest_marker("important")
        )
        self.question_shortcut = QShortcut(QKeySequence("Ctrl+2"), self)
        self.question_shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self.question_shortcut.activated.connect(
            lambda: self.toggle_latest_marker("question")
        )
        self.pause_button.clicked.connect(self.toggle_pause)
        self.stop_button.clicked.connect(self.stop)
        control_layout.addWidget(self.material_toggle)
        control_layout.addWidget(self.fullscreen_button)
        control_layout.addWidget(self.review_button)
        control_layout.addWidget(self.reading_choice)
        control_layout.addWidget(self.important_button)
        control_layout.addWidget(self.question_button)
        control_layout.addStretch()
        control_layout.addWidget(self.pause_button)
        control_layout.addWidget(self.stop_button)
        control_layout.addStretch()
        controls.hide()
        self.layout.addWidget(controls)
        self._apply_reading_mode()
        self.refresh_devices()

    def _reading_choice_changed(self, index: int) -> None:
        mode = self.reading_choice.itemData(index)
        if mode not in READING_MODES:
            return
        self.reading_mode = mode
        self._apply_reading_mode()
        try:
            save_env_settings({"CLASSNOTE_READING_MODE": mode})
        except Exception as exc:
            self.notice.show_notice(f"阅读模式已切换，但未能记住选择：{friendly_error(exc)}", error=False)

    def _apply_reading_mode(self) -> None:
        if self.reading_mode == "english":
            self.partial.setStyleSheet(f"color:{COLORS['text']};font-size:20px;font-weight:550;line-height:1.6;")
            self.current_translation.setStyleSheet(f"color:{COLORS['muted']};font-size:16px;line-height:1.5;")
        elif self.reading_mode == "bilingual":
            self.partial.setStyleSheet(f"color:{COLORS['text']};font-size:18px;line-height:1.55;")
            self.current_translation.setStyleSheet(f"color:{COLORS['text']};font-size:18px;line-height:1.55;")
        else:
            self.partial.setStyleSheet(f"color:{COLORS['muted']};font-size:16px;line-height:1.5;")
            self.current_translation.setStyleSheet(f"color:{COLORS['text']};font-size:20px;font-weight:550;line-height:1.6;")
        for card in self.paragraph_cards:
            card.set_reading_mode(self.reading_mode)

    def on_materials_changed(self, available: bool, count: int) -> None:
        if available:
            try:
                self.course_context = build_course_context(self.materials.paths)
                hotwords = len(self.course_context.hotwords)
                terms = len(self.course_context.terms)
                self.material_state.setText(
                    f"已解析 {count} 份课件 · {hotwords} 个识别热词 · {terms} 条中英术语"
                )
                self.update_course_context_preview()
                if self.course_context.warnings:
                    self.notice.show_notice("；".join(self.course_context.warnings), error=False)
            except Exception as exc:
                self.course_context = CourseContext(source_count=count)
                self.material_state.setText(f"已导入 {count} 份课件 · 内容解析失败，但仍可查看")
                self.notice.show_notice(
                    f"课件解析失败，不影响开始课堂：{friendly_error(exc)}", error=False
                )
            self.import_materials_button.setText("继续导入")
            self.material_toggle.show()
            self.set_materials_visible(True)
        else:
            self.course_context = CourseContext()
            self.material_state.setText("未导入课件 · 字幕将使用更宽的阅读区域")
            self.import_materials_button.setText("导入课件（可选）")
            self.material_toggle.hide()
            self.set_materials_visible(False)
            self.update_course_context_preview()

    def update_course_context_preview(self) -> None:
        context = self.effective_course_context()
        if context.terms:
            self.summary.set_reference_terms(
                [
                    f"{english}：{chinese}"
                    for english, chinese in list(context.terms.items())[:30]
                ]
            )
        elif context.hotwords:
            self.summary.set_reference_terms(context.hotwords[:30])
        else:
            self.summary.set_reference_terms([])

    def effective_course_context(self) -> CourseContext:
        subject = self.subject_input.text().strip() or "通用课程"
        return merge_subject_context(
            self.course_context, self.repository.get_subject_terms(subject)
        )

    def manage_subject_terms(self) -> None:
        subject = self.subject_input.text().strip() or "通用课程"
        SubjectTermsDialog(self.repository, subject, self).exec()
        self.update_course_context_preview()

    def toggle_materials(self) -> None:
        if not self.materials.has_materials:
            self.materials.choose_files()
            return
        self.set_materials_visible(self.materials.isHidden())

    def set_materials_visible(self, visible: bool) -> None:
        visible = bool(visible and self.materials.has_materials)
        self.timeline.setVisible(not visible and self.session is not None)
        self.materials.setVisible(visible)
        self.material_toggle.setText(
            "课堂脉络" if visible and self.session is not None
            else "隐藏课件" if visible else "查看课件"
        )
        if visible or self.timeline.isVisible():
            total = max(980, self.workspace.width())
            context_width = max(210, min(290, int(total * 0.21)))
            summary_width = max(270, min(340, int(total * 0.24)))
            self.workspace.setSizes(
                [context_width if self.timeline.isVisible() else 0,
                 context_width if visible else 0,
                 max(500, total - context_width - summary_width), summary_width]
            )
        else:
            total = max(900, self.workspace.width())
            summary_width = max(280, min(360, int(total * 0.27)))
            self.workspace.setSizes([0, 0, max(560, total - summary_width), summary_width])

    def jump_to_topic(self, start_ms: int) -> None:
        if not self.paragraph_groups:
            return
        index = next(
            (index for index, group in enumerate(self.paragraph_groups)
             if paragraph_time_bounds(group)[1] >= start_ms),
            len(self.paragraph_groups) - 1,
        )
        self._jump_to_group(index)

    def jump_to_segment(self, segment_id: str) -> None:
        index = self.group_by_segment.get(segment_id)
        if index is None:
            return
        self._jump_to_group(index)

    def _jump_to_group(self, index: int) -> None:
        if self._view_dirty or not (self.visible_start <= index < self._visible_end()):
            start = (
                self.visible_start if self.visible_start <= index < self._visible_end()
                else index - self.VISIBLE_PARAGRAPHS // 2
            )
            self._render_paragraph_window(start)
        target = self.paragraph_cards[index - self.visible_start]
        self._jump_to_card(target)

    def _jump_to_card(self, target: ParagraphCard) -> None:
        if not self.quick_review.isHidden():
            self.quick_review.hide()
            self.recent_review.show()
            self.review_button.setText("回顾刚才")
        self.scroll.ensureWidgetVisible(target, 0, 8)
        self.auto_follow = False
        self.follow_timer.stop()
        self._show_return_to_live()
        target.highlight_for_review()

    def _visible_end(self) -> int:
        return self.visible_start + len(self.paragraph_cards)

    def _create_paragraph_card(self, group: list[Segment]) -> ParagraphCard:
        card = ParagraphCard(group[0])
        for segment in group[1:]:
            card.add_segment(segment)
        card.failed_ids = {
            segment.id for segment in group if segment.id in self.failed_segment_ids
        }
        if card.failed_ids:
            card.render()
        card.set_reading_mode(self.reading_mode)
        card.retry_requested.connect(self.retry_translation)
        card.marker_requested.connect(self.set_segment_marker)
        for segment in group:
            self.transcript_cards[segment.id] = card
        return card

    def _remove_paragraph_card(self, card: ParagraphCard) -> None:
        for segment in card.segments:
            self.transcript_cards.pop(segment.id, None)
        self.cards_layout.removeWidget(card)
        card.hide()
        card.setParent(None)
        card.deleteLater()

    def _render_paragraph_window(self, start: int) -> None:
        total = len(self.paragraph_groups)
        start = max(0, min(start, max(0, total - self.VISIBLE_PARAGRAPHS)))
        self._programmatic_scroll = True
        try:
            for card in self.paragraph_cards:
                self._remove_paragraph_card(card)
            self.paragraph_cards = []
            self.visible_start = start
            for group in self.paragraph_groups[start:start + self.VISIBLE_PARAGRAPHS]:
                card = self._create_paragraph_card(group)
                self.paragraph_cards.append(card)
                self.cards_layout.insertWidget(max(0, self.cards_layout.count() - 1), card)
            self.empty_state.setVisible(not total)
            self._view_dirty = False
        finally:
            self._programmatic_scroll = False
        self._update_history_navigation()

    def _update_history_navigation(self) -> None:
        total = len(self.paragraph_groups)
        if total:
            self.history_head.setText(
                f"课堂记录 · 第 {self.visible_start + 1}–{self._visible_end()} / {total} 段"
            )
        else:
            self.history_head.setText("课堂记录 · 按段落整理")
        self.older_button.setVisible(self.visible_start > 0)
        self.newer_button.setVisible(self._visible_end() < total)

    def show_older_paragraphs(self) -> None:
        if self.visible_start <= 0:
            return
        self.auto_follow = False
        self.follow_timer.stop()
        self._render_paragraph_window(self.visible_start - self.PAGE_PARAGRAPHS)
        self._programmatic_scroll = True
        try:
            self.scroll.verticalScrollBar().setValue(0)
        finally:
            self._programmatic_scroll = False
        self._show_return_to_live()

    def show_newer_paragraphs(self) -> None:
        if self._visible_end() >= len(self.paragraph_groups):
            return
        self.auto_follow = False
        self.follow_timer.stop()
        self._render_paragraph_window(self.visible_start + self.PAGE_PARAGRAPHS)
        self._programmatic_scroll = True
        try:
            self.scroll.verticalScrollBar().setValue(0)
        finally:
            self._programmatic_scroll = False
        self._show_return_to_live()

    def toggle_quick_review(self) -> None:
        if self.quick_review.isHidden():
            self.quick_review.set_segments(self._all_live_segments())
            self.recent_review.hide()
            self.quick_review.show()
            self.review_button.setText("收起回顾")
        else:
            self.close_quick_review()

    def close_quick_review(self) -> None:
        self.quick_review.hide()
        self.recent_review.show()
        self.review_button.setText("回顾刚才")
        self._scroll_to_latest()

    def _return_to_live(self) -> None:
        if self.quick_review.isHidden():
            self._scroll_to_latest()
        else:
            self.close_quick_review()

    def jump_from_quick_review(self, start_ms: int) -> None:
        self.quick_review.hide()
        self.recent_review.show()
        self.review_button.setText("回顾刚才")
        self.jump_to_topic(start_ms)

    def _all_live_segments(self) -> list[Segment]:
        return self.live_segments

    def set_fullscreen_state(self, fullscreen: bool) -> None:
        self.fullscreen_button.setText("退出全屏  Esc" if fullscreen else "进入全屏")

    def refresh_devices(self) -> None:
        self.device_combo.clear()
        try:
            self.devices = list_input_devices()
            self.device_combo.addItems([device.label for device in self.devices])
            microphone_count = sum(not device.is_loopback for device in self.devices)
            loopback_count = sum(device.is_loopback for device in self.devices)
            self.status.setText(
                f"找到 {microphone_count} 个麦克风"
                + (f"和 {loopback_count} 个系统声音源" if loopback_count else "")
            )
        except Exception as exc:
            self.devices = []
            self.status.setText(f"读取录音设备失败：{exc}")

    def test_audio_source(self) -> None:
        index = self.device_combo.currentIndex()
        if not (0 <= index < len(self.devices)):
            self.audio_test_status.setText("请先选择一个音源。")
            return
        device = self.devices[index]
        self.test_audio_button.setEnabled(False)
        self.audio_test_status.setText(
            "正在测试系统声音，请播放一段课程音频……"
            if device.is_loopback
            else "正在测试麦克风，请正常说一句英文……"
        )

        def worker() -> None:
            try:
                result = capture_audio_level(device)
                self.bridge.event.emit("audio_test_result", (device, result))
            except Exception as exc:
                self.bridge.event.emit("audio_test_error", str(exc))

        threading.Thread(target=worker, name="classnote-audio-test", daemon=True).start()

    def show_audio_test_result(self, device: AudioDevice, result: AudioLevelResult) -> None:
        self.test_audio_button.setEnabled(True)
        details = f"峰值 {result.peak_dbfs:.1f} dBFS · 平均 {result.rms_dbfs:.1f} dBFS"
        if result.status == "silent":
            message = (
                "没有检测到系统播放声音，请播放课程音频后重试。"
                if device.is_loopback
                else "几乎没有检测到声音，请检查麦克风权限、设备选择和输入音量。"
            )
            color = COLORS["danger"]
        elif result.status == "low":
            message = "音量偏小，远距离讲话可能漏词；建议靠近老师或提高输入音量。"
            color = "#9A6700"
        elif result.status == "clipping":
            message = "音量过大并出现爆音，数字和辅音可能识别错误；建议降低增益。"
            color = COLORS["danger"]
        else:
            message = "音源状态良好，适合实时课堂。"
            color = COLORS["success"]
        self.audio_test_status.setText(f"{message}  {details}")
        self.audio_test_status.setStyleSheet(f"color:{color}; font-size:12px;")

    @staticmethod
    def _set_quality_text(label: QLabel, text: str, color: str = COLORS["muted"]) -> None:
        label.setText(text)
        label.setStyleSheet(f"color:{color};font-size:12px;")

    def _set_runtime_status(self, message: str) -> None:
        if self.stop_armed:
            self._live_status_before_stop = message
        else:
            self.live_status.setText(message)

    def begin_quality_monitoring(self, device: AudioDevice, live_mode: str) -> None:
        self.quality_strip.show()
        self.runtime_title.setText(self.title_input.text().strip() or "英语课堂")
        self.live_status.setText("正在准备音频和识别模型")
        self.audio_meter.setValue(0)
        self.audio_meter.setStyleSheet("")
        source = "系统声音" if device.is_loopback else "麦克风"
        self._set_quality_text(self.audio_quality, f"{source} 等待声音")
        recognition = {
            "local": "识别 正在加载模型",
            "realtime": "识别 正在连接云端",
            "chunked": "识别 分段模式",
        }.get(live_mode, "识别 正在准备")
        self._set_quality_text(self.asr_quality, recognition)
        self._set_quality_text(self.translation_quality, "翻译 等待英文")
        self.drop_quality.hide()

    def update_audio_quality(self, metrics: object) -> None:
        values = metrics  # type: ignore[assignment]
        peak = float(values.get("peak_dbfs", -120.0))  # type: ignore[attr-defined]
        status = str(values.get("status", "silent"))  # type: ignore[attr-defined]
        silent_seconds = float(values.get("silent_seconds", 0.0))  # type: ignore[attr-defined]
        has_signal = bool(values.get("has_signal", False))  # type: ignore[attr-defined]
        dropped_ms = int(values.get("dropped_ms", 0))  # type: ignore[attr-defined]
        level_percent = max(0, min(100, int((peak + 60.0) / 60.0 * 100)))
        self.audio_meter.setValue(level_percent)

        if status == "clipping":
            text, color = "音源 过载", COLORS["danger"]
        elif silent_seconds >= 20:
            text, color = f"音源 静音 {int(silent_seconds)}s", COLORS["danger"]
        elif silent_seconds >= 8:
            text, color = f"音源 安静 {int(silent_seconds)}s", "#9A6700"
        elif not has_signal:
            text, color = "音源 等待声音", COLORS["muted"]
        elif status == "low":
            text, color = "音源 偏低", "#9A6700"
        else:
            text, color = "音源 正常", COLORS["muted"]
        self._set_quality_text(self.audio_quality, text, color)
        meter_color = COLORS["success"] if status == "good" else (
            color if color != COLORS["muted"] else COLORS["primary"]
        )
        self.audio_meter.setStyleSheet(
            "QProgressBar{border:none;background:#EAECF0;border-radius:3px;}"
            f"QProgressBar::chunk{{background:{meter_color};border-radius:3px;}}"
        )

        if dropped_ms <= 0:
            self.drop_quality.hide()
        elif dropped_ms < 500:
            self.drop_quality.show()
            self._set_quality_text(self.drop_quality, f"丢帧 {dropped_ms}ms", "#9A6700")
        else:
            self.drop_quality.show()
            self._set_quality_text(self.drop_quality, f"丢帧 {dropped_ms}ms", COLORS["danger"])

    def update_asr_quality(self, metrics: object) -> None:
        values = metrics  # type: ignore[assignment]
        inference = int(values.get("inference_ms", 0))  # type: ignore[attr-defined]
        audio = max(1, int(values.get("audio_ms", 1)))  # type: ignore[attr-defined]
        rtf = inference / audio
        if rtf < 0.35:
            state, color = "流畅", COLORS["muted"]
        elif rtf < 0.7:
            state, color = "正常", "#9A6700"
        else:
            state, color = "正在追赶", COLORS["danger"]
        self._set_quality_text(self.asr_quality, f"识别 {state} · {inference}ms", color)

    def update_translation_quality(self, metrics: object) -> None:
        values = metrics  # type: ignore[assignment]
        queued = int(values.get("translation_queue", 0))  # type: ignore[attr-defined]
        active = int(values.get("translation_active", 0))  # type: ignore[attr-defined]
        duration = int(values.get("last_translation_ms", 0))  # type: ignore[attr-defined]
        if queued >= 8:
            text, color = f"翻译 积压 {queued} 句", COLORS["danger"]
        elif queued >= 3:
            text, color = f"翻译 排队 {queued} 句", "#9A6700"
        elif queued or active:
            text, color = f"翻译 处理中{f' · {queued}句待处理' if queued else ''}", COLORS["muted"]
        elif duration:
            text, color = f"翻译 实时 · {duration}ms", COLORS["muted"]
        else:
            text, color = "翻译 等待英文", COLORS["muted"]
        self._set_quality_text(self.translation_quality, text, color)

    def start(self) -> None:
        if not self.devices or self.device_combo.currentIndex() < 0:
            self.notice.show_notice("没有找到可用的录音设备。请连接或启用麦克风后刷新设备。", error=True)
            return
        current_settings = Settings.load()
        selected_device = self.devices[self.device_combo.currentIndex()]
        if selected_device.is_loopback and current_settings.live_mode != "local":
            self.notice.show_notice(
                f"系统声音直采目前使用{local_speech_name()}识别。请在设置中选择“{local_speech_option()}”。",
                "前往设置",
                error=True,
            )
            return
        if current_settings.live_mode != "local" and not current_settings.api_key:
            self.notice.show_notice(
                f"云端实时转写尚未配置。请填写 OpenAI API Key，或改用{local_speech_name()}识别。",
                "前往设置",
                error=True,
            )
            return
        if not current_settings.text_api_key:
            provider = "DeepSeek" if current_settings.text_provider == "deepseek" else "文本服务"
            self.notice.show_notice(
                f"{provider} 密钥尚未配置，当前无法生成中文翻译和课堂笔记。",
                "前往设置",
                error=True,
            )
            return
        try:
            self.clear_session_content()
            self.session = LiveCourseSession(
                self.title_input.text().strip() or "英语课堂",
                self.subject_input.text().strip() or "通用课程",
                self.devices[self.device_combo.currentIndex()],
                lambda name, payload: self.bridge.event.emit(name, payload),
                self.effective_course_context(),
            )
            self.session.start()  # type: ignore[attr-defined]
            self.elapsed = 0
            self.timer.start(1000)
            self.notice.hide()
            self.info.hide()
            self.status.hide()
            self.preparation_host.hide()
            self.begin_quality_monitoring(selected_device, current_settings.live_mode)
            self.workspace.show()
            self.set_materials_visible(False)
            self.controls.show()
            self.set_immersive(True)
            self.fullscreen_button.show()
            self.set_fullscreen_state(True)
            if self.materials.has_materials:
                self.material_toggle.show()
            self.title_label.setText(self.title_input.text().strip() or "英语课堂")
            self.subtitle_label.setText("英语  →  简体中文")
            self.record_dot.setStyleSheet(f"color:{COLORS['danger']}; font-size:15px;")
            self.start_button.hide()
            self.pause_button.setEnabled(True)
            self.stop_button.setEnabled(True)
            self.important_button.setEnabled(True)
            self.question_button.setEnabled(True)
        except Exception as exc:
            self.session = None
            self.notice.show_notice(friendly_error(exc), "检查设置", error=True)

    def toggle_pause(self) -> None:
        if self.session is None:
            return
        self._disarm_stop()
        if self.paused:
            self.session.resume()  # type: ignore[attr-defined]
            self.pause_button.setText("暂停")
            self._set_quality_text(self.audio_quality, "音源 恢复中")
        else:
            self.session.pause()  # type: ignore[attr-defined]
            self.pause_button.setText("继续")
            self.audio_meter.setValue(0)
            self._set_quality_text(self.audio_quality, "音源 已暂停", COLORS["muted"])
        self.paused = not self.paused

    def stop(self, confirmed: bool = False) -> None:
        if self.session is None:
            return
        if not confirmed and not self.stop_armed:
            self.stop_armed = True
            self._live_status_before_stop = self.live_status.text()
            self.stop_button.setText("再次点击结束")
            self.stop_button.setToolTip("课堂仍在记录；4 秒内再次点击才会结束并整理")
            self.live_status.setText("课堂仍在继续 · 再次点击才会结束")
            self.stop_arm_timer.start()
            return
        self._disarm_stop(restore_status=False)
        self.session.stop()  # type: ignore[attr-defined]
        self.timer.stop()
        self.status.setText("正在结束录音并整理最后的课堂内容，请不要关闭软件……")
        self.live_status.setText("正在结束录音并整理课堂内容")
        self._set_quality_text(self.asr_quality, "识别 正在收尾")
        self._set_quality_text(self.translation_quality, "翻译 正在收尾")
        self.pause_button.setEnabled(False)
        self.stop_button.setEnabled(False)
        self.important_button.setEnabled(False)
        self.question_button.setEnabled(False)

    def _disarm_stop(self, restore_status: bool = True) -> None:
        was_armed = self.stop_armed
        self.stop_arm_timer.stop()
        self.stop_armed = False
        self.stop_button.setText("结束并整理")
        self.stop_button.setToolTip("首次点击只会请求确认；4 秒内再次点击才会结束课堂")
        if restore_status and was_armed and self._live_status_before_stop:
            self.live_status.setText(self._live_status_before_stop)
        self._live_status_before_stop = ""

    def handle_event(self, name: str, payload: object) -> None:
        if name == "status":
            self.status.setText(str(payload))
            self._set_runtime_status(str(payload))
            message = str(payload)
            if "低延迟连接已建立" in message or "转写会话已就绪" in message:
                self._set_quality_text(self.asr_quality, "识别 云端实时", COLORS["muted"])
        elif name == "partial":
            _, text = payload  # type: ignore[misc]
            self.partial.setText(str(text))
            self.current_translation.setText("正在等待完整英文句子……")
        elif name == "final_original":
            _, text, _ = payload  # type: ignore[misc]
            self.partial.setText(str(text))
            self.current_translation.setText("正在翻译……")
        elif name == "segment_original":
            self.add_segment(payload, pending=True)  # type: ignore[arg-type]
        elif name == "translation_delta":
            segment_id, text = payload  # type: ignore[misc]
            self.update_translation(str(segment_id), str(text), final=False)
        elif name == "segment_update":
            segment = payload  # type: ignore[assignment]
            self.update_translation(segment.id, segment.translated_text)  # type: ignore[attr-defined]
            if segment.id == self.latest_segment_id:  # type: ignore[attr-defined]
                self.summary.observe_segment(segment)  # type: ignore[arg-type]
        elif name == "summary_update":
            if isinstance(payload, LiveSummarySnapshot):
                self.summary.apply_snapshot(payload)
                self.recent_review.set_topic(payload.topic)
                self.quick_review.set_topic(payload.topic)
                if payload.topic and self.live_segments:
                    latest = self.segments_by_id.get(self.latest_segment_id)
                    start_ms = latest.start_ms if latest is not None else 0
                    if not self.timeline.entries:
                        start_ms = min(item.start_ms for item in self.live_segments)
                    if self.timeline.add_topic(start_ms, payload.topic):
                        result = getattr(self.session, "result", None)
                        if result is not None:
                            try:
                                CourseRepository(Settings.load().database_path).add_course_topic(
                                    result.id, start_ms, payload.topic
                                )
                            except Exception as exc:
                                self.notice.show_notice(
                                    f"课堂主题未能保存：{friendly_error(exc)}", error=False
                                )
        elif name == "summary_status":
            self.summary.set_status(payload)
        elif name == "usage_update" and isinstance(payload, dict):
            requests = int(payload.get("requests", 0))
            cost = payload.get("estimated_cost_usd", 0)
            unknown = int(payload.get("unknown_requests", 0))
            unpriced = int(payload.get("unpriced_requests", 0))
            self.usage_quality.setText(
                f"文本约 ${cost:.6f}" if requests > unknown + unpriced
                else f"文本 {requests} 次 · 费用未知"
            )
            self.usage_quality.setToolTip(format_usage_summary(payload))
        elif name == "budget_update" and isinstance(payload, dict):
            state = str(payload.get("state", ""))
            limit = payload.get("limit_usd", 0)
            labels = {
                "unavailable": "预算 无法核算",
                "saving": "预算 80% · 摘要暂停",
                "limit": "预算用完 · 英文继续",
            }
            self.budget_quality.show()
            self.budget_quality.setText(labels.get(state, f"预算 ${limit}"))
            self.budget_quality.setToolTip(
                f"文本预算 ${limit}，当前估算 ${payload.get('spent_usd', 0)}。"
                "并发请求可能产生少量超额；云端语音费用不包含在内。"
            )
            if state in {"saving", "limit"}:
                self.summary.set_status({"state": "paused_budget"})
        elif name == "capture_started":
            self._set_quality_text(self.audio_quality, "音源 已开始采集", COLORS["success"])
            self._set_quality_text(self.asr_quality, "识别 模型准备中")
            self._set_runtime_status("音频已开始采集 · 开场内容正在缓存")
        elif name == "capture_buffer":
            metrics = payload if isinstance(payload, dict) else {}
            buffered_ms = int(metrics.get("buffered_ms", 0))
            self._set_quality_text(
                self.asr_quality,
                f"识别 准备中 · 缓存 {buffered_ms / 1000:.0f}s",
            )
            self._set_runtime_status(
                f"音频持续采集中 · 已缓存开场内容 {buffered_ms / 1000:.0f}s"
            )
        elif name == "model_ready":
            metrics = payload if isinstance(payload, dict) else {}
            buffered_ms = int(metrics.get("buffered_ms", 0))
            self._set_quality_text(self.asr_quality, "识别 本地 GPU 已就绪", COLORS["success"])
            self._set_runtime_status(
                f"本地模型已就绪 · 正在处理缓存音频 {buffered_ms / 1000:.1f}s"
            )
        elif name == "partial_clear":
            if self.partial.text().startswith("等待"):
                self.current_translation.setText("翻译会显示在这里。")
        elif name == "segment":
            self.add_segment(payload)  # type: ignore[arg-type]
        elif name == "translation_failed":
            segment_id, _ = payload  # type: ignore[misc]
            self.failed_segment_ids.add(str(segment_id))
            saved = self.segments_by_id.get(str(segment_id))
            if saved is not None:
                saved.translated_text = ""
            card = self.transcript_cards.get(str(segment_id))
            if card is not None:
                card.update_translation(str(segment_id), "", failed=True)
            if str(segment_id) == self.latest_segment_id:
                self.current_translation.setText(
                    "本句翻译暂时失败 · 英文已保存，可稍后在课程库补译"
                )
        elif name == "segment_retrying":
            segment_id = str(payload)
            self.failed_segment_ids.discard(segment_id)
            saved = self.segments_by_id.get(segment_id)
            if saved is not None:
                saved.translated_text = ""
            card = self.transcript_cards.get(segment_id)
            if card is not None:
                card.set_retrying(segment_id)
            if segment_id == self.latest_segment_id:
                self.current_translation.setText("正在重新翻译……")
        elif name == "metrics":
            self.update_translation_quality(payload)
        elif name == "asr_metrics":
            self.update_asr_quality(payload)
        elif name == "audio_metrics":
            self.update_audio_quality(payload)
        elif name == "audio_test_result":
            device, result = payload  # type: ignore[misc]
            self.show_audio_test_result(device, result)
        elif name == "audio_test_error":
            self.test_audio_button.setEnabled(True)
            self.audio_test_status.setText(f"音源测试失败：{friendly_error(payload)}")
            self.audio_test_status.setStyleSheet(f"color:{COLORS['danger']}; font-size:12px;")
        elif name == "warning":
            self.notice.show_notice(friendly_error(payload), error=False)
        elif name == "finished":
            result, path = payload  # type: ignore[misc]
            self.status.setText(f"课堂笔记已导出：{path}")
            self.reset()
            show_message(self, QMessageBox.Icon.Information, "课堂已整理", f"《{result.title}》已完成。\n\n笔记位置：{path}")
        elif name == "error":
            self.notice.show_notice(friendly_error(payload), "检查设置", error=True)
            self.reset()

    def add_segment(self, segment: Segment, pending: bool = False) -> None:
        if segment.id in self.segments_by_id:
            if segment.translated_text.strip():
                self.update_translation(segment.id, segment.translated_text)
            return
        current_latest = self.segments_by_id.get(self.latest_segment_id)
        late_arrival = current_latest is not None and (
            segment.start_ms, segment.end_ms
        ) < (current_latest.start_ms, current_latest.end_ms)
        if not late_arrival:
            self.latest_segment_id = segment.id
            self.partial.setText(segment.original_text)
            self.current_translation.setText(segment.translated_text or "正在翻译……")
            if not pending:
                self.summary.observe_segment(segment)
            self._sync_marker_controls(segment.marker)
        self.empty_state.hide()
        self.segments_by_id[segment.id] = segment
        self.live_segments.append(segment)
        if late_arrival:
            self._rebuild_paragraphs_after_late_arrival()
        else:
            self._append_current_paragraph(segment)
        self.saved_segment_count += 1
        self.save_quality.setText(f"已保存 {self.saved_segment_count} 句")
        segments = self._all_live_segments()
        self.recent_review.set_segments(segments)
        self.summary.set_marked_contexts(build_marked_contexts(segments))
        if not self.quick_review.isHidden():
            self.quick_review.set_segments(segments)
        self._schedule_transcript_follow()

    def _append_current_paragraph(self, segment: Segment) -> None:
        prior_group_count = len(self.paragraph_groups)
        new_group = not self.paragraph_groups or should_start_new_paragraph(
            self.paragraph_groups[-1], segment
        )
        if new_group:
            self.paragraph_groups.append([segment])
        else:
            self.paragraph_groups[-1].append(segment)
        self.group_by_segment[segment.id] = len(self.paragraph_groups) - 1
        if not self.paragraph_cards and prior_group_count == 0:
            self._render_paragraph_window(0)
        elif (
            self.auto_follow
            and self._visible_end() == prior_group_count
            and not self._view_dirty
        ):
            if new_group:
                if len(self.paragraph_cards) >= self.VISIBLE_PARAGRAPHS:
                    self._remove_paragraph_card(self.paragraph_cards.pop(0))
                    self.visible_start += 1
                card = self._create_paragraph_card([segment])
                self.paragraph_cards.append(card)
                self.cards_layout.insertWidget(max(0, self.cards_layout.count() - 1), card)
            elif self.paragraph_cards:
                self.paragraph_cards[-1].add_segment(segment)
                self.transcript_cards[segment.id] = self.paragraph_cards[-1]
            self.empty_state.hide()
        else:
            self._view_dirty = True
        self._update_history_navigation()

    def _rebuild_paragraphs_after_late_arrival(self) -> None:
        anchor_id = ""
        if self.paragraph_cards and self.visible_start < len(self.paragraph_groups):
            anchor_id = self.paragraph_groups[self.visible_start][0].id
        scroll_bar = self.scroll.verticalScrollBar()
        old_scroll_value = scroll_bar.value()
        self.paragraph_groups = group_segments(self.live_segments)
        self.group_by_segment = {
            item.id: index
            for index, group in enumerate(self.paragraph_groups)
            for item in group
        }
        if self.auto_follow:
            start = max(0, len(self.paragraph_groups) - self.VISIBLE_PARAGRAPHS)
        else:
            start = self.group_by_segment.get(anchor_id, self.visible_start)
        self._render_paragraph_window(start)
        if not self.auto_follow:
            self._programmatic_scroll = True
            try:
                scroll_bar.setValue(min(old_scroll_value, scroll_bar.maximum()))
            finally:
                self._programmatic_scroll = False

    def toggle_latest_marker(self, marker: str) -> None:
        if not self.latest_segment_id:
            return
        segment = self.segments_by_id.get(self.latest_segment_id)
        if segment is None:
            return
        updated = "" if segment.marker == marker else marker
        self.set_segment_marker(self.latest_segment_id, updated)

    def set_segment_marker(self, segment_id: str, marker: str) -> None:
        if self.session is None:
            self.notice.show_notice("课堂已结束；请在下一节课堂中标记新记录。", error=False)
            return
        segment = self.segments_by_id.get(segment_id)
        if segment is None:
            return
        setter = getattr(self.session, "set_segment_marker", None)
        if not callable(setter):
            self.notice.show_notice("当前课堂模式暂不支持重点标记。", error=False)
            return
        try:
            setter(segment_id, marker)
            segment.marker = marker
            card = self.transcript_cards.get(segment_id)
            if card is not None:
                card.set_marker(segment_id, marker)
            if segment_id == self.latest_segment_id:
                self._sync_marker_controls(marker)
            self.summary.set_marked_contexts(
                build_marked_contexts(self._all_live_segments())
            )
        except Exception as exc:
            self.notice.show_notice(f"标记没有保存：{friendly_error(exc)}", error=True)

    def _sync_marker_controls(self, marker: str) -> None:
        self.important_button.setText("★ 已标重点" if marker == "important" else "☆ 重点")
        self.question_button.setText("? 已标疑问" if marker == "question" else "? 疑问")

    def retry_translation(self, segment_id: str) -> None:
        if self.session is None:
            return
        retry = getattr(self.session, "retry_translation", None)
        if not callable(retry):
            self.notice.show_notice("当前课堂模式请在课程库中补译失败内容。", error=False)
            return
        card = self.transcript_cards.get(segment_id)
        self.failed_segment_ids.discard(segment_id)
        saved = self.segments_by_id.get(segment_id)
        if saved is not None:
            saved.translated_text = ""
        if card is not None:
            card.set_retrying(segment_id)
        if segment_id == self.latest_segment_id:
            self.current_translation.setText("正在重新翻译……")
        try:
            retry(segment_id)
        except Exception as exc:
            self.failed_segment_ids.add(segment_id)
            if card is not None:
                card.update_translation(segment_id, "", failed=True)
            if segment_id == self.latest_segment_id:
                self.current_translation.setText("重试暂未开始 · 英文仍已安全保存")
            self.notice.show_notice(f"暂时无法重试：{friendly_error(exc)}", error=True)

    def update_translation(self, segment_id: str, text: str, final: bool = True) -> None:
        saved = self.segments_by_id.get(segment_id)
        if saved is not None:
            saved.translated_text = text
        if text:
            self.failed_segment_ids.discard(segment_id)
        card = self.transcript_cards.get(segment_id)
        if card is not None:
            card.update_translation(segment_id, text, final=final)
        if text and segment_id == self.latest_segment_id:
            self.current_translation.setText(text)
        if final:
            segments = self._all_live_segments()
            self.recent_review.set_segments(segments)
            self.summary.set_marked_contexts(build_marked_contexts(segments))
            if not self.quick_review.isHidden():
                self.quick_review.set_segments(segments)

    def _schedule_transcript_follow(self) -> None:
        if self.auto_follow:
            if not self.follow_timer.isActive():
                self.follow_timer.start(50)
            return
        self.unseen_segments += 1
        self._show_return_to_live()

    def _follow_latest_if_enabled(self) -> None:
        if self.auto_follow:
            self._scroll_to_latest()

    def _show_return_to_live(self) -> None:
        label = "回到实时"
        if self.unseen_segments:
            label += f" · {self.unseen_segments} 条新字幕"
        self.new_items_button.setText(label)
        self.new_items_button.show()

    def _on_transcript_scroll(self, value: int) -> None:
        if self._programmatic_scroll:
            return
        bar = self.scroll.verticalScrollBar()
        near_bottom = (
            bar.maximum() - value <= 40
            and self._visible_end() == len(self.paragraph_groups)
            and not self._view_dirty
        )
        if near_bottom:
            self.auto_follow = True
            self.unseen_segments = 0
            self.new_items_button.hide()
        else:
            self.auto_follow = False
            self.follow_timer.stop()
            self._show_return_to_live()

    def _scroll_to_latest(self) -> None:
        self.follow_timer.stop()
        latest_start = max(0, len(self.paragraph_groups) - self.VISIBLE_PARAGRAPHS)
        if self._view_dirty or self.visible_start != latest_start:
            self._render_paragraph_window(latest_start)
        bar = self.scroll.verticalScrollBar()
        self._programmatic_scroll = True
        try:
            bar.setValue(bar.maximum())
        finally:
            self._programmatic_scroll = False
        self.auto_follow = True
        self.unseen_segments = 0
        self.new_items_button.setText("回到实时")
        self.new_items_button.hide()

    def clear_session_content(self) -> None:
        self.follow_timer.stop()
        self.quick_review.hide()
        self.quick_review.reset()
        self.recent_review.show()
        self.review_button.setText("回顾刚才")
        self.timeline.reset()
        self.transcript_cards.clear()
        self.paragraph_cards.clear()
        self.paragraph_groups.clear()
        self.group_by_segment.clear()
        self.segments_by_id.clear()
        self.failed_segment_ids.clear()
        self.visible_start = 0
        self._view_dirty = False
        self.live_segments = []
        self.latest_segment_id = ""
        self.auto_follow = True
        self.unseen_segments = 0
        self.saved_segment_count = 0
        self.save_quality.setText("已保存 0 句")
        self.usage_quality.setText("文本用量 等待")
        self.usage_quality.setToolTip("仅统计文本服务返回的 Token；费用为单价快照估算，不含云端语音")
        self.budget_quality.setText("课堂预算 关闭")
        self.budget_quality.hide()
        self._sync_marker_controls("")
        self.new_items_button.setText("回到实时")
        self.new_items_button.hide()
        self._update_history_navigation()
        for index in range(self.cards_layout.count() - 1, -1, -1):
            widget = self.cards_layout.itemAt(index).widget()
            if widget is not None and widget is not self.empty_state:
                self.cards_layout.takeAt(index)
                widget.deleteLater()
        self.empty_state.show()
        self.partial.setText("等待英文语音……")
        self.current_translation.setText("翻译会显示在这里。")
        self.recent_review.reset()
        self.summary.reset()
        self.update_course_context_preview()

    def reset(self) -> None:
        self.clear_session_content()
        self.quick_review.hide()
        self.recent_review.show()
        self.review_button.setText("回顾刚才")
        self.session = None
        self.paused = False
        self.timer.stop()
        self._disarm_stop(restore_status=False)
        self.record_dot.setStyleSheet(f"color:{COLORS['muted']}; font-size:15px;")
        self.start_button.setEnabled(True)
        self.start_button.show()
        self.pause_button.setEnabled(False)
        self.pause_button.setText("暂停")
        self.stop_button.setEnabled(False)
        self.important_button.setEnabled(False)
        self.question_button.setEnabled(False)
        self.info.show()
        self.status.show()
        self.preparation_host.show()
        self.quality_strip.hide()
        self.workspace.hide()
        self.timeline.hide()
        self.controls.hide()
        self.fullscreen_button.hide()
        if not self.materials.has_materials:
            self.material_toggle.hide()
        self.set_immersive(False)
        self.title_label.setText("实时课堂")
        self.subtitle_label.setText("低延迟英文字幕、中文翻译和自动课堂整理。")

    def _tick(self) -> None:
        if not self.paused:
            self.elapsed += 1
        self.time_label.setText(f"{self.elapsed // 3600:02d}:{(self.elapsed % 3600) // 60:02d}:{self.elapsed % 60:02d}")


class DropZone(QFrame):
    file_dropped = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self.setAcceptDrops(True)
        self.setObjectName("Card")
        self.setMinimumHeight(190)
        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon = QLabel("↑")
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon.setStyleSheet(f"font-size:30px; color:{COLORS['primary']};")
        title = QLabel("拖入课堂录音，或点击选择文件")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setStyleSheet("font-size:16px; font-weight:650;")
        subtitle = QLabel("MP3 · WAV · M4A · MP4 · WEBM · FLAC")
        subtitle.setObjectName("Muted")
        subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(icon)
        layout.addWidget(title)
        layout.addWidget(subtitle)

    def mousePressEvent(self, event) -> None:  # type: ignore[override]
        from PySide6.QtWidgets import QFileDialog

        path, _ = QFileDialog.getOpenFileName(self, "选择课堂录音", "", "音频和视频 (*.mp3 *.wav *.m4a *.mp4 *.mpeg *.mpga *.ogg *.webm *.flac)")
        if path:
            self.file_dropped.emit(path)

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent) -> None:
        urls = event.mimeData().urls()
        if urls:
            self.file_dropped.emit(urls[0].toLocalFile())


class FilePage(Page):
    def __init__(self, bridge: Bridge, repository: CourseRepository) -> None:
        super().__init__("文件转写", "导入已有课堂录音，生成英中记录和结构化笔记。")
        self.bridge = bridge
        self.repository = repository
        self.path = ""
        self.drop = DropZone()
        self.drop.file_dropped.connect(self.select_file)
        self.layout.addWidget(self.drop)
        form = QFrame()
        form.setObjectName("Card")
        form_layout = QVBoxLayout(form)
        form_layout.setContentsMargins(18, 16, 18, 16)
        self.file_label = QLabel("尚未选择文件")
        self.file_label.setObjectName("Muted")
        fields = QHBoxLayout()
        self.title_input = QLineEdit()
        self.title_input.setPlaceholderText("课程名称")
        self.subject_input = QLineEdit("通用课程")
        self.subject_input.setPlaceholderText("课程领域")
        fields.addWidget(self.title_input, 2)
        fields.addWidget(self.subject_input, 1)
        terms_button = QPushButton("术语管理")
        terms_button.setToolTip("管理当前课程领域的本地中英术语")
        terms_button.clicked.connect(self.manage_subject_terms)
        fields.addWidget(terms_button)
        self.start_button = QPushButton("开始处理")
        self.start_button.setObjectName("Primary")
        self.demo_button = QPushButton("运行离线演示")
        buttons = QHBoxLayout()
        buttons.addStretch()
        buttons.addWidget(self.demo_button)
        buttons.addWidget(self.start_button)
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.hide()
        self.status = QLabel("")
        self.status.setObjectName("Muted")
        form_layout.addWidget(self.file_label)
        form_layout.addLayout(fields)
        form_layout.addWidget(self.progress)
        form_layout.addWidget(self.status)
        form_layout.addLayout(buttons)
        self.layout.addWidget(form)
        self.layout.addStretch()
        self.start_button.clicked.connect(lambda: self.start(False))
        self.demo_button.clicked.connect(lambda: self.start(True))

    def manage_subject_terms(self) -> None:
        subject = self.subject_input.text().strip() or "通用课程"
        SubjectTermsDialog(self.repository, subject, self).exec()

    def select_file(self, path: str) -> None:
        self.path = path
        file = Path(path)
        self.file_label.setText(f"{file.name}  ·  {file.parent}")
        if not self.title_input.text().strip():
            self.title_input.setText(file.stem)

    def start(self, demo: bool) -> None:
        if not demo and not self.path:
            show_message(self, QMessageBox.Icon.Warning, "还没有选择文件", "请把课堂录音拖到上方区域，或者点击该区域选择文件。")
            return
        self.start_button.setEnabled(False)
        self.demo_button.setEnabled(False)
        self.progress.show()
        audio = Path(__file__) if demo else Path(self.path)
        title = self.title_input.text().strip() or (f"{APP_NAME}演示课程" if demo else audio.stem)
        subject = self.subject_input.text().strip() or "通用课程"

        def worker() -> None:
            try:
                result, exported = process_course(
                    audio,
                    title,
                    subject,
                    demo=demo,
                    progress=lambda message: self.bridge.event.emit("file_status", message),
                )
                self.bridge.event.emit("file_finished", (result, exported))
            except Exception as exc:
                self.bridge.event.emit("file_error", str(exc))

        threading.Thread(target=worker, daemon=True).start()

    def handle_event(self, name: str, payload: object) -> None:
        if name == "file_status":
            self.status.setText(str(payload))
        elif name == "file_finished":
            result, path = payload  # type: ignore[misc]
            self.finish()
            usage = self.repository.get_text_usage_summary(result.id)
            show_message(
                self, QMessageBox.Icon.Information, "处理完成",
                f"《{result.title}》笔记已导出。\n\n文件位置：{path}\n\n{format_usage_summary(usage)}",
            )
        elif name == "file_error":
            self.finish()
            show_message(self, QMessageBox.Icon.Critical, "处理失败", friendly_error(payload))

    def finish(self) -> None:
        self.progress.hide()
        self.start_button.setEnabled(True)
        self.demo_button.setEnabled(True)


class LibraryPage(Page):
    recovery_event = Signal(str, object)

    def __init__(self, repository: CourseRepository) -> None:
        super().__init__("课程库", "搜索并回看已经保存的课堂记录。")
        self.repository = repository
        self._recovery_running = False
        self.rows = []
        self.search = QLineEdit()
        self.search.setPlaceholderText("搜索课程、逐字稿、翻译或笔记……")
        self.search.textChanged.connect(self.reload)
        toolbar = QHBoxLayout()
        toolbar.addWidget(self.search, 1)
        refresh = QPushButton("刷新")
        refresh.setToolTip("重新读取课程数据库")
        refresh.clicked.connect(self.reload)
        self.recover_button = QPushButton("补译并整理")
        self.recover_button.setToolTip("补译失败或中断的句子，并重新生成课堂笔记")
        self.recover_button.setEnabled(False)
        self.recover_button.clicked.connect(self.recover_selected)
        self.edit_button = QPushButton("校对字幕")
        self.edit_button.setToolTip("逐句修改已结束课堂的英文和中文；保存后更新本地对照笔记")
        self.edit_button.setEnabled(False)
        self.edit_button.clicked.connect(self.edit_selected)
        self.delete_button = QPushButton("删除记录")
        self.delete_button.setObjectName("Danger")
        self.delete_button.setToolTip("删除选中的软件内课程记录；不会删除已导出的笔记文件")
        self.delete_button.setEnabled(False)
        self.delete_button.clicked.connect(self.delete_selected)
        toolbar.addWidget(refresh)
        toolbar.addWidget(self.recover_button)
        toolbar.addWidget(self.edit_button)
        toolbar.addWidget(self.delete_button)
        self.layout.addLayout(toolbar)
        content = QHBoxLayout()
        self.list = QListWidget()
        self.list.setMinimumWidth(310)
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.list.currentRowChanged.connect(self.selection_changed)
        self.delete_shortcut = QShortcut(QKeySequence(Qt.Key.Key_Delete), self.list)
        self.delete_shortcut.setContext(Qt.ShortcutContext.WidgetShortcut)
        self.delete_shortcut.activated.connect(self.delete_selected)
        self.preview = QTextBrowser()
        self.preview.setOpenExternalLinks(False)
        content.addWidget(self.list, 1)
        content.addWidget(self.preview, 2)
        self.layout.addLayout(content, 1)
        self.recovery_event.connect(self.handle_recovery_event)
        self.reload()

    def reload(self, *_: object, preferred_index: int | None = None) -> None:
        self.rows = self.repository.list_courses(query=self.search.text())
        self.list.clear()
        for row in self.rows:
            try:
                created = datetime.fromisoformat(str(row["created_at"])).astimezone().strftime(
                    "%Y-%m-%d %H:%M"
                )
            except ValueError:
                created = str(row["created_at"])
            count = int(row["segment_count"])
            pending = int(row["pending_count"])
            status = COURSE_STATUS_LABELS.get(str(row["status"]), str(row["status"]))
            state = f"{status} · {count} 句 · {format_duration(int(row['duration_ms']))}"
            if pending:
                state += f" · {pending} 句待补译"
            item = QListWidgetItem(f"{row['title']}\n{row['subject']} · {state}\n{created}")
            item.setSizeHint(QSize(0, 72))
            item.setToolTip(f"{row['title']}\n单击查看；按 Delete 可删除记录")
            self.list.addItem(item)
        if self.rows:
            target = 0 if preferred_index is None else min(max(0, preferred_index), len(self.rows) - 1)
            self.list.setCurrentRow(target)
        else:
            self.delete_button.setEnabled(False)
            self.recover_button.setEnabled(False)
            self.edit_button.setEnabled(False)
            self.preview.setHtml(
                "<div style='color:#777;padding:24px'>"
                "<h3>没有找到课程记录</h3>"
                "<p>完成一节课堂后，英文、中文和整理笔记会出现在这里。</p></div>"
            )

    def selection_changed(self, index: int) -> None:
        valid = 0 <= index < len(self.rows)
        self.delete_button.setEnabled(valid and not self._recovery_running)
        recoverable = False
        editable = False
        if valid:
            row = self.rows[index]
            editable = int(row["segment_count"]) > 0 and str(row["status"]) not in {
                "recording", "transcribing", "translating", "organizing"
            }
            recoverable = int(row["segment_count"]) > 0 and (
                int(row["pending_count"]) > 0
                or str(row["status"]) in {"needs_attention", "interrupted", "failed"}
            )
            self.recover_button.setText(
                "补译并整理" if int(row["pending_count"]) else "重新整理"
            )
        self.recover_button.setEnabled(recoverable and not self._recovery_running)
        self.edit_button.setEnabled(editable and not self._recovery_running)
        if valid:
            self.show_course(index)

    def select_course(self, course_id: str) -> bool:
        self.reload()
        for index, row in enumerate(self.rows):
            if str(row["id"]) == course_id:
                self.list.setCurrentRow(index)
                return True
        self.list.setCurrentRow(-1)
        return False

    def delete_selected(self) -> None:
        if self._recovery_running:
            return
        index = self.list.currentRow()
        if not (0 <= index < len(self.rows)):
            return
        row = self.rows[index]
        title = str(row["title"])
        count = int(row["segment_count"])
        if not confirm_course_delete(self, title, count):
            return
        if self.repository.delete_course(str(row["id"])):
            self.reload(preferred_index=index)
        else:
            show_message(self, QMessageBox.Icon.Warning, "记录不存在", "这条课程记录可能已经被删除，请刷新后重试。")

    def edit_selected(self) -> None:
        index = self.list.currentRow()
        if self._recovery_running or not (0 <= index < len(self.rows)):
            return
        row = self.rows[index]
        if not self.edit_button.isEnabled():
            return
        course_id = str(row["id"])
        dialog = SegmentEditorDialog(self.repository, course_id, str(row["title"]), self)
        dialog.exec()
        if dialog.saved_any:
            self.select_course(course_id)

    def recover_selected(self) -> None:
        index = self.list.currentRow()
        if self._recovery_running or not (0 <= index < len(self.rows)):
            return
        row = self.rows[index]
        course_id = str(row["id"])
        self._recovery_running = True
        self.recover_button.setEnabled(False)
        self.edit_button.setEnabled(False)
        self.delete_button.setEnabled(False)
        self.preview.setMarkdown(
            f"# {row['title']}\n\n正在补译和重新整理，请不要关闭软件……"
        )

        def worker() -> None:
            try:
                from .recovery import recover_course

                result, path = recover_course(
                    course_id,
                    self.repository,
                    progress=lambda message: self.recovery_event.emit("progress", message),
                )
                self.recovery_event.emit("finished", (result, path, index))
            except Exception as exc:
                self.recovery_event.emit("error", (str(exc), index))

        threading.Thread(
            target=worker, name="classnote-course-recovery", daemon=True
        ).start()

    def handle_recovery_event(self, name: str, payload: object) -> None:
        if name == "progress":
            self.preview.setMarkdown(f"# 正在处理课程\n\n{payload}")
            return
        self._recovery_running = False
        if name == "finished":
            result, path, index = payload  # type: ignore[misc]
            self.reload(preferred_index=int(index))
            show_message(
                self,
                QMessageBox.Icon.Information,
                "课程已恢复",
                f"《{result.title}》已经补译并重新整理。\n\n笔记位置：{path}",
            )
            return
        message, index = payload  # type: ignore[misc]
        self.reload(preferred_index=int(index))
        show_message(self, QMessageBox.Icon.Critical, "课程恢复失败", friendly_error(message))

    def show_course(self, index: int) -> None:
        if not (0 <= index < len(self.rows)):
            return
        row = self.rows[index]
        saved_segments = self.repository.get_course_segments(str(row["id"]))
        segments = [
            Segment(
                original_text=str(item["original_text"] or ""),
                translated_text=str(item["translated_text"] or ""),
                start_ms=int(item["start_ms"]),
                end_ms=int(item["end_ms"]),
                id=str(item["id"]),
                marker=str(item["marker"] or ""),
            )
            for item in saved_segments
        ]
        notes = str(row["notes_markdown"] or "").strip()
        topics = self.repository.get_course_topics(str(row["id"]))
        topics_section = "\n".join(
            f"- {format_duration(int(topic['start_ms']))}　{str(topic['title'])}"
            for topic in topics
        ) or "> 本节课尚未生成课堂主题。"
        status = COURSE_STATUS_LABELS.get(str(row["status"]), str(row["status"]))
        pending_count = int(row["pending_count"])
        lifecycle = f"状态：{status} · {row['segment_count']} 句 · {format_duration(int(row['duration_ms']))}"
        if pending_count:
            lifecycle += f" · {pending_count} 句待补译"
        error_message = str(row["error_message"] or "").strip()
        issue = f"\n\n> {error_message}" if error_message else ""
        transcript = []
        for paragraph in group_segments(segments):
            start_ms, end_ms = paragraph_time_bounds(paragraph)
            start_seconds = max(0, start_ms // 1000)
            end_seconds = max(0, end_ms // 1000)
            stamp = (
                f"{start_seconds // 60:02d}:{start_seconds % 60:02d}–"
                f"{end_seconds // 60:02d}:{end_seconds % 60:02d} · {len(paragraph)} 句"
            )
            markers = {segment.marker for segment in paragraph}
            if "important" in markers:
                stamp += " · ⭐ 重点"
            if "question" in markers:
                stamp += " · ❓ 疑问"
            original = " ".join(segment.original_text.strip() for segment in paragraph)
            translated = " ".join(
                segment.translated_text.strip()
                for segment in paragraph
                if segment.translated_text.strip()
            )
            block = f"### {stamp}\n\n**English**\n\n{original}"
            if translated:
                block += f"\n\n**中文**\n\n{translated}"
            transcript.append(block)
        notes_section = notes or "> 这节课尚未生成整理笔记，已保存的逐字稿仍可在下方查看。"
        marked_section = marked_contexts_markdown(build_marked_contexts(segments))
        if marked_section:
            marked_section = f"---\n\n{marked_section}\n\n"
        transcript_section = "\n\n---\n\n".join(transcript) or "> 没有保存到有效字幕。这通常表示课堂在音频设备或模型启动阶段就已停止。"
        usage_section = format_usage_summary(
            self.repository.get_text_usage_summary(str(row["id"]))
        ).replace("\n", "\n\n")
        self.preview.setMarkdown(
            f"# {row['title']}\n\n"
            f"{row['subject']} · {lifecycle}{issue}\n\n"
            f"---\n\n## 课堂脉络\n\n{topics_section}\n\n"
            f"---\n\n## 整理笔记\n\n{notes_section}\n\n"
            f"---\n\n## 文本 API 用量\n\n{usage_section}\n\n"
            f"{marked_section}"
            f"---\n\n## 英中对照逐字稿\n\n{transcript_section}"
        )


class SettingsPage(Page):
    PROVIDERS = {"OpenAI": "openai", "DeepSeek": "deepseek", "自定义兼容服务": "compatible"}
    MODELS = {
        "openai": ["gpt-5-mini"],
        "deepseek": ["deepseek-v4-flash", "deepseek-v4-pro"],
        "compatible": ["qwen3:8b"],
    }

    def __init__(self, settings: Settings) -> None:
        super().__init__("设置", "转写、翻译与本地数据")
        self.settings = settings
        self.runtime_check = RuntimeCheckBridge(self)
        self.runtime_check.finished.connect(self.local_runtime_finished)
        self.notice = NoticeBar()
        self.layout.addWidget(self.notice)

        speech = QFrame()
        speech.setObjectName("Card")
        speech_layout = QVBoxLayout(speech)
        speech_layout.setContentsMargins(22, 19, 22, 20)
        speech_layout.setSpacing(11)
        speech_title = QLabel("语音转写")
        speech_title.setObjectName("SectionTitle")
        speech_hint = QLabel(
            "推荐使用 Apple 芯片本地识别：音频不上传，也不需要语音 API Key。"
            if IS_MACOS
            else "推荐使用 RTX 4060 本地识别：延迟低、英文不上传，也不需要语音 API Key。"
        )
        speech_hint.setObjectName("Muted")
        speech_fields = QHBoxLayout()
        self.speech_provider = QComboBox()
        self.speech_provider.addItems([local_speech_option(), "OpenAI 云端实时语音"])
        self.speech_provider.setCurrentIndex(0 if settings.live_mode == "local" else 1)
        self.speech_model = QComboBox()
        self.speech_model.setEditable(True)
        self.speech_model.addItems(
            [settings.mac_transcription_model, "mlx-community/whisper-large-v3-turbo-8bit"]
            if settings.live_mode == "local" and IS_MACOS
            else [settings.local_transcription_model, "distil-large-v3", "large-v3-turbo", "small.en"]
            if settings.live_mode == "local"
            else [settings.live_transcription_model, "gpt-live-transcribe"]
        )
        self.speech_model.setCurrentText(
            (settings.mac_transcription_model if IS_MACOS else settings.local_transcription_model)
            if settings.live_mode == "local"
            else settings.live_transcription_model
        )
        self.speech_key = self._key_input(settings.api_key, "粘贴 OpenAI API Key（sk-...）")
        self.speech_key.setVisible(settings.live_mode != "local")
        speech_fields.addWidget(self.speech_provider, 1)
        speech_fields.addWidget(self.speech_model, 1)
        speech_fields.addWidget(self.speech_key, 2)
        speech_layout.addWidget(speech_title)
        speech_layout.addWidget(speech_hint)
        speech_layout.addLayout(speech_fields)
        speech_actions = QHBoxLayout()
        self.speech_status = QLabel("本地模型首次使用会自动下载，之后可离线完成英文识别。")
        self.speech_status.setObjectName("Muted")
        self.test_local_button = QPushButton(
            "下载并预热模型" if IS_MACOS else "检测本地识别"
        )
        self.test_local_button.setToolTip(
            "提前下载并加载模型，避免第一次上课时等待"
            if IS_MACOS
            else "检测 CUDA、FP16 和本地识别依赖"
        )
        self.test_local_button.clicked.connect(self.test_local_runtime)
        speech_actions.addWidget(self.speech_status, 1)
        speech_actions.addWidget(self.test_local_button)
        speech_layout.addLayout(speech_actions)
        self.temporary_audio_check = QCheckBox("临时保留本机课堂音频（默认关闭）")
        self.temporary_audio_check.setChecked(settings.temporary_audio)
        self.temporary_audio_check.setToolTip(
            "课堂进行时写入本机 WAV；正常完成并导出后自动删除，失败或中断时保留供恢复。"
        )
        audio_retention_hint = QLabel(
            "仅保存在课程数据库旁的 temporary-audio 文件夹；失败或中断时不会自动删除，请注意隐私和磁盘空间。"
        )
        audio_retention_hint.setObjectName("Muted")
        audio_retention_hint.setWordWrap(True)
        speech_layout.addWidget(self.temporary_audio_check)
        speech_layout.addWidget(audio_retention_hint)
        self.layout.addWidget(speech)

        text_card = QFrame()
        text_card.setObjectName("Card")
        text_layout = QVBoxLayout(text_card)
        text_layout.setContentsMargins(22, 19, 22, 20)
        text_layout.setSpacing(11)
        text_title = QLabel("翻译与笔记")
        text_title.setObjectName("SectionTitle")
        text_hint = QLabel("OpenAI 可以复用语音密钥；DeepSeek 使用独立密钥。")
        text_hint.setObjectName("Muted")
        text_fields = QHBoxLayout()
        self.provider = QComboBox()
        self.provider.addItems(self.PROVIDERS.keys())
        current_label = next((label for label, value in self.PROVIDERS.items() if value == settings.text_provider), "OpenAI")
        self.provider.setCurrentText(current_label)
        self.text_model = QComboBox()
        self.text_model.setEditable(True)
        self.text_key = self._key_input(settings.text_api_key if settings.text_provider != "openai" else None, "粘贴文本服务 API Key（sk-...）")
        self.base_url = QLineEdit(settings.text_base_url or "")
        self.base_url.setPlaceholderText("兼容服务地址，例如 http://localhost:11434/v1")
        text_fields.addWidget(self.provider, 1)
        text_fields.addWidget(self.text_model, 1)
        text_fields.addWidget(self.text_key, 2)
        text_layout.addWidget(text_title)
        text_layout.addWidget(text_hint)
        text_layout.addLayout(text_fields)
        text_layout.addWidget(self.base_url)
        budget_fields = QHBoxLayout()
        budget_label = QLabel("单节课文本预算（美元）")
        budget_label.setObjectName("Muted")
        self.budget_input = QLineEdit(
            str(settings.class_budget_usd) if settings.class_budget_usd else ""
        )
        self.budget_input.setPlaceholderText("留空或填 0：不限制；例如 0.10")
        self.budget_input.setToolTip(
            "达到 80% 暂停实时摘要，达到上限暂停新的文本请求；本地英文继续保存。"
        )
        budget_fields.addWidget(budget_label)
        budget_fields.addWidget(self.budget_input, 1)
        text_layout.addLayout(budget_fields)
        budget_hint = QLabel(
            "预算仅按已返回用量估算文本费用；并发请求可能超额。云端语音不包含在内，未知单价时不执行限额。"
        )
        budget_hint.setObjectName("Muted")
        budget_hint.setWordWrap(True)
        text_layout.addWidget(budget_hint)
        output_hint = QLabel("笔记输出位置")
        output_hint.setObjectName("Muted")
        output_fields = QHBoxLayout()
        self.output_dir = QLineEdit(str(settings.export_dir.resolve()))
        self.output_dir.setPlaceholderText("选择 Markdown 课堂笔记的保存文件夹")
        self.output_dir.setToolTip("实时课堂和文件转写完成后，Markdown 笔记会保存到这里")
        browse_output = QPushButton("浏览…")
        browse_output.clicked.connect(self.choose_output_directory)
        open_output = QPushButton("打开")
        open_output.clicked.connect(self.open_current_output_directory)
        output_fields.addWidget(self.output_dir, 1)
        output_fields.addWidget(browse_output)
        output_fields.addWidget(open_output)
        text_layout.addWidget(output_hint)
        text_layout.addLayout(output_fields)
        self.layout.addWidget(text_card)

        footer = QHBoxLayout()
        save = QPushButton("保存并应用")
        save.setObjectName("Primary")
        save.clicked.connect(self.save)
        show_keys = QPushButton("显示密钥")
        show_keys.setCheckable(True)
        show_keys.toggled.connect(self.toggle_keys)
        footer.addWidget(save)
        footer.addWidget(show_keys)
        footer.addStretch()
        self.layout.addLayout(footer)
        self.layout.addStretch()

        self.provider.currentTextChanged.connect(self.provider_changed)
        self.speech_provider.currentTextChanged.connect(self.speech_provider_changed)
        self.provider_changed(self.provider.currentText(), preserve_model=settings.text_model)

    @staticmethod
    def _key_input(value: str | None, placeholder: str) -> QLineEdit:
        field = QLineEdit(value or "")
        field.setPlaceholderText(placeholder)
        field.setEchoMode(QLineEdit.EchoMode.Password)
        return field

    def toggle_keys(self, visible: bool) -> None:
        mode = QLineEdit.EchoMode.Normal if visible else QLineEdit.EchoMode.Password
        self.speech_key.setEchoMode(mode)
        self.text_key.setEchoMode(mode)

    def speech_provider_changed(self, label: str) -> None:
        local = label.startswith("本地")
        current = self.speech_model.currentText()
        self.speech_model.clear()
        if local:
            models = (
                ["mlx-community/whisper-large-v3-turbo", "mlx-community/whisper-large-v3-turbo-8bit"]
                if IS_MACOS
                else ["distil-large-v3", "large-v3-turbo", "small.en"]
            )
            self.speech_model.addItems(models)
            if current in models:
                self.speech_model.setCurrentText(current)
        else:
            self.speech_model.addItem("gpt-live-transcribe")
            if current == "gpt-live-transcribe":
                self.speech_model.setCurrentText(current)
        self.speech_key.setVisible(not local)

    def provider_changed(self, label: str, preserve_model: str = "") -> None:
        provider = self.PROVIDERS[label]
        self.text_model.clear()
        self.text_model.addItems(self.MODELS[provider])
        if preserve_model:
            self.text_model.setCurrentText(preserve_model)
        self.text_key.setVisible(provider != "openai")
        self.base_url.setVisible(provider == "compatible")
        if provider == "deepseek":
            self.text_key.setPlaceholderText("粘贴 DeepSeek API Key（sk-...）")
        elif provider == "compatible":
            self.text_key.setPlaceholderText("粘贴兼容服务 API Key；本地服务可填写 local")

    def test_local_runtime(self) -> None:
        if IS_MACOS:
            if not IS_APPLE_SILICON:
                self.speech_status.setText("当前是 Intel Mac，无法使用 MLX 本地识别")
                self.notice.show_notice("Intel Mac 请改用 OpenAI 云端实时语音；本地模式面向 M1 或更新机型。", error=True)
                return
            model = self.speech_model.currentText().strip()
            if not model:
                self.notice.show_notice("请先选择一个 Mac 本地识别模型。", error=True)
                return
            self.test_local_button.setEnabled(False)
            self.speech_status.setText("正在下载或加载模型；首次可能需要几分钟，请保持网络连接……")
            self.notice.show_notice("模型准备期间可以继续查看设置，但请不要退出软件。")
            threading.Thread(
                target=self._prepare_mac_runtime,
                args=(model,),
                name="classnote-mac-preflight",
                daemon=True,
            ).start()
            return
        try:
            from .local_live import _prepare_nvidia_dlls

            _prepare_nvidia_dlls()
            import ctranslate2

            devices = ctranslate2.get_cuda_device_count()
            compute = ctranslate2.get_supported_compute_types("cuda") if devices else set()
            if devices and "float16" in compute:
                model = self.speech_model.currentText().strip() or "distil-large-v3"
                self.speech_status.setText(f"检测通过：1 块 CUDA 显卡 · FP16 · {model}")
                self.notice.show_notice("本地 GPU 识别环境正常，可以用于实时课堂。")
            else:
                self.speech_status.setText("未检测到可用的 CUDA FP16 环境")
                self.notice.show_notice("本地 GPU 环境不可用，可暂时选择 OpenAI 云端实时语音。", error=True)
        except Exception as exc:
            self.speech_status.setText("本地识别组件检测失败")
            self.notice.show_notice(friendly_error(exc), error=True)

    def _prepare_mac_runtime(self, model: str) -> None:
        try:
            import mlx.core  # noqa: F401

            from .mac_live import preload_mlx_model

            reused = preload_mlx_model(model)
            devices = list_input_devices()
            microphones = sum(not device.is_loopback for device in devices)
            if not microphones:
                raise RuntimeError(
                    "没有检测到麦克风。请检查连接，并在系统设置中允许麦克风访问。"
                )
            detail = f"Apple 芯片 · MLX · {microphones} 个麦克风 · {model}"
            message = (
                "模型已经预热，可以直接开始课堂。"
                if reused
                else "模型下载和预热完成，可以直接开始课堂。"
            )
            self.runtime_check.finished.emit(True, detail, message)
        except Exception as exc:
            self.runtime_check.finished.emit(False, "Mac 本地识别准备失败", friendly_error(exc))

    def local_runtime_finished(self, success: bool, detail: str, message: str) -> None:
        self.test_local_button.setEnabled(True)
        self.speech_status.setText(detail)
        self.notice.show_notice(message, error=not success)

    def choose_output_directory(self) -> None:
        from PySide6.QtWidgets import QFileDialog

        current = self.output_dir.text().strip() or str(self.settings.export_dir.resolve())
        selected = QFileDialog.getExistingDirectory(
            self,
            "选择课堂笔记输出文件夹",
            current,
            QFileDialog.Option.ShowDirsOnly,
        )
        if selected:
            self.output_dir.setText(str(Path(selected).resolve()))

    def resolved_output_directory(self, create: bool = False) -> Path:
        raw = self.output_dir.text().strip()
        if not raw:
            raise ValueError("请选择课堂笔记的输出位置。")
        path = Path(raw).expanduser().resolve()
        if path.exists() and not path.is_dir():
            raise ValueError("笔记输出位置必须是文件夹，不能是文件。")
        if create:
            path.mkdir(parents=True, exist_ok=True)
        return path

    def open_current_output_directory(self) -> None:
        try:
            self.open_path(self.resolved_output_directory(create=True), "笔记目录")
        except (OSError, ValueError) as exc:
            self.notice.show_notice(friendly_error(exc), error=True)

    def save(self) -> None:
        speech_key = self.speech_key.text().strip()
        local_speech = self.speech_provider.currentText().startswith("本地")
        provider = self.PROVIDERS[self.provider.currentText()]
        text_key = self.text_key.text().strip()
        if not local_speech and not speech_key:
            self.notice.show_notice(f"云端语音转写需要 OpenAI API Key；也可以选择{local_speech_name()}识别。", error=True)
            self.speech_key.setFocus()
            return
        if provider != "openai" and not text_key:
            self.notice.show_notice("请填写所选文本服务的 API Key。", error=True)
            self.text_key.setFocus()
            return
        model = self.text_model.currentText().strip()
        if not model:
            self.notice.show_notice("请选择或填写一个翻译模型。", error=True)
            return
        base_url = self.base_url.text().strip()
        if provider == "compatible" and not base_url:
            self.notice.show_notice("自定义兼容服务需要填写 API 地址。", error=True)
            self.base_url.setFocus()
            return
        try:
            budget = Decimal(self.budget_input.text().strip() or "0")
        except InvalidOperation:
            budget = Decimal("-1")
        if not budget.is_finite() or budget < 0:
            self.notice.show_notice("课堂文本预算请填写非负美元金额；留空或填 0 表示关闭。", error=True)
            self.budget_input.setFocus()
            return
        try:
            output_dir = self.resolved_output_directory(create=True)
        except (OSError, ValueError) as exc:
            self.notice.show_notice(friendly_error(exc), error=True)
            self.output_dir.setFocus()
            return
        values = {
            "OPENAI_API_KEY": speech_key,
            "LIVE_MODE": "local" if local_speech else "realtime",
            "LIVE_TRANSCRIPTION_MODEL": (
                "gpt-live-transcribe" if local_speech else self.speech_model.currentText().strip()
            ),
            "LOCAL_TRANSCRIPTION_MODEL": (
                self.speech_model.currentText().strip() if local_speech and not IS_MACOS else self.settings.local_transcription_model
            ),
            "MAC_TRANSCRIPTION_MODEL": (
                self.speech_model.currentText().strip() if local_speech and IS_MACOS else self.settings.mac_transcription_model
            ),
            "LOCAL_COMPUTE_TYPE": "float16",
            "LOCAL_REFRESH_MS": "800",
            "TEXT_PROVIDER": provider,
            "TEXT_MODEL": model,
            "TEXT_API_KEY": text_key if provider == "compatible" else "",
            "DEEPSEEK_API_KEY": text_key if provider == "deepseek" else "",
            "TEXT_BASE_URL": base_url if provider == "compatible" else ("https://api.deepseek.com" if provider == "deepseek" else ""),
            "CLASSNOTE_EXPORT_DIR": str(output_dir),
            "CLASSNOTE_TEMP_AUDIO": "true" if self.temporary_audio_check.isChecked() else "false",
            "CLASSNOTE_CLASS_BUDGET_USD": str(budget),
        }
        try:
            save_env_settings(values)
        except OSError as exc:
            self.notice.show_notice(f"保存配置失败：{friendly_error(exc)}", error=True)
            return
        self.settings = Settings.load()
        self.output_dir.setText(str(self.settings.export_dir.resolve()))
        self.notice.show_notice(
            f"配置已保存并立即生效。之后的课堂笔记将输出到：{self.settings.export_dir.resolve()}"
        )

    def open_path(self, path: Path, label: str) -> None:
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(path))):
            show_message(self, QMessageBox.Icon.Critical, f"无法打开{label}", "系统没有找到可用于打开该位置的应用。")


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(APP_TITLE)
        self.resize(1180, 780)
        self.setMinimumSize(920, 640)
        self.immersive_active = False
        self._close_after_session = False
        self._was_maximized = False
        self._normal_geometry = self.saveGeometry()
        self.settings = Settings.load()
        self.settings.ensure_directories()
        self.repository = CourseRepository(self.settings.database_path)
        self.bridge = Bridge()
        self.bridge.event.connect(self.handle_event)
        self.escape_shortcut = QShortcut(QKeySequence(Qt.Key.Key_Escape), self)
        self.escape_shortcut.setContext(Qt.ShortcutContext.ApplicationShortcut)
        self.escape_shortcut.activated.connect(self.exit_fullscreen_if_needed)

        root = QWidget()
        root.setObjectName("Root")
        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)
        self.setCentralWidget(root)

        self.sidebar = QFrame()
        self.sidebar.setObjectName("Topbar")
        self.sidebar.setFixedHeight(50)
        side = QHBoxLayout(self.sidebar)
        side.setContentsMargins(18, 0, 14, 0)
        side.setSpacing(4)
        mark = QLabel(APP_NAME)
        mark.setObjectName("TopBrand")
        mark.setMinimumWidth(112)
        side.addWidget(mark)

        self.stack = QStackedWidget()
        self.nav_buttons: list[QPushButton] = []
        nav = [("首页", "打开课堂准备中心", 0), ("实时", "开始实时课堂", 1), ("文件", "导入录音或视频", 2), ("课程", "浏览课程库", 3)]
        for text, tip, index in nav:
            button = QPushButton(text)
            button.setObjectName("Nav")
            button.setCheckable(True)
            button.setFixedSize(68, 49)
            button.setToolTip(tip)
            button.clicked.connect(lambda checked=False, i=index: self.navigate(i))
            self.nav_buttons.append(button)
            side.addWidget(button)
        side.addStretch()
        settings_button = QPushButton("设置")
        settings_button.setObjectName("Nav")
        settings_button.setCheckable(True)
        settings_button.setFixedSize(68, 49)
        settings_button.setToolTip("服务与数据设置")
        settings_button.clicked.connect(lambda: self.navigate(4))
        self.nav_buttons.append(settings_button)
        side.addWidget(settings_button)

        self.home_page = HomePage(
            self.navigate,
            self.repository,
            self.settings,
            self.open_course,
            self.open_recovery_center,
        )
        self.live_page = LivePage(
            self.bridge,
            self.navigate,
            self.set_immersive,
            self.toggle_fullscreen,
            self.repository,
        )
        self.file_page = FilePage(self.bridge, self.repository)
        self.library_page = LibraryPage(self.repository)
        self.settings_page = SettingsPage(self.settings)
        for page in [self.home_page, self.live_page, self.file_page, self.library_page, self.settings_page]:
            self.stack.addWidget(page)
        root_layout.addWidget(self.sidebar)
        root_layout.addWidget(self.stack, 1)
        self.navigate(0)

    def navigate(self, index: int) -> None:
        self.stack.setCurrentIndex(index)
        for current, button in enumerate(self.nav_buttons):
            button.setChecked(current == index)
        if index == 0:
            self.home_page.reload()
        elif index == 3:
            self.library_page.reload()

    def open_course(self, course_id: str) -> None:
        self.navigate(3)
        self.library_page.select_course(course_id)

    def resume_course(self, course_id: str) -> None:
        self.navigate(3)
        if self.library_page.select_course(course_id) and self.library_page.recover_button.isEnabled():
            self.library_page.recover_selected()

    def open_recovery_audio(self, path: Path, title: str, subject: str) -> None:
        self.navigate(2)
        self.file_page.select_file(str(path))
        self.file_page.title_input.setText(title if title != "未关联的课堂录音" else "恢复的课堂录音")
        self.file_page.subject_input.setText(subject)
        self.file_page.status.setText("已载入保留录音。确认课程信息后点击“开始处理”，将创建一条新记录。")

    def open_recovery_center(self) -> None:
        RecoveryCenterDialog(
            self.repository, self.resume_course, self.open_recovery_audio, self
        ).exec()

    def set_immersive(self, active: bool) -> None:
        """Use a distraction-free workspace while keeping recording controls accessible."""
        if active and not self.immersive_active:
            self._was_maximized = self.isMaximized()
            self._normal_geometry = self.saveGeometry()
        self.immersive_active = active
        self.sidebar.setVisible(not active)
        if active:
            self.live_page.title_label.hide()
            self.live_page.subtitle_label.hide()
            self.live_page.layout.setContentsMargins(8, 6, 8, 8)
            self.live_page.layout.setSpacing(6)
            self.live_page.control_layout.setContentsMargins(12, 7, 12, 7)
            self.showFullScreen()
            self.live_page.set_fullscreen_state(True)
        else:
            self._leave_fullscreen()
            self.live_page.title_label.show()
            self.live_page.subtitle_label.show()
            self.live_page.layout.setContentsMargins(24, 20, 24, 20)
            self.live_page.layout.setSpacing(12)
            self.live_page.control_layout.setContentsMargins(16, 10, 16, 10)
            self.live_page.set_fullscreen_state(False)

    def toggle_fullscreen(self) -> None:
        if not self.immersive_active:
            return
        if self.isFullScreen():
            self._leave_fullscreen()
            self.live_page.set_fullscreen_state(False)
            self.live_page.status.setText("已退出全屏，课堂仍在继续。点击“进入全屏”可恢复。")
            self.live_page.live_status.setText("课堂仍在继续 · 点击“进入全屏”可恢复专注模式")
        else:
            self.showFullScreen()
            self.live_page.set_fullscreen_state(True)

    def exit_fullscreen_if_needed(self) -> None:
        if self.immersive_active and self.isFullScreen():
            self.toggle_fullscreen()

    def _leave_fullscreen(self) -> None:
        if self.isFullScreen():
            if self._was_maximized:
                self.showMaximized()
            else:
                self.showNormal()
                self.restoreGeometry(self._normal_geometry)

    def keyPressEvent(self, event: QKeyEvent) -> None:  # type: ignore[override]
        if event.key() == Qt.Key.Key_Escape and self.immersive_active and self.isFullScreen():
            self.toggle_fullscreen()
            event.accept()
            return
        super().keyPressEvent(event)

    def handle_event(self, name: str, payload: object) -> None:
        if name.startswith("file_"):
            self.file_page.handle_event(name, payload)
        else:
            self.live_page.handle_event(name, payload)
            if self._close_after_session and name in {"finished", "error"}:
                QTimer.singleShot(0, self.close)

    def closeEvent(self, event) -> None:  # type: ignore[override]
        if self.live_page.session is not None:
            if self._close_after_session:
                event.ignore()
                return
            if not confirm_message(
                self,
                "课堂仍在进行",
                "软件会先停止录音、保存最后字幕并完成收尾，然后自动退出。确定结束课堂吗？",
            ):
                event.ignore()
                return
            self._close_after_session = True
            self.live_page.stop(confirmed=True)
            event.ignore()
            return
        event.accept()


def main() -> None:
    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationDisplayName(APP_NAME)
    resource_root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[2]))
    icon_path = resource_root / "assets" / "brand" / "tingyiji-icon-256.png"
    if icon_path.exists():
        app.setWindowIcon(QIcon(str(icon_path)))
    app.setStyle("Fusion")
    app.setStyleSheet(STYLE)
    font = QFont(UI_FONT_FAMILY, 10)
    app.setFont(font)
    settings = Settings.load()
    settings.ensure_directories()
    lock = QLockFile(str((settings.database_path.parent / "classnote.lock").resolve()))
    if not lock.tryLock(100):
        show_message(
            None,  # type: ignore[arg-type]
            QMessageBox.Icon.Information,
            f"{APP_NAME}已在运行",
            f"已经有一个{APP_NAME}窗口正在运行，请回到现有窗口继续使用。",
        )
        return
    # Keep the lock alive for the full QApplication lifetime.
    app._classnote_lock = lock  # type: ignore[attr-defined]
    CourseRepository(settings.database_path).mark_active_courses_interrupted()
    window = MainWindow()
    window.show()
    raise SystemExit(app.exec())


if __name__ == "__main__":
    main()
