"""Modern local desktop application for novel2anime."""

from __future__ import annotations

import os
import re
import sys
import time
import traceback
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from PySide6.QtCore import QSettings, QSize, Qt, QThread, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QFont, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from app.core.config import settings
from app.domain.audio import DubbingLineSpec
from app.domain.video import EpisodeClipSpec, VideoRenderSpec
from app.services.asset_package_service import (
    AssetPackageService,
    OrganizeResult,
)
from app.services.audio_service import (
    DubbingComposeResult,
    DubbingRuntimeStatus,
    DubbingService,
)
from app.services.character_presets import (
    CHARACTER_LAYOUT_PRESETS,
    DEFAULT_CHARACTER_LAYOUT_ID,
    character_layout_label,
)
from app.services.cosyvoice_service import (
    CosyVoiceRemoteService,
    CosyVoiceStatus,
)
from app.services.desktop_service import (
    ChapterSnapshot,
    CharacterSnapshot,
    DesktopProjectService,
    EpisodeSnapshot,
    ImageSnapshot,
    ProjectSnapshot,
)
from app.services.gpu_service import (
    GenerationResult,
    GpuConnection,
    GpuServerService,
    GpuStatus,
    default_gpu_connection,
)
from app.services.image_models import (
    DEFAULT_IMAGE_MODEL_ID,
    IMAGE_MODEL_PRESETS,
)
from app.services.latentsync_service import (
    LatentSyncRemoteService,
    LatentSyncResult,
    LatentSyncStatus,
)
from app.services.lip_sync_batch_service import (
    LipSyncBatchPlanner,
    LipSyncBatchRunResult,
)
from app.services.local_comfy_service import LocalComfyGenerationService
from app.services.local_llm_service import LocalLlmService, LocalLlmStatus
from app.services.model_runtime_service import (
    LocalModelRuntimeService,
    LocalRuntimeInventory,
)
from app.services.prompt_styles import (
    DEFAULT_STYLE,
    STYLE_PRESETS,
    apply_style,
    style_prompt,
)
from app.services.video_service import (
    EpisodeComposeResult,
    VideoBatchResult,
    VideoClipResult,
    VideoRenderService,
    VideoRuntimeStatus,
)
from app.services.voice_library_service import VoiceLibraryService
from app.ui.asset_library_page import AssetLibraryPage
from app.ui.video_page import VideoGenerationPage
from app.ui.voice_library_page import VoiceLibraryPage

AUTO_VOICE_REFERENCE_TEXT = (
    "我会记住今日的风声，也会坚定地走完属于自己的路。"
)

APP_STYLE = """
* {
    font-family: "Microsoft YaHei UI", "Segoe UI";
    font-size: 13px;
    color: #172033;
}
QMainWindow, QWidget#appRoot {
    background: #F3F6FA;
}
QFrame#sidebar {
    background: #111827;
    border: none;
}
QLabel#brandMark {
    color: #FFFFFF;
    font-size: 20px;
    font-weight: 700;
}
QLabel#brandSub {
    color: #8FA0B8;
    font-size: 11px;
}
QPushButton#navButton {
    background: transparent;
    color: #9EABC0;
    border: none;
    border-radius: 10px;
    padding: 11px 14px;
    text-align: left;
    font-size: 14px;
}
QPushButton#navButton:hover {
    background: #1B2638;
    color: #FFFFFF;
}
QPushButton#navButton:checked {
    background: #243247;
    color: #FFFFFF;
    font-weight: 600;
    border-left: 3px solid #35C7B1;
}
QLabel#sectionCaption {
    color: #667085;
    font-size: 12px;
    font-weight: 600;
}
QLabel#pageTitle {
    color: #101828;
    font-size: 26px;
    font-weight: 700;
}
QLabel#pageSubtitle {
    color: #667085;
    font-size: 13px;
}
QFrame#card {
    background: #FFFFFF;
    border: 1px solid #E7ECF2;
    border-radius: 14px;
}
QPushButton#overviewButton {
    background: #FFFFFF;
    border: 1px solid #E7ECF2;
    border-radius: 14px;
    padding: 0px;
    text-align: left;
}
QPushButton#overviewButton:hover {
    background: #FBFEFD;
    border: 1px solid #6DC9BC;
}
QPushButton#modelSelector {
    background: #FFFFFF;
    color: #344054;
    border: 1px solid #D7DEE8;
    border-radius: 9px;
    padding: 8px 12px;
    font-weight: 600;
}
QPushButton#modelSelector:hover {
    border-color: #58BDAE;
}
QPushButton#modelSelector:checked {
    background: #E7F7F3;
    color: #12685F;
    border: 2px solid #2EA898;
}
QPushButton#modelSelector:disabled {
    background: #F2F4F7;
    color: #98A2B3;
    border-color: #EAECF0;
}
QLabel#metricValue {
    color: #101828;
    font-size: 28px;
    font-weight: 700;
}
QLabel#metricLabel {
    color: #667085;
    font-size: 12px;
}
QLabel#cardTitle {
    color: #182230;
    font-size: 15px;
    font-weight: 650;
}
QLabel#muted {
    color: #78869A;
    font-size: 12px;
}
QLabel#pillGood {
    background: #E9F9F4;
    color: #087A66;
    border-radius: 10px;
    padding: 4px 9px;
    font-weight: 600;
}
QLabel#pillWarn {
    background: #FFF5E5;
    color: #A25A00;
    border-radius: 10px;
    padding: 4px 9px;
    font-weight: 600;
}
QLabel#pillOff {
    background: #F2F4F7;
    color: #667085;
    border-radius: 10px;
    padding: 4px 9px;
    font-weight: 600;
}
QPushButton#primaryButton {
    background: #177D73;
    color: #FFFFFF;
    border: none;
    border-radius: 9px;
    padding: 9px 16px;
    font-weight: 600;
}
QPushButton#primaryButton:hover {
    background: #12685F;
}
QPushButton#primaryButton:disabled {
    background: #A9C8C3;
}
QPushButton#secondaryButton {
    background: #FFFFFF;
    color: #344054;
    border: 1px solid #D7DEE8;
    border-radius: 9px;
    padding: 8px 14px;
    font-weight: 600;
}
QPushButton#secondaryButton:hover {
    background: #F7F9FC;
    border-color: #B8C3D1;
}
QPushButton#dangerButton {
    background: #FFF5F5;
    color: #B42318;
    border: 1px solid #FECDCA;
    border-radius: 9px;
    padding: 8px 14px;
    font-weight: 600;
}
QPushButton#dangerButton:hover {
    background: #FEE4E2;
    border-color: #FDA29B;
}
QLineEdit, QComboBox, QSpinBox {
    background: #FFFFFF;
    border: 1px solid #D7DEE8;
    border-radius: 8px;
    padding: 7px 9px;
    min-height: 20px;
}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus {
    border: 1px solid #2EA898;
}
QTextEdit {
    background: #FFFFFF;
    border: 1px solid #E1E7EF;
    border-radius: 10px;
    padding: 8px;
    selection-background-color: #BEEBE4;
}
QListWidget {
    background: #FFFFFF;
    border: 1px solid #E1E7EF;
    border-radius: 12px;
    padding: 5px;
    outline: none;
}
QListWidget::item {
    border-radius: 8px;
    padding: 10px;
    margin: 2px;
}
QListWidget::item:selected {
    background: #E9F7F4;
    color: #12685F;
    font-weight: 600;
}
QTableWidget {
    background: #FFFFFF;
    alternate-background-color: #F8FAFC;
    border: 1px solid #E1E7EF;
    border-radius: 12px;
    gridline-color: transparent;
    selection-background-color: #DFF4EF;
    selection-color: #172033;
}
QHeaderView::section {
    background: #F7F9FC;
    color: #667085;
    border: none;
    border-bottom: 1px solid #E1E7EF;
    padding: 9px;
    font-weight: 600;
}
QScrollArea {
    border: none;
    background: transparent;
}
QScrollBar:vertical {
    background: transparent;
    width: 9px;
}
QScrollBar::handle:vertical {
    background: #CBD5E1;
    min-height: 28px;
    border-radius: 4px;
}
QProgressBar {
    background: #E8EDF3;
    border: none;
    border-radius: 7px;
    min-height: 14px;
    text-align: center;
    color: #344054;
    font-size: 11px;
}
QProgressBar::chunk {
    background: #20A590;
    border-radius: 7px;
}
QFrame#imageCard {
    background: #FFFFFF;
    border: 1px solid #E1E7EF;
    border-radius: 12px;
}
QFrame#imageCard[selected="true"] {
    background: #F7FFFC;
    border: 2px solid #20A590;
}
QLabel#imageSurface {
    background: #E9EEF5;
    border-radius: 9px;
}
QFrame#nextStepCard {
    background: #EAF8F5;
    border: 1px solid #B9E5DD;
    border-radius: 14px;
}
QLabel#stepNumber {
    background: #E8EEF5;
    color: #667085;
    border-radius: 13px;
    font-weight: 700;
}
QLabel#stepNumberActive {
    background: #177D73;
    color: #FFFFFF;
    border-radius: 13px;
    font-weight: 700;
}
"""


class BackgroundTask(QThread):
    succeeded = Signal(object)
    failed = Signal(str)

    def __init__(self, callback: Callable[[], Any], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.callback = callback

    def run(self) -> None:
        try:
            self.succeeded.emit(self.callback())
        except Exception as exc:
            detail = f"{exc}\n\n{traceback.format_exc(limit=5)}"
            self.failed.emit(detail)


class ProgressTask(QThread):
    succeeded = Signal(object)
    failed = Signal(str)
    progress = Signal(int, str)

    def __init__(
        self,
        callback: Callable[[Callable[[int, str], None]], Any],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.callback = callback

    def run(self) -> None:
        try:
            self.succeeded.emit(self.callback(self.progress.emit))
        except Exception as exc:
            detail = f"{exc}\n\n{traceback.format_exc(limit=5)}"
            self.failed.emit(detail)


class MetricCard(QPushButton):
    def __init__(self, label: str, value: str = "—", note: str = "") -> None:
        super().__init__("")
        self.setObjectName("overviewButton")
        self.setMinimumHeight(120)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 15)
        layout.setSpacing(5)
        caption = QLabel(label)
        caption.setObjectName("metricLabel")
        self.value_label = QLabel(value)
        self.value_label.setObjectName("metricValue")
        self.note_label = QLabel(note)
        self.note_label.setObjectName("muted")
        open_label = QLabel("打开 →")
        open_label.setStyleSheet("color:#177D73;font-size:12px;font-weight:600;")
        layout.addWidget(caption)
        layout.addWidget(self.value_label)
        footer = QHBoxLayout()
        footer.addWidget(self.note_label)
        footer.addStretch()
        footer.addWidget(open_label)
        layout.addLayout(footer)
        for child in self.findChildren(QLabel):
            child.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

    def set_value(self, value: str, note: str = "") -> None:
        self.value_label.setText(value)
        self.note_label.setText(note)


class PipelineRow(QPushButton):
    def __init__(self, index: int, title: str, subtitle: str) -> None:
        super().__init__("")
        self.setObjectName("overviewButton")
        self.setFixedHeight(72)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 11, 16, 11)
        number = QLabel(f"{index:02d}")
        number.setFixedSize(36, 36)
        number.setAlignment(Qt.AlignmentFlag.AlignCenter)
        number.setStyleSheet("background:#EFF3F8;color:#536176;border-radius:18px;font-weight:700;")
        text_layout = QVBoxLayout()
        text_layout.setSpacing(2)
        title_label = QLabel(title)
        title_label.setObjectName("cardTitle")
        subtitle_label = QLabel(subtitle)
        subtitle_label.setObjectName("muted")
        text_layout.addWidget(title_label)
        text_layout.addWidget(subtitle_label)
        self.status = QLabel("未开始")
        self.status.setObjectName("pillOff")
        arrow = QLabel("›")
        arrow.setStyleSheet("font-size:22px;color:#9AA7B8;")
        layout.addWidget(number)
        layout.addLayout(text_layout, 1)
        layout.addWidget(self.status)
        layout.addWidget(arrow)
        for child in self.findChildren(QLabel):
            child.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

    def set_status(self, text: str, state: str) -> None:
        self.status.setText(text)
        self.status.setObjectName({"good": "pillGood", "warn": "pillWarn"}.get(state, "pillOff"))
        self.status.style().unpolish(self.status)
        self.status.style().polish(self.status)


class PageHeader(QWidget):
    def __init__(self, title: str, subtitle: str) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 12)
        layout.setSpacing(3)
        title_label = QLabel(title)
        title_label.setObjectName("pageTitle")
        subtitle_label = QLabel(subtitle)
        subtitle_label.setObjectName("pageSubtitle")
        layout.addWidget(title_label)
        layout.addWidget(subtitle_label)


class OverviewPage(QWidget):
    check_server = Signal()
    continue_requested = Signal(int)

    def __init__(self) -> None:
        super().__init__()
        self.next_target = 2
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 26)
        layout.setSpacing(16)
        layout.addWidget(PageHeader("项目总览", "查看小说解析、视觉资产与 GPU 服务状态"))

        next_step = QFrame()
        next_step.setObjectName("nextStepCard")
        next_layout = QHBoxLayout(next_step)
        next_layout.setContentsMargins(20, 16, 18, 16)
        next_text = QVBoxLayout()
        next_text.setSpacing(3)
        self.next_title = QLabel("下一步：确定第一集角色定妆")
        self.next_title.setStyleSheet("font-size:17px;font-weight:700;color:#125C53;")
        self.next_description = QLabel("生成候选图并为每位主要角色选定一张定妆照")
        self.next_description.setStyleSheet("color:#39736B;")
        next_text.addWidget(self.next_title)
        next_text.addWidget(self.next_description)
        self.continue_button = QPushButton("继续选角")
        self.continue_button.setObjectName("primaryButton")
        self.continue_button.setMinimumWidth(120)
        self.continue_button.clicked.connect(lambda: self.continue_requested.emit(self.next_target))
        next_layout.addLayout(next_text, 1)
        next_layout.addWidget(self.continue_button)
        layout.addWidget(next_step)

        metrics = QHBoxLayout()
        metrics.setSpacing(12)
        self.chapter_card = MetricCard("标准章节")
        self.analysis_card = MetricCard("已完成分析")
        self.episode_card = MetricCard("分镜剧集")
        self.media_card = MetricCard("媒体产物")
        for card in (
            self.chapter_card,
            self.analysis_card,
            self.episode_card,
            self.media_card,
        ):
            metrics.addWidget(card)
        self.chapter_card.clicked.connect(lambda: self.continue_requested.emit(1))
        self.analysis_card.clicked.connect(lambda: self.continue_requested.emit(1))
        self.episode_card.clicked.connect(lambda: self.continue_requested.emit(3))
        self.media_card.clicked.connect(lambda: self.continue_requested.emit(4))
        layout.addLayout(metrics)

        body = QHBoxLayout()
        body.setSpacing(14)
        pipeline_card = QFrame()
        pipeline_card.setObjectName("card")
        pipeline_layout = QVBoxLayout(pipeline_card)
        pipeline_layout.setContentsMargins(18, 18, 18, 18)
        pipeline_layout.setSpacing(9)
        heading = QLabel("生产流程")
        heading.setObjectName("cardTitle")
        pipeline_layout.addWidget(heading)
        self.pipeline_rows = [
            PipelineRow(1, "小说入库", "导入、切章并建立标准章节"),
            PipelineRow(2, "内容分析", "提取人物、事件、对白和证据"),
            PipelineRow(3, "分镜导演", "生成 Episode 与镜头提示词"),
            PipelineRow(4, "角色定妆", "使用多模型生成并筛选角色"),
            PipelineRow(5, "声音选角", "匹配人物音色并允许手动调整"),
            PipelineRow(6, "视频与成片", "图生视频、配音、字幕与剪辑"),
            PipelineRow(7, "资源归档", "按类型整理并打开本地资源包"),
        ]
        for row in self.pipeline_rows:
            pipeline_layout.addWidget(row)
        for row, target in zip(
            self.pipeline_rows,
            (1, 1, 3, 2, 7, 4, 8),
            strict=True,
        ):
            row.clicked.connect(
                lambda _checked=False, page=target: self.continue_requested.emit(page)
            )
        pipeline_layout.addStretch()
        body.addWidget(pipeline_card, 3)

        server_card = QFrame()
        server_card.setObjectName("card")
        server_layout = QVBoxLayout(server_card)
        server_layout.setContentsMargins(20, 18, 20, 18)
        server_layout.setSpacing(11)
        server_heading = QHBoxLayout()
        title = QLabel("GPU 生成服务器")
        title.setObjectName("cardTitle")
        self.server_pill = QLabel("未检测")
        self.server_pill.setObjectName("pillOff")
        server_heading.addWidget(title)
        server_heading.addStretch()
        server_heading.addWidget(self.server_pill)
        server_layout.addLayout(server_heading)
        self.gpu_name = QLabel("RTX 3090")
        self.gpu_name.setStyleSheet("font-size:20px;font-weight:700;color:#101828;")
        self.gpu_detail = QLabel("点击检测服务器状态")
        self.gpu_detail.setObjectName("muted")
        self.model_name = QLabel("FLUX.1 Krea Dev FP8\nJuggernaut XI（SDXL）")
        self.model_name.setWordWrap(True)
        self.model_name.setStyleSheet(
            "background:#F3F7F9;border-radius:9px;padding:11px;font-weight:600;color:#23584F;"
        )
        server_actions = QHBoxLayout()
        settings_button = QPushButton("连接设置")
        settings_button.setObjectName("primaryButton")
        settings_button.clicked.connect(lambda: self.continue_requested.emit(6))
        check = QPushButton("检测服务器")
        check.setObjectName("secondaryButton")
        check.clicked.connect(self.check_server.emit)
        server_layout.addWidget(self.gpu_name)
        server_layout.addWidget(self.gpu_detail)
        server_layout.addSpacing(6)
        server_layout.addWidget(QLabel("可用生图模型"))
        server_layout.addWidget(self.model_name)
        server_layout.addStretch()
        server_actions.addWidget(settings_button)
        server_actions.addWidget(check)
        server_layout.addLayout(server_actions)
        body.addWidget(server_card, 2)
        layout.addLayout(body, 1)

    def set_project(self, project: ProjectSnapshot) -> None:
        if project.episode_count and (project.cast_selected_count < project.cast_character_count):
            self.next_target = 2
            self.next_title.setText("下一步：确定第一集角色定妆")
            self.next_description.setText(
                f"已选定 {project.cast_selected_count} / "
                f"{project.cast_character_count} 位角色；生成候选后点击“设为定妆照”"
            )
            self.continue_button.setText("继续选角")
        elif project.episode_count and project.cast_character_count:
            self.next_target = 3
            self.next_title.setText("角色定妆已完成")
            self.next_description.setText(
                f"{project.cast_selected_count} 位角色均已锁定，可以继续检查分镜"
            )
            self.continue_button.setText("查看分镜")
        elif project.chapter_count:
            self.next_target = 3
            self.next_title.setText("下一步：生成第一集分镜")
            self.next_description.setText("章节已入库，准备生成 Episode 和镜头提示词")
            self.continue_button.setText("查看分镜")
        else:
            self.next_target = 1
            self.next_title.setText("下一步：导入小说")
            self.next_description.setText("当前是空项目，请先通过任务流程导入小说文本")
            self.continue_button.setText("导入小说")
        self.chapter_card.set_value(
            f"{project.chapter_count:,}",
            "已导入数据库",
        )
        self.analysis_card.set_value(
            f"{project.analysis_count:,}",
            f"共 {project.chapter_count:,} 章",
        )
        self.episode_card.set_value(str(project.episode_count), "可编辑分镜")
        self.media_card.set_value(
            f"{project.image_count} / {project.video_count}",
            "图片 / 视频",
        )
        self.pipeline_rows[0].set_status(
            "已完成" if project.chapter_count else "未开始",
            "good" if project.chapter_count else "off",
        )
        analysis_done = (
            project.chapter_count > 0 and project.analysis_count >= project.chapter_count
        )
        self.pipeline_rows[1].set_status(
            "已完成" if analysis_done else f"{project.analysis_count}/{project.chapter_count}",
            "good" if analysis_done else ("warn" if project.analysis_count else "off"),
        )
        self.pipeline_rows[2].set_status(
            "已有分镜" if project.episode_count else "未开始",
            "good" if project.episode_count else "off",
        )
        self.pipeline_rows[3].set_status(
            (
                f"已锁定 {project.cast_selected_count}/{project.cast_character_count}"
                if project.cast_character_count
                else "未开始"
            ),
            (
                "good"
                if project.cast_character_count
                and project.cast_selected_count >= project.cast_character_count
                else ("warn" if project.image_count else "off")
            ),
        )
        self.pipeline_rows[4].set_status(
            "可选声" if project.episode_count else "未开始",
            "warn" if project.episode_count else "off",
        )
        self.pipeline_rows[5].set_status(
            (
                f"已有 {project.video_count} 个视频"
                if project.video_count
                else ("待生成" if project.episode_count else "未开始")
            ),
            (
                "good"
                if project.video_count
                else ("warn" if project.episode_count else "off")
            ),
        )
        self.pipeline_rows[6].set_status(
            "可整理" if project.episode_count else "未开始",
            "warn" if project.episode_count else "off",
        )

    def set_gpu_status(self, status: GpuStatus) -> None:
        if status.ssh_online and status.comfy_online and status.available_model_ids:
            text, object_name = "在线", "pillGood"
        elif status.ssh_online:
            text, object_name = "需处理", "pillWarn"
        else:
            text, object_name = "离线", "pillOff"
        self.server_pill.setText(text)
        self.server_pill.setObjectName(object_name)
        self.server_pill.style().unpolish(self.server_pill)
        self.server_pill.style().polish(self.server_pill)
        self.gpu_name.setText(status.gpu_name or "GPU 服务器")
        if status.ssh_online:
            self.gpu_detail.setText(
                f"显存 {status.memory_used_mb}/{status.memory_total_mb} MB  ·  "
                f"利用率 {status.utilization_percent}%  ·  磁盘剩余 {status.disk_available}"
            )
            labels = [
                IMAGE_MODEL_PRESETS[model_id].label
                for model_id in status.available_model_ids
                if model_id in IMAGE_MODEL_PRESETS
            ]
            self.model_name.setText("\n".join(labels) if labels else "未发现可用模型")
        else:
            self.gpu_detail.setText(status.message or "服务器未连接")


class ImagePreviewDialog(QDialog):
    def __init__(self, path: Path, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(path.name)
        self.resize(760, 820)
        layout = QVBoxLayout(self)
        image = QLabel()
        image.setAlignment(Qt.AlignmentFlag.AlignCenter)
        pixmap = QPixmap(str(path))
        image.setPixmap(
            pixmap.scaled(
                QSize(720, 760),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )
        layout.addWidget(image)


class ImageRevisionDialog(QDialog):
    """Collect a precise correction request for one generated image."""

    def __init__(
        self,
        path: Path,
        base_prompt: str,
        *,
        title: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.path = Path(path)
        self.image_width = 832
        self.image_height = 480
        self.setWindowTitle(f"修改图片 · {title}")
        self.resize(880, 720)
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 18)
        root.setSpacing(12)

        heading = QLabel("这张图哪里不好？告诉模型具体改什么")
        heading.setStyleSheet("font-size:20px;font-weight:700;color:#101828;")
        note = QLabel(
            "使用 FLUX.1 Kontext 基于当前图修改。原图不会被删除；生成完成后先看候选，再决定是否替换。"
        )
        note.setObjectName("muted")
        note.setWordWrap(True)
        root.addWidget(heading)
        root.addWidget(note)

        content = QHBoxLayout()
        preview = QLabel()
        preview.setObjectName("imageSurface")
        preview.setFixedSize(320, 320)
        preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        pixmap = QPixmap(str(self.path))
        if not pixmap.isNull():
            self.image_width = pixmap.width()
            self.image_height = pixmap.height()
            preview.setPixmap(
                pixmap.scaled(
                    preview.size(),
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
            )
        content.addWidget(preview)

        form = QVBoxLayout()
        form.addWidget(QLabel("常见问题"))
        self.issue_preset = QComboBox()
        presets = (
            ("自定义问题", ""),
            ("人物太老/不够好看", "人物显得太老或不够好看；改成年轻、自然、五官精致且符合角色年龄"),
            ("脸部或五官不正确", "修复脸部结构、双眼、鼻子、嘴和下巴，保持同一人物身份"),
            ("手指或肢体错误", "修复手指数量、手掌连接、四肢结构和自然受力"),
            ("服装不符合设定", "按照提示词修正服装款式、颜色、材质和配饰"),
            ("构图或裁切不好", "修正构图和取景，完整显示要求的身体范围，不遮挡主体"),
            ("背景不符合场景", "按照提示词修正背景地点、时代和环境元素"),
            ("风格或真实感不对", "修正为提示词指定的视觉风格，提高真实皮肤、光线和摄影质感"),
        )
        for label, value in presets:
            self.issue_preset.addItem(label, value)
        self.issue_preset.currentIndexChanged.connect(self._apply_issue_preset)
        form.addWidget(self.issue_preset)
        form.addWidget(QLabel("问题与修改要求"))
        self.issue = QTextEdit()
        self.issue.setMaximumHeight(92)
        self.issue.setPlaceholderText(
            "例如：脸太老，改成18岁左右的英俊少年；不要胡子；保持青色衣服和背景不变。"
        )
        form.addWidget(self.issue)
        content.addLayout(form, 1)
        root.addLayout(content)

        root.addWidget(QLabel("修改后的目标提示词"))
        self.prompt = QTextEdit()
        self.prompt.setMaximumHeight(120)
        self.prompt.setPlainText(base_prompt)
        self.prompt.setPlaceholderText("描述最终希望得到的画面")
        root.addWidget(self.prompt)
        root.addWidget(QLabel("明确不要出现的内容"))
        self.negative_prompt = QLineEdit()
        self.negative_prompt.setPlaceholderText(
            "例如：old, beard, moustache, deformed hands, extra fingers, text, watermark"
        )
        root.addWidget(self.negative_prompt)

        controls = QHBoxLayout()
        controls.addWidget(QLabel("保留原图程度"))
        self.preservation = QComboBox()
        self.preservation.addItem("严格保留 · 只修指定问题", "strict")
        self.preservation.addItem("平衡修改 · 推荐", "balanced")
        self.preservation.addItem("较大调整 · 允许改构图/姿势", "creative")
        self.preservation.setCurrentIndex(1)
        controls.addWidget(self.preservation)
        controls.addSpacing(12)
        controls.addWidget(QLabel("候选"))
        self.candidate_count = QSpinBox()
        self.candidate_count.setRange(1, 4)
        self.candidate_count.setValue(2)
        controls.addWidget(self.candidate_count)
        controls.addSpacing(12)
        controls.addWidget(QLabel("Seed"))
        self.seed = QSpinBox()
        self.seed.setRange(1, 2_000_000_000)
        self.seed.setValue(int(time.time()) % 2_000_000_000)
        controls.addWidget(self.seed)
        controls.addStretch()
        root.addLayout(controls)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("开始修改")
        buttons.button(QDialogButtonBox.StandardButton.Ok).setObjectName(
            "primaryButton"
        )
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.accepted.connect(self._accept_request)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def _apply_issue_preset(self, _index: int) -> None:
        value = str(self.issue_preset.currentData() or "")
        if value:
            self.issue.setPlainText(value)

    def _accept_request(self) -> None:
        if not self.issue.toPlainText().strip():
            QMessageBox.warning(self, "请说明问题", "请至少说明当前图片哪里不好、希望怎么改。")
            return
        self.accept()

    def payload(self) -> dict[str, Any]:
        return {
            "prompt": self.prompt.toPlainText().strip(),
            "issue": self.issue.toPlainText().strip(),
            "negative_prompt": self.negative_prompt.text().strip(),
            "preservation": str(self.preservation.currentData()),
            "candidate_count": self.candidate_count.value(),
            "seed": self.seed.value(),
            "width": self.image_width,
            "height": self.image_height,
        }


class ImageRevisionResultDialog(QDialog):
    """Let the user review revision candidates before replacing the current image."""

    def __init__(
        self,
        images: list[Path],
        *,
        window_title: str = "图片修改结果",
        heading_text: str = "修改完成 · 请选择要使用的版本",
        note_text: str = "点击“使用这张”才会替换当前选择；原图和其他候选都会保留。",
        labels: dict[Path, str] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.selected_path: Path | None = None
        self.setWindowTitle(window_title)
        self.resize(980, 660)
        root = QVBoxLayout(self)
        heading = QLabel(heading_text)
        heading.setStyleSheet("font-size:20px;font-weight:700;color:#101828;")
        note = QLabel(note_text)
        note.setObjectName("muted")
        root.addWidget(heading)
        root.addWidget(note)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        grid = QGridLayout(content)
        grid.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        for index, path in enumerate(images):
            card = QFrame()
            card.setObjectName("imageCard")
            card.setFixedWidth(285)
            card_layout = QVBoxLayout(card)
            image = QLabel()
            image.setObjectName("imageSurface")
            image.setFixedSize(265, 360)
            image.setAlignment(Qt.AlignmentFlag.AlignCenter)
            pixmap = QPixmap(str(path))
            if not pixmap.isNull():
                image.setPixmap(
                    pixmap.scaled(
                        image.size(),
                        Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation,
                    )
                )
            label = QLabel(
                (labels or {}).get(path, f"候选 {index + 1} · FLUX.1 Kontext")
            )
            label.setObjectName("muted")
            choose = QPushButton("使用这张")
            choose.setObjectName("primaryButton")
            choose.clicked.connect(lambda _checked=False, p=path: self._choose(p))
            preview = QPushButton("查看大图")
            preview.setObjectName("secondaryButton")
            preview.clicked.connect(
                lambda _checked=False, p=path: ImagePreviewDialog(p, self).exec()
            )
            card_layout.addWidget(label)
            card_layout.addWidget(image)
            card_layout.addWidget(preview)
            card_layout.addWidget(choose)
            grid.addWidget(card, index // 3, index % 3)
        scroll.setWidget(content)
        root.addWidget(scroll, 1)
        keep = QPushButton("只保存候选，暂不替换")
        keep.setObjectName("secondaryButton")
        keep.clicked.connect(self.reject)
        root.addWidget(keep, 0, Qt.AlignmentFlag.AlignRight)

    def _choose(self, path: Path) -> None:
        self.selected_path = path
        self.accept()


class ImageCard(QFrame):
    selection_requested = Signal(object)
    unlock_requested = Signal()
    revision_requested = Signal(object)

    def __init__(self, snapshot: ImageSnapshot, *, selected: bool = False) -> None:
        super().__init__()
        self.path = snapshot.path
        self.setObjectName("imageCard")
        self.setProperty("selected", selected)
        self.setFixedWidth(235)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 10)
        layout.setSpacing(7)
        image = QLabel()
        image.setObjectName("imageSurface")
        image.setFixedSize(217, 240)
        image.setAlignment(Qt.AlignmentFlag.AlignCenter)
        pixmap = QPixmap(str(self.path))
        if not pixmap.isNull():
            image.setPixmap(
                pixmap.scaled(
                    image.size(),
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
            )
        model = QLabel(snapshot.model_label)
        model.setStyleSheet(
            "background:#E9F7F4;color:#12685F;border-radius:7px;"
            "padding:5px 7px;font-size:11px;font-weight:700;"
        )
        model.setWordWrap(True)
        metadata = QLabel(
            f"{snapshot.generated_at}\n{snapshot.layout_label} · {self.path.stem}"
        )
        metadata.setObjectName("muted")
        metadata.setWordWrap(True)
        actions = QHBoxLayout()
        actions.setSpacing(7)
        preview = QPushButton("查看大图")
        preview.setObjectName("secondaryButton")
        preview.clicked.connect(self.open_preview)
        select = QPushButton("解除定妆" if selected else "设为定妆照")
        select.setObjectName("secondaryButton" if selected else "primaryButton")
        if selected:
            select.clicked.connect(self.unlock_requested.emit)
        else:
            select.clicked.connect(lambda: self.selection_requested.emit(self.path))
        layout.addWidget(model)
        layout.addWidget(metadata)
        layout.addWidget(image)
        actions.addWidget(preview)
        actions.addWidget(select)
        layout.addLayout(actions)
        revise = QPushButton("不满意 · 修改此图")
        revise.setObjectName("secondaryButton")
        revise.clicked.connect(lambda: self.revision_requested.emit(self.path))
        layout.addWidget(revise)

    def open_preview(self) -> None:
        ImagePreviewDialog(self.path, self).exec()


class CharactersPage(QWidget):
    generate_requested = Signal(str, str, str, object, str, int, int)
    save_prompt_requested = Signal(str, str, str, str)
    open_folder_requested = Signal(str)
    selection_requested = Signal(str, object)
    unlock_requested = Signal(str)
    revision_requested = Signal(str, object, object)
    check_server_requested = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.episode: EpisodeSnapshot | None = None
        self.characters: dict[str, CharacterSnapshot] = {}
        self.generation_started_at = 0.0
        self.elapsed_timer = QTimer(self)
        self.elapsed_timer.timeout.connect(self._refresh_elapsed)
        root = QVBoxLayout(self)
        root.setContentsMargins(28, 24, 28, 26)
        root.setSpacing(12)
        root.addWidget(PageHeader("角色定妆", "多模型生成角色候选，支持单人照与标准角色设定板"))

        steps = QFrame()
        steps.setObjectName("nextStepCard")
        steps_layout = QHBoxLayout(steps)
        steps_layout.setContentsMargins(17, 11, 17, 11)
        steps_layout.setSpacing(10)
        self.step_numbers: list[QLabel] = []
        for index, text in enumerate(
            ("选择左侧角色", "生成真人候选", "选定一张定妆照"),
            start=1,
        ):
            number = QLabel(str(index))
            number.setObjectName("stepNumberActive" if index == 1 else "stepNumber")
            number.setFixedSize(26, 26)
            number.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.step_numbers.append(number)
            label = QLabel(text)
            label.setStyleSheet("font-weight:600;color:#315A55;")
            steps_layout.addWidget(number)
            steps_layout.addWidget(label)
            if index < 3:
                separator = QLabel("→")
                separator.setStyleSheet("color:#84A69F;font-size:16px;")
                steps_layout.addWidget(separator)
        steps_layout.addStretch()
        root.addWidget(steps)

        content = QHBoxLayout()
        content.setSpacing(14)
        left = QFrame()
        left.setObjectName("card")
        left.setFixedWidth(235)
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(12, 15, 12, 15)
        title = QLabel("第一集角色")
        title.setObjectName("cardTitle")
        self.character_list = QListWidget()
        self.character_list.currentTextChanged.connect(self.show_character)
        left_layout.addWidget(title)
        left_layout.addWidget(self.character_list, 1)
        content.addWidget(left)

        right = QFrame()
        right.setObjectName("card")
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(18, 16, 18, 18)
        top = QHBoxLayout()
        self.character_title = QLabel("选择角色")
        self.character_title.setStyleSheet("font-size:21px;font-weight:700;color:#101828;")
        self.image_count = QLabel("0 张候选")
        self.image_count.setObjectName("pillOff")
        self.selection_status = QLabel("尚未选定")
        self.selection_status.setObjectName("pillWarn")
        top.addWidget(self.character_title)
        top.addStretch()
        top.addWidget(self.selection_status)
        top.addWidget(self.image_count)
        right_layout.addLayout(top)
        self.character_hint = QLabel("先从左侧选择角色")
        self.character_hint.setObjectName("muted")
        right_layout.addWidget(self.character_hint)
        prompt_label = QLabel("角色提示词")
        prompt_label.setObjectName("sectionCaption")
        right_layout.addWidget(prompt_label)
        self.profile = QTextEdit()
        self.profile.setMaximumHeight(96)
        self.profile.setPlaceholderText("输入年龄、外貌、服装、发型、气质等角色视觉描述")
        right_layout.addWidget(self.profile)

        style_row = QHBoxLayout()
        style_row.addWidget(QLabel("预设风格"))
        self.style = QComboBox()
        self.style.addItems(list(STYLE_PRESETS))
        self.style.setMinimumWidth(150)
        self.apply_style_button = QPushButton("应用到提示词")
        self.apply_style_button.setObjectName("secondaryButton")
        self.apply_style_button.clicked.connect(self._apply_style)
        self.save_prompt = QPushButton("保存提示词")
        self.save_prompt.setObjectName("primaryButton")
        self.save_prompt.clicked.connect(self._save_prompt)
        style_row.addWidget(self.style)
        style_row.addWidget(self.apply_style_button)
        style_row.addStretch()
        style_row.addWidget(self.save_prompt)
        right_layout.addLayout(style_row)

        preset_row = QHBoxLayout()
        preset_row.addWidget(QLabel("构图预设"))
        self.layout_preset = QComboBox()
        for preset_id, preset in CHARACTER_LAYOUT_PRESETS.items():
            self.layout_preset.addItem(preset.label, preset_id)
        self.layout_preset.setMinimumWidth(360)
        preset_row.addWidget(self.layout_preset, 1)
        preset_note = QLabel("预设1使用方形设定板；预设2使用横向三视图")
        preset_note.setObjectName("muted")
        preset_row.addWidget(preset_note)
        right_layout.addLayout(preset_row)

        model_row = QHBoxLayout()
        model_row.addWidget(QLabel("生成模型（可多选）"))
        self.model_buttons: dict[str, QPushButton] = {}
        for model_id, preset in IMAGE_MODEL_PRESETS.items():
            button = QPushButton(preset.label)
            button.setObjectName("modelSelector")
            button.setCheckable(True)
            button.setChecked(model_id == DEFAULT_IMAGE_MODEL_ID)
            button.setProperty("modelAvailable", True)
            button.setToolTip(f"模型文件：{preset.filename}")
            self.model_buttons[model_id] = button
            model_row.addWidget(button)
        model_row.addStretch()
        right_layout.addLayout(model_row)

        server_row = QHBoxLayout()
        self.gpu_status = QLabel("GPU 尚未检测")
        self.gpu_status.setObjectName("pillOff")
        self.check_server = QPushButton("检测连接")
        self.check_server.setObjectName("secondaryButton")
        self.check_server.clicked.connect(self.check_server_requested.emit)
        server_row.addWidget(self.gpu_status)
        server_row.addStretch()
        server_row.addWidget(self.check_server)
        right_layout.addLayout(server_row)

        controls = QHBoxLayout()
        controls.addWidget(QLabel("生成数量"))
        self.count = QSpinBox()
        self.count.setRange(1, 8)
        self.count.setValue(4)
        controls.addWidget(self.count)
        controls.addSpacing(8)
        controls.addWidget(QLabel("随机种子"))
        self.seed = QSpinBox()
        self.seed.setRange(1, 2_000_000_000)
        self.seed.setValue(20260727)
        controls.addWidget(self.seed)
        controls.addStretch()
        self.open_folder = QPushButton("打开文件夹")
        self.open_folder.setObjectName("secondaryButton")
        self.open_folder.clicked.connect(
            lambda: (
                self.open_folder_requested.emit(self.character_list.currentItem().text())
                if self.character_list.currentItem()
                else None
            )
        )
        self.generate = QPushButton("使用所选模型生成")
        self.generate.setObjectName("primaryButton")
        self.generate.clicked.connect(self._request_generation)
        controls.addWidget(self.open_folder)
        controls.addWidget(self.generate)
        right_layout.addLayout(controls)

        progress_row = QHBoxLayout()
        self.generation_progress = QProgressBar()
        self.generation_progress.setRange(0, 100)
        self.generation_progress.setValue(0)
        self.generation_progress.setTextVisible(True)
        self.generation_stage = QLabel("等待生成")
        self.generation_stage.setObjectName("muted")
        self.generation_elapsed = QLabel("00:00")
        self.generation_elapsed.setObjectName("pillOff")
        progress_row.addWidget(self.generation_progress, 1)
        progress_row.addWidget(self.generation_stage)
        progress_row.addWidget(self.generation_elapsed)
        right_layout.addLayout(progress_row)

        gallery_scroll = QScrollArea()
        gallery_scroll.setWidgetResizable(True)
        self.gallery = QWidget()
        self.gallery_grid = QGridLayout(self.gallery)
        self.gallery_grid.setContentsMargins(0, 10, 0, 0)
        self.gallery_grid.setSpacing(12)
        self.gallery_grid.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        gallery_scroll.setWidget(self.gallery)
        right_layout.addWidget(gallery_scroll, 1)
        content.addWidget(right, 1)
        root.addLayout(content, 1)

    def set_episode(self, episode: EpisodeSnapshot | None) -> None:
        self.episode = episode
        self.characters = {
            character.name: character for character in (episode.characters if episode else [])
        }
        current = self.character_list.currentItem()
        current_name = current.text() if current else ""
        self.character_list.blockSignals(True)
        self.character_list.clear()
        self.character_list.addItems(list(self.characters))
        matches = self.character_list.findItems(
            current_name,
            Qt.MatchFlag.MatchExactly,
        )
        if matches:
            self.character_list.setCurrentItem(matches[0])
        elif self.character_list.count():
            self.character_list.setCurrentRow(0)
        self.character_list.blockSignals(False)
        selected = self.character_list.currentItem()
        self.show_character(selected.text() if selected else "")

    def show_character(self, name: str) -> None:
        character = self.characters.get(name)
        self.character_title.setText(name or "选择角色")
        self.profile.setPlainText(character.profile if character else "")
        self.style.setCurrentText(character.style if character else DEFAULT_STYLE)
        preset_id = (
            character.generation_preset if character else DEFAULT_CHARACTER_LAYOUT_ID
        )
        preset_index = self.layout_preset.findData(preset_id)
        self.layout_preset.setCurrentIndex(max(0, preset_index))
        self.character_hint.setText(
            f"当前角色：{name} · 目标：年轻真人古装 · 无胡子" if character else "先从左侧选择角色"
        )
        self._clear_gallery()
        images = character.images if character else []
        selected = (
            character.selected_image.resolve() if character and character.selected_image else None
        )
        completed_steps = 3 if selected else (2 if images else 1)
        for index, number in enumerate(self.step_numbers, start=1):
            number.setObjectName("stepNumberActive" if index <= completed_steps else "stepNumber")
            number.style().unpolish(number)
            number.style().polish(number)
        self.selection_status.setText("✓ 已锁定定妆照" if selected else "尚未选定")
        self.selection_status.setObjectName("pillGood" if selected else "pillWarn")
        self.selection_status.style().unpolish(self.selection_status)
        self.selection_status.style().polish(self.selection_status)
        self.image_count.setText(f"{len(images)} 张候选")
        self.image_count.setObjectName("pillGood" if images else "pillOff")
        self.image_count.style().unpolish(self.image_count)
        self.image_count.style().polish(self.image_count)
        for index, image in enumerate(images):
            card = ImageCard(
                image,
                selected=selected == image.path.resolve(),
            )
            card.selection_requested.connect(
                lambda image_path, character_name=name: self.selection_requested.emit(
                    character_name,
                    image_path,
                )
            )
            card.unlock_requested.connect(
                lambda character_name=name: self.unlock_requested.emit(character_name)
            )
            card.revision_requested.connect(
                lambda image_path, character_name=name: self._open_revision(
                    character_name,
                    image_path,
                )
            )
            self.gallery_grid.addWidget(card, index // 3, index % 3)
        if not images:
            empty = QLabel("还没有候选图。选择一个或多个模型后生成第一组定妆照。")
            empty.setObjectName("muted")
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            empty.setMinimumHeight(220)
            self.gallery_grid.addWidget(empty, 0, 0, 1, 3)

    def set_generating(self, active: bool) -> None:
        self.generate.setDisabled(active)
        self.save_prompt.setDisabled(active)
        self.layout_preset.setDisabled(active)
        for button in self.model_buttons.values():
            button.setDisabled(active or not button.property("modelAvailable"))
        self.generate.setText("正在生成…" if active else "使用所选模型生成")
        if active:
            self.generation_started_at = time.monotonic()
            self.generation_progress.setValue(0)
            self.generation_stage.setText("正在准备任务")
            self.elapsed_timer.start(1000)
        else:
            self.elapsed_timer.stop()

    def set_generation_progress(self, percent: int, message: str) -> None:
        self.generation_progress.setValue(percent)
        self.generation_stage.setText(message)
        self._refresh_elapsed()

    def finish_generation(self, elapsed_seconds: float, message: str) -> None:
        self.elapsed_timer.stop()
        self.generation_progress.setValue(100)
        self.generation_stage.setText(message)
        self.generation_elapsed.setText(self._format_elapsed(elapsed_seconds))

    def set_gpu_status(self, status: GpuStatus) -> None:
        if status.ssh_online and status.comfy_online and status.available_model_ids:
            text, object_name = "GPU 已就绪", "pillGood"
        elif status.ssh_online:
            text, object_name = "GPU 需检查", "pillWarn"
        else:
            text, object_name = "GPU 离线", "pillOff"
        self.gpu_status.setText(text)
        self.gpu_status.setObjectName(object_name)
        self.gpu_status.style().unpolish(self.gpu_status)
        self.gpu_status.style().polish(self.gpu_status)
        available = set(status.available_model_ids)
        for model_id, button in self.model_buttons.items():
            is_available = model_id in available
            button.setProperty("modelAvailable", is_available)
            button.setEnabled(is_available)
            button.setToolTip(
                
                    f"已安装：{IMAGE_MODEL_PRESETS[model_id].filename}"
                    if is_available
                    else f"服务器未安装：{IMAGE_MODEL_PRESETS[model_id].filename}"
                
            )
            if not is_available:
                button.setChecked(False)
        if available and not any(
            button.isChecked() for button in self.model_buttons.values()
        ):
            for model_id in IMAGE_MODEL_PRESETS:
                if model_id in available:
                    self.model_buttons[model_id].setChecked(True)
                    break

    def _request_generation(self) -> None:
        item = self.character_list.currentItem()
        if item:
            model_ids = [
                model_id
                for model_id, button in self.model_buttons.items()
                if button.isChecked() and button.isEnabled()
            ]
            if not model_ids:
                QMessageBox.warning(self, "未选择模型", "请至少选择一个可用生图模型。")
                return
            self.generate_requested.emit(
                item.text(),
                self.profile.toPlainText().strip(),
                self.style.currentText(),
                model_ids,
                str(self.layout_preset.currentData()),
                self.count.value(),
                self.seed.value(),
            )

    def _open_revision(self, character: str, image_path: Path) -> None:
        dialog = ImageRevisionDialog(
            image_path,
            self.profile.toPlainText().strip(),
            title=character,
            parent=self,
        )
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.revision_requested.emit(
                character,
                image_path,
                dialog.payload(),
            )

    def _save_prompt(self) -> None:
        item = self.character_list.currentItem()
        if item:
            self.save_prompt_requested.emit(
                item.text(),
                self.profile.toPlainText().strip(),
                self.style.currentText(),
                str(self.layout_preset.currentData()),
            )

    def _apply_style(self) -> None:
        self.profile.setPlainText(apply_style(self.profile.toPlainText(), self.style.currentText()))

    def _refresh_elapsed(self) -> None:
        if self.generation_started_at:
            elapsed = time.monotonic() - self.generation_started_at
            self.generation_elapsed.setText(self._format_elapsed(elapsed))

    @staticmethod
    def _format_elapsed(seconds: float) -> str:
        minutes, remaining = divmod(int(seconds), 60)
        return f"{minutes:02d}:{remaining:02d}"

    def _clear_gallery(self) -> None:
        while self.gallery_grid.count():
            item = self.gallery_grid.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()


class StoryboardPage(QWidget):
    save_prompt_requested = Signal(int, int, str, str)
    generate_images_requested = Signal(int)
    regenerate_sequence_requested = Signal(int)
    regenerate_rejected_requested = Signal(int)
    image_qc_requested = Signal(int, int, str, str)
    revision_requested = Signal(int, int, object, object)
    candidate_selected_requested = Signal(int, int, object)

    def __init__(self) -> None:
        super().__init__()
        self.episode: EpisodeSnapshot | None = None
        self.episodes: list[EpisodeSnapshot] = []
        self.current_shot_number = 0
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 26)
        layout.setSpacing(12)
        layout.addWidget(PageHeader("分镜脚本", "查看镜头拆分，修改生图提示词并应用统一视觉风格"))
        self.episode_label = QLabel("尚未加载分镜")
        self.episode_label.setObjectName("cardTitle")
        episode_row = QHBoxLayout()
        self.episode_combo = QComboBox()
        self.episode_combo.setMinimumWidth(220)
        self.episode_combo.currentIndexChanged.connect(self._select_episode)
        self.image_readiness = QLabel("0/0 有画面")
        self.image_readiness.setObjectName("pillOff")
        self.generate_missing_images = QPushButton("自动补全缺失画面")
        self.generate_missing_images.setObjectName("primaryButton")
        self.generate_missing_images.clicked.connect(
            lambda: (
                self.generate_images_requested.emit(self.episode.number)
                if self.episode
                else None
            )
        )
        self.regenerate_sequence = QPushButton("重做连续首帧")
        self.regenerate_sequence.setObjectName("secondaryButton")
        self.regenerate_sequence.setToolTip(
            "重新规划整集镜头连续组，并按顺序用上一镜头作为视觉参考生成首帧；"
            "旧候选图仍会保留"
        )
        self.regenerate_sequence.clicked.connect(
            lambda: (
                self.regenerate_sequence_requested.emit(self.episode.number)
                if self.episode
                else None
            )
        )
        self.regenerate_rejected = QPushButton("重做驳回首帧")
        self.regenerate_rejected.setObjectName("secondaryButton")
        self.regenerate_rejected.setToolTip(
            "只重新生成本集已标记为“驳回”的首帧；通过和待审核图片保持不变"
        )
        self.regenerate_rejected.clicked.connect(
            lambda: (
                self.regenerate_rejected_requested.emit(self.episode.number)
                if self.episode
                else None
            )
        )
        episode_row.addWidget(self.episode_label)
        episode_row.addStretch()
        episode_row.addWidget(self.image_readiness)
        episode_row.addWidget(self.generate_missing_images)
        episode_row.addWidget(self.regenerate_rejected)
        episode_row.addWidget(self.regenerate_sequence)
        episode_row.addWidget(QLabel("剧集"))
        episode_row.addWidget(self.episode_combo)
        layout.addLayout(episode_row)
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(
            ["镜头", "画面描述", "连续承接", "风格", "首帧", "质检"]
        )
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.currentCellChanged.connect(self._show_shot)
        self.table.verticalHeader().setVisible(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(5, QHeaderView.ResizeMode.ResizeToContents)
        layout.addWidget(self.table, 2)

        editor_card = QFrame()
        editor_card.setObjectName("card")
        editor_layout = QVBoxLayout(editor_card)
        editor_layout.setContentsMargins(18, 15, 18, 16)
        editor_top = QHBoxLayout()
        self.editor_title = QLabel("选择一个镜头")
        self.editor_title.setObjectName("cardTitle")
        self.save_state = QLabel("未修改")
        self.save_state.setObjectName("pillOff")
        editor_top.addWidget(self.editor_title)
        editor_top.addStretch()
        editor_top.addWidget(self.save_state)
        editor_layout.addLayout(editor_top)
        self.continuity_summary = QLabel("尚未规划镜头连续性")
        self.continuity_summary.setObjectName("muted")
        self.continuity_summary.setWordWrap(True)
        editor_layout.addWidget(self.continuity_summary)
        self.prompt_editor = QTextEdit()
        self.prompt_editor.setMaximumHeight(110)
        self.prompt_editor.setPlaceholderText("镜头英文生图提示词")
        self.prompt_editor.textChanged.connect(self._mark_modified)
        editor_content = QHBoxLayout()
        self.image_preview = QLabel("当前镜头没有首帧")
        self.image_preview.setObjectName("imageSurface")
        self.image_preview.setFixedSize(240, 135)
        self.image_preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        editor_content.addWidget(self.image_preview)
        editor_content.addWidget(self.prompt_editor, 1)
        editor_layout.addLayout(editor_content)
        actions = QHBoxLayout()
        actions.addWidget(QLabel("预设风格"))
        self.style = QComboBox()
        self.style.addItems(list(STYLE_PRESETS))
        self.apply_style_button = QPushButton("应用到提示词")
        self.apply_style_button.setObjectName("secondaryButton")
        self.apply_style_button.clicked.connect(self._apply_style)
        self.save_button = QPushButton("保存镜头提示词")
        self.save_button.setObjectName("primaryButton")
        self.save_button.clicked.connect(self._save_prompt)
        self.revise_image = QPushButton("不满意 · 修改当前图")
        self.revise_image.setObjectName("secondaryButton")
        self.revise_image.clicked.connect(self._open_current_revision)
        self.revise_image.setEnabled(False)
        self.image_history = QPushButton("历史版本")
        self.image_history.setObjectName("secondaryButton")
        self.image_history.clicked.connect(self._open_image_history)
        self.image_history.setEnabled(False)
        actions.addWidget(self.style)
        actions.addWidget(self.apply_style_button)
        actions.addStretch()
        actions.addWidget(self.image_history)
        actions.addWidget(self.revise_image)
        actions.addWidget(self.save_button)
        editor_layout.addLayout(actions)
        qc_actions = QHBoxLayout()
        self.qc_status = QLabel("质检：未选择镜头")
        self.qc_status.setObjectName("pillOff")
        self.qc_note = QLineEdit()
        self.qc_note.setPlaceholderText("驳回原因或通过备注（可选）")
        self.approve_image = QPushButton("通过此首帧")
        self.approve_image.setObjectName("primaryButton")
        self.approve_image.clicked.connect(
            lambda: self._request_image_qc("approved")
        )
        self.reject_image = QPushButton("驳回并重做")
        self.reject_image.setObjectName("secondaryButton")
        self.reject_image.clicked.connect(
            lambda: self._request_image_qc("rejected")
        )
        self.previous_qc = QPushButton("上一个待处理")
        self.previous_qc.setObjectName("secondaryButton")
        self.previous_qc.clicked.connect(lambda: self._jump_qc_issue(-1))
        self.next_qc = QPushButton("下一个待处理")
        self.next_qc.setObjectName("secondaryButton")
        self.next_qc.clicked.connect(lambda: self._jump_qc_issue(1))
        self.approve_image.setEnabled(False)
        self.reject_image.setEnabled(False)
        qc_actions.addWidget(self.qc_status)
        qc_actions.addWidget(self.qc_note, 1)
        qc_actions.addWidget(self.previous_qc)
        qc_actions.addWidget(self.next_qc)
        qc_actions.addWidget(self.reject_image)
        qc_actions.addWidget(self.approve_image)
        editor_layout.addLayout(qc_actions)
        layout.addWidget(editor_card, 1)

    def set_episode(self, episode: EpisodeSnapshot | None) -> None:
        self.episode = episode
        total_duration = (
            sum(shot.duration_seconds for shot in episode.shots)
            if episode
            else 0.0
        )
        duration_note = (
            f" · 预计 {total_duration:.0f} 秒"
            + ("（不足 60 秒）" if total_duration < 60 else "")
            if episode
            else ""
        )
        self.episode_label.setText(
            f"第 {episode.number} 集 · {episode.title} · "
            f"{len(episode.shots)} 个镜头{duration_note}"
            if episode
            else "尚未加载分镜"
        )
        self.table.setRowCount(0)
        if not episode:
            self.prompt_editor.clear()
            self.image_readiness.setText("0/0 有画面")
            self.generate_missing_images.setEnabled(False)
            self.regenerate_sequence.setEnabled(False)
            self.regenerate_rejected.setEnabled(False)
            self._show_image_preview(None)
            self.qc_status.setText("质检：未选择镜头")
            self.approve_image.setEnabled(False)
            self.reject_image.setEnabled(False)
            self.revise_image.setEnabled(False)
            self.image_history.setEnabled(False)
            return
        ready_count = sum(bool(shot.source_image) for shot in episode.shots)
        approved_count = sum(
            shot.image_qc_status == "approved" for shot in episode.shots
        )
        rejected_count = sum(
            shot.image_qc_status == "rejected" for shot in episode.shots
        )
        self.image_readiness.setText(
            f"{ready_count}/{len(episode.shots)} 有画面 · "
            f"{approved_count}/{len(episode.shots)} 已通过"
        )
        self.image_readiness.setObjectName(
            "pillGood" if approved_count == len(episode.shots) else "pillWarn"
        )
        self.image_readiness.style().unpolish(self.image_readiness)
        self.image_readiness.style().polish(self.image_readiness)
        self.generate_missing_images.setEnabled(ready_count < len(episode.shots))
        self.regenerate_rejected.setEnabled(rejected_count > 0)
        self.regenerate_sequence.setEnabled(bool(episode.shots))
        self.table.setRowCount(len(episode.shots))
        for row, shot in enumerate(episode.shots):
            number = QTableWidgetItem(f"{shot.number:02d}")
            number.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.table.setItem(row, 0, number)
            self.table.setItem(row, 1, QTableWidgetItem(shot.description))
            self.table.setItem(
                row,
                2,
                QTableWidgetItem(
                    (
                        f"{shot.continuity_group} · "
                        f"承接 {shot.reference_shot_number:02d}"
                    )
                    if shot.reference_shot_number
                    else f"{shot.continuity_group} · 起始"
                ),
            )
            self.table.setItem(row, 3, QTableWidgetItem(shot.style))
            self.table.setItem(
                row,
                4,
                QTableWidgetItem(
                    shot.source_image.name if shot.source_image else "待自动生成"
                ),
            )
            qc_label = {
                "approved": "已通过",
                "rejected": "已驳回",
                "pending": "待审核",
                "missing": "无首帧",
            }.get(shot.image_qc_status, "待审核")
            self.table.setItem(row, 5, QTableWidgetItem(qc_label))
            self.table.setRowHeight(row, 66)
        if episode.shots:
            self.table.setCurrentCell(0, 0)

    def set_episodes(self, episodes: list[EpisodeSnapshot]) -> None:
        current_number = self.episode.number if self.episode else 0
        self.episodes = episodes
        self.episode_combo.blockSignals(True)
        self.episode_combo.clear()
        for episode in episodes:
            self.episode_combo.addItem(
                f"第 {episode.number} 集 · {episode.title}",
                episode.number,
            )
        index = self.episode_combo.findData(current_number)
        self.episode_combo.setCurrentIndex(max(0, index))
        self.episode_combo.blockSignals(False)
        self.set_episode(episodes[max(0, index)] if episodes else None)

    def _select_episode(self, index: int) -> None:
        if 0 <= index < len(self.episodes):
            self.set_episode(self.episodes[index])

    def _show_shot(
        self,
        row: int,
        _column: int,
        _previous_row: int,
        _previous_column: int,
    ) -> None:
        if not self.episode or row < 0 or row >= len(self.episode.shots):
            return
        shot = self.episode.shots[row]
        self.current_shot_number = shot.number
        self.editor_title.setText(f"镜头 {shot.number:02d} 生图提示词")
        self.prompt_editor.blockSignals(True)
        self.prompt_editor.setPlainText(shot.prompt)
        self.prompt_editor.blockSignals(False)
        self.style.setCurrentText(shot.style)
        self._show_image_preview(shot.source_image)
        self.qc_note.setText(shot.image_qc_note)
        qc_text, qc_object = {
            "approved": ("质检：已通过", "pillGood"),
            "rejected": ("质检：已驳回，等待重做", "pillWarn"),
            "pending": ("质检：待审核", "pillWarn"),
            "missing": ("质检：没有首帧", "pillOff"),
        }.get(shot.image_qc_status, ("质检：待审核", "pillWarn"))
        self.qc_status.setText(qc_text)
        self.qc_status.setObjectName(qc_object)
        self.qc_status.style().unpolish(self.qc_status)
        self.qc_status.style().polish(self.qc_status)
        has_image = bool(shot.source_image and shot.source_image.is_file())
        self.approve_image.setEnabled(has_image)
        self.reject_image.setEnabled(has_image)
        self.revise_image.setEnabled(has_image)
        self.image_history.setEnabled(bool(shot.image_candidates))
        reference = (
            f"镜头 {shot.reference_shot_number:02d}"
            if shot.reference_shot_number
            else "本连续组起始镜头"
        )
        self.continuity_summary.setText(
            f"连续组：{shot.continuity_group}  ·  节拍：{shot.beat_type}/"
            f"{shot.action_phase}  ·  视觉参考：{reference}  ·  "
            f"重绘幅度：{shot.reference_denoise:.2f}\n"
            f"入镜：{shot.entry_state or '待规划'}\n"
            f"出镜：{shot.exit_state or '待规划'}\n"
            f"匹配锚点：{shot.match_anchor or '待规划'}"
        )
        self._set_save_state("已保存", "good")

    def _apply_style(self) -> None:
        self.prompt_editor.setPlainText(
            apply_style(
                self.prompt_editor.toPlainText(),
                self.style.currentText(),
            )
        )

    def _save_prompt(self) -> None:
        if not self.episode or not self.current_shot_number:
            return
        self.save_prompt_requested.emit(
            self.episode.number,
            self.current_shot_number,
            self.prompt_editor.toPlainText().strip(),
            self.style.currentText(),
        )

    def _open_current_revision(self) -> None:
        if not self.episode or not self.current_shot_number:
            return
        shot = next(
            (
                item
                for item in self.episode.shots
                if item.number == self.current_shot_number
            ),
            None,
        )
        if not shot or not shot.source_image or not shot.source_image.is_file():
            QMessageBox.warning(self, "没有可修改图片", "请先为当前镜头生成或选择一张首帧。")
            return
        dialog = ImageRevisionDialog(
            shot.source_image,
            self.prompt_editor.toPlainText().strip(),
            title=f"镜头 {shot.number:02d}",
            parent=self,
        )
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.revision_requested.emit(
                self.episode.number,
                shot.number,
                shot.source_image,
                dialog.payload(),
            )

    def _open_image_history(self) -> None:
        if not self.episode or not self.current_shot_number:
            return
        shot = next(
            (
                item
                for item in self.episode.shots
                if item.number == self.current_shot_number
            ),
            None,
        )
        if not shot or not shot.image_candidates:
            QMessageBox.information(self, "没有历史版本", "当前镜头还没有已保存的候选图。")
            return
        images = [item.path for item in shot.image_candidates]
        labels = {
            item.path: (
                f"{item.layout_label} · {item.model_label}"
                + (f" · {item.generated_at}" if item.generated_at else "")
            )
            for item in shot.image_candidates
        }
        dialog = ImageRevisionResultDialog(
            images,
            window_title=f"镜头 {shot.number:02d} 历史版本",
            heading_text=f"镜头 {shot.number:02d} · 选择要恢复的首帧",
            note_text="选择历史图只会切换当前首帧，全部候选和生成记录都会保留。",
            labels=labels,
            parent=self,
        )
        dialog.exec()
        if dialog.selected_path:
            self.candidate_selected_requested.emit(
                self.episode.number,
                shot.number,
                dialog.selected_path,
            )

    def _mark_modified(self) -> None:
        self._set_save_state("未保存", "warn")

    def _request_image_qc(self, status: str) -> None:
        if not self.episode or not self.current_shot_number:
            return
        self.image_qc_requested.emit(
            self.episode.number,
            self.current_shot_number,
            status,
            self.qc_note.text().strip(),
        )

    def _jump_qc_issue(self, direction: int) -> None:
        if not self.episode:
            return
        rows = [
            row
            for row, shot in enumerate(self.episode.shots)
            if shot.source_image and shot.image_qc_status != "approved"
        ]
        if not rows:
            return
        current = self.table.currentRow()
        if direction < 0:
            candidates = [row for row in rows if row < current]
            target = candidates[-1] if candidates else rows[-1]
        else:
            candidates = [row for row in rows if row > current]
            target = candidates[0] if candidates else rows[0]
        self.table.setCurrentCell(target, 0)

    def select_next_qc(self, after_shot_number: int) -> None:
        """Select the next pending or rejected shot after a saved QC decision."""

        if not self.episode:
            return
        candidates = [
            (row, shot)
            for row, shot in enumerate(self.episode.shots)
            if shot.source_image and shot.image_qc_status != "approved"
        ]
        if not candidates:
            return
        target = next(
            (
                row
                for row, shot in candidates
                if shot.number > after_shot_number
            ),
            candidates[0][0],
        )
        self.table.setCurrentCell(target, 0)

    def _show_image_preview(self, path: Path | None) -> None:
        if not path or not path.is_file():
            self.image_preview.setPixmap(QPixmap())
            self.image_preview.setText("当前镜头没有首帧")
            return
        pixmap = QPixmap(str(path))
        if pixmap.isNull():
            self.image_preview.setPixmap(QPixmap())
            self.image_preview.setText(path.name)
            return
        self.image_preview.setText("")
        self.image_preview.setPixmap(
            pixmap.scaled(
                QSize(230, 125),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )

    def set_saved(self) -> None:
        self._set_save_state("已保存", "good")

    def set_image_generation(self, active: bool, message: str = "") -> None:
        all_ready = bool(self.episode) and all(
            shot.source_image for shot in self.episode.shots
        )
        self.generate_missing_images.setDisabled(
            active or not self.episode or all_ready
        )
        self.regenerate_sequence.setDisabled(active or not self.episode)
        rejected_ready = bool(self.episode) and any(
            shot.image_qc_status == "rejected" for shot in self.episode.shots
        )
        self.regenerate_rejected.setDisabled(active or not rejected_ready)
        has_current_image = bool(
            self.episode
            and any(
                shot.number == self.current_shot_number and shot.source_image
                for shot in self.episode.shots
            )
        )
        has_history = bool(
            self.episode
            and any(
                shot.number == self.current_shot_number
                and shot.image_candidates
                for shot in self.episode.shots
            )
        )
        self.revise_image.setDisabled(active or not has_current_image)
        self.image_history.setDisabled(active or not has_history)
        if message:
            self.image_readiness.setText(message)
            self.image_readiness.setObjectName(
                "pillWarn" if active else "pillGood"
            )
            self.image_readiness.style().unpolish(self.image_readiness)
            self.image_readiness.style().polish(self.image_readiness)

    def set_revision_progress(self, percent: int, message: str) -> None:
        self.image_readiness.setText(f"{percent}% · {message}")
        self.image_readiness.setObjectName(
            "pillGood" if percent >= 100 else "pillWarn"
        )
        self.image_readiness.style().unpolish(self.image_readiness)
        self.image_readiness.style().polish(self.image_readiness)

    def _set_save_state(self, text: str, state: str) -> None:
        self.save_state.setText(text)
        self.save_state.setObjectName(
            {"good": "pillGood", "warn": "pillWarn"}.get(state, "pillOff")
        )
        self.save_state.style().unpolish(self.save_state)
        self.save_state.style().polish(self.save_state)


class NovelImportPage(QWidget):
    process_requested = Signal(object, int)
    reprocess_requested = Signal(int)

    def __init__(self) -> None:
        super().__init__()
        self.started_at = 0.0
        self.total_chapter_count = 0
        self.processing_operation = "import"
        self.elapsed_timer = QTimer(self)
        self.elapsed_timer.timeout.connect(self._refresh_elapsed)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 26)
        layout.setSpacing(12)
        layout.addWidget(
            PageHeader(
                "小说导入与自动处理",
                "导入 TXT 或 Markdown，自动切章、提取人物并生成可查看的分镜",
            )
        )

        controls = QFrame()
        controls.setObjectName("card")
        controls_layout = QGridLayout(controls)
        controls_layout.setContentsMargins(18, 16, 18, 17)
        controls_layout.setHorizontalSpacing(10)
        controls_layout.setVerticalSpacing(10)
        controls_layout.addWidget(QLabel("小说文件"), 0, 0)
        self.source = QLineEdit()
        self.source.setPlaceholderText("请选择 TXT、Markdown 或章节 JSON 文件")
        browse = QPushButton("选择文件")
        browse.setObjectName("secondaryButton")
        browse.clicked.connect(self._choose_source)
        controls_layout.addWidget(self.source, 0, 1)
        controls_layout.addWidget(browse, 0, 2)
        controls_layout.addWidget(QLabel("自动分析"), 1, 0)
        self.analysis_limit = QSpinBox()
        self.analysis_limit.setRange(0, 100)
        self.analysis_limit.setValue(3)
        self.analysis_limit.setSpecialValueText("全部章节")
        self.analysis_limit.setSuffix(" 章")
        self.analysis_limit.setToolTip("小说会完整导入；此处只限制本次自动分析和分镜数量")
        self.start = QPushButton("导入并自动处理")
        self.start.setObjectName("primaryButton")
        self.start.clicked.connect(self._request_process)
        controls_layout.addWidget(self.analysis_limit, 1, 1)
        controls_layout.addWidget(self.start, 1, 2)
        controls_layout.addWidget(QLabel("已有内容"), 2, 0)
        reprocess_hint = QLabel("不重复导入，强制重做分析并重建对应分镜")
        reprocess_hint.setObjectName("muted")
        self.reprocess = QPushButton("重新处理已有内容")
        self.reprocess.setObjectName("secondaryButton")
        self.reprocess.clicked.connect(self._request_reprocess)
        controls_layout.addWidget(reprocess_hint, 2, 1)
        controls_layout.addWidget(self.reprocess, 2, 2)
        self.auto_generate_images = QCheckBox(
            "分镜完成后自动生成缺失首帧并回填到视频任务"
        )
        self.auto_generate_images.setChecked(True)
        self.auto_generate_images.setToolTip(
            "需要 GPU 服务器、ComfyUI 和至少一个可用生图模型；"
            "条件不满足时保留分镜并明确提示待补全。"
        )
        controls_layout.addWidget(self.auto_generate_images, 3, 1, 1, 2)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.stage = QLabel("等待导入")
        self.stage.setObjectName("muted")
        self.elapsed = QLabel("00:00")
        self.elapsed.setObjectName("pillOff")
        controls_layout.addWidget(self.progress, 4, 1)
        progress_meta = QHBoxLayout()
        progress_meta.addWidget(self.stage)
        progress_meta.addWidget(self.elapsed)
        controls_layout.addLayout(progress_meta, 4, 2)
        layout.addWidget(controls)

        self.tabs = QTabWidget()
        self.chapter_table = QTableWidget(0, 4)
        self.chapter_table.setHorizontalHeaderLabels(["章节", "标题", "字数", "内容预览"])
        self.character_table = QTableWidget(0, 3)
        self.character_table.setHorizontalHeaderLabels(["人物", "风格", "视觉设定"])
        self.shot_table = QTableWidget(0, 7)
        self.shot_table.setHorizontalHeaderLabels(
            [
                "剧集",
                "镜头",
                "画面描述",
                "风格",
                "生图提示词",
                "首帧",
                "视频提示词",
            ]
        )
        for table in (
            self.chapter_table,
            self.character_table,
            self.shot_table,
        ):
            table.setAlternatingRowColors(True)
            table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
            table.verticalHeader().setVisible(False)
            table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.tabs.addTab(self.chapter_table, "切分章节")
        self.tabs.addTab(self.character_table, "提取人物")
        self.tabs.addTab(self.shot_table, "生成分镜")
        layout.addWidget(self.tabs, 1)

    def set_data(
        self,
        chapters: list[ChapterSnapshot],
        episodes: list[EpisodeSnapshot],
        total_chapters: int | None = None,
    ) -> None:
        self.chapter_table.setRowCount(len(chapters))
        for row, chapter in enumerate(chapters):
            values = (
                chapter.order,
                chapter.title,
                chapter.character_count,
                chapter.preview,
            )
            for column, value in enumerate(values):
                self.chapter_table.setItem(
                    row,
                    column,
                    QTableWidgetItem(str(value)),
                )
            self.chapter_table.setRowHeight(row, 42)

        character_map = {
            character.name: character for episode in episodes for character in episode.characters
        }
        characters = list(character_map.values())
        self.character_table.setRowCount(len(characters))
        for row, character in enumerate(characters):
            values = (character.name, character.style, character.profile)
            for column, value in enumerate(values):
                self.character_table.setItem(
                    row,
                    column,
                    QTableWidgetItem(str(value)),
                )
            self.character_table.setRowHeight(row, 54)

        shots = [(episode.number, shot) for episode in episodes for shot in episode.shots]
        self.shot_table.setRowCount(len(shots))
        for row, (episode_number, shot) in enumerate(shots):
            values = (
                episode_number,
                shot.number,
                shot.description,
                shot.style,
                shot.prompt,
                shot.source_image.name if shot.source_image else "待自动生成",
                (
                    "已自动填写"
                    if (
                        shot.subject_motion
                        and shot.continuity_constraints
                        and shot.negative_prompt
                    )
                    else "待补全"
                ),
            )
            for column, value in enumerate(values):
                self.shot_table.setItem(
                    row,
                    column,
                    QTableWidgetItem(str(value)),
                )
            self.shot_table.setRowHeight(row, 60)
        chapter_count = total_chapters if total_chapters is not None else len(chapters)
        self.total_chapter_count = chapter_count
        self.reprocess.setEnabled(chapter_count > 0)
        chapter_label = f"切分章节 · {chapter_count}"
        if chapter_count > len(chapters):
            chapter_label += f"（显示前 {len(chapters)}）"
        self.tabs.setTabText(0, chapter_label)
        self.tabs.setTabText(1, f"提取人物 · {len(characters)}")
        self.tabs.setTabText(2, f"生成分镜 · {len(shots)}")

    def set_processing(self, active: bool, operation: str = "import") -> None:
        if active:
            self.processing_operation = operation
        self.start.setDisabled(active)
        self.reprocess.setDisabled(active or self.total_chapter_count == 0)
        self.source.setDisabled(active)
        self.analysis_limit.setDisabled(active)
        if active:
            self.started_at = time.monotonic()
            self.progress.setValue(0)
            self.stage.setText(
                "正在准备重新处理"
                if operation == "reprocess"
                else "正在准备导入"
            )
            self.elapsed_timer.start(1000)
            if operation == "reprocess":
                self.reprocess.setText("正在重新处理…")
            else:
                self.start.setText("正在自动处理…")
        else:
            self.elapsed_timer.stop()
            self.start.setText("导入并自动处理")
            self.reprocess.setText("重新处理已有内容")

    def set_progress(self, percent: int, message: str) -> None:
        self.progress.setValue(percent)
        self.stage.setText(message)
        self._refresh_elapsed()

    def finish_processing(self, message: str) -> None:
        self.elapsed_timer.stop()
        self.progress.setValue(100)
        self.stage.setText(message)
        self._refresh_elapsed()

    def _choose_source(self) -> None:
        path, _filter = QFileDialog.getOpenFileName(
            self,
            "选择小说文件",
            "",
            "小说文件 (*.txt *.md *.markdown *.json);;所有文件 (*)",
        )
        if path:
            self.source.setText(path)

    def _request_process(self) -> None:
        source = Path(self.source.text().strip())
        if not source.is_file():
            QMessageBox.warning(self, "请选择小说", "请选择一个存在的小说文件。")
            return
        self.process_requested.emit(source, self.analysis_limit.value())

    def _request_reprocess(self) -> None:
        if self.total_chapter_count <= 0:
            QMessageBox.warning(
                self,
                "没有已有内容",
                "当前项目没有已导入章节，请先导入小说。",
            )
            return
        limit = self.analysis_limit.value()
        target = (
            self.total_chapter_count
            if limit == 0
            else min(limit, self.total_chapter_count)
        )
        answer = QMessageBox.question(
            self,
            "确认重新处理",
            (
                f"将强制重新分析前 {target} 章并重建对应分镜。\n\n"
                "已有 Episode 会先自动备份；手工修改的角色或镜头提示词可能"
                "被新结果覆盖。候选图片和已选定的定妆照不会删除。\n\n"
                "是否继续？"
            ),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer == QMessageBox.StandardButton.Yes:
            self.reprocess_requested.emit(limit)

    def _refresh_elapsed(self) -> None:
        if self.started_at:
            seconds = time.monotonic() - self.started_at
            minutes, remaining = divmod(int(seconds), 60)
            self.elapsed.setText(f"{minutes:02d}:{remaining:02d}")


class JobsPage(QWidget):
    def __init__(self) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 26)
        layout.setSpacing(12)
        layout.addWidget(PageHeader("任务与日志", "查看数据库任务状态和当前桌面会话活动"))
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["任务", "类型", "状态", "进度", "更新时间"])
        self.table.setAlternatingRowColors(True)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.table, 2)
        log_title = QLabel("会话日志")
        log_title.setObjectName("cardTitle")
        self.log = QTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumHeight(190)
        layout.addWidget(log_title)
        layout.addWidget(self.log, 1)

    def set_jobs(self, jobs: list[Any]) -> None:
        self.table.setRowCount(len(jobs))
        for row, job in enumerate(jobs):
            values = (
                job.job_id[:8],
                job.job_type,
                job.status,
                f"{job.progress:.0%}",
                job.updated_at,
            )
            for column, value in enumerate(values):
                self.table.setItem(row, column, QTableWidgetItem(str(value)))
            self.table.setRowHeight(row, 38)

    def append_log(self, message: str) -> None:
        timestamp = time.strftime("%H:%M:%S")
        self.log.append(f"[{timestamp}] {message}")


class SettingsPage(QWidget):
    check_requested = Signal()
    start_comfy_requested = Signal()
    install_identity_adapter_requested = Signal()
    check_llm_requested = Signal()
    start_llm_requested = Signal()
    check_cosy_requested = Signal()
    start_cosy_requested = Signal()
    stop_cosy_requested = Signal()
    deploy_cosy_requested = Signal()
    check_latentsync_requested = Signal()
    deploy_latentsync_requested = Signal()
    deploy_h3_requested = Signal()
    deploy_flf_requested = Signal()
    deploy_kontext_requested = Signal()
    check_local_models_requested = Signal()

    def __init__(self, connection: GpuConnection) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 26)
        layout.setSpacing(13)
        layout.addWidget(PageHeader("连接与设置", "配置 GPU 服务器；密码只保存在当前应用内存"))
        card = QFrame()
        card.setObjectName("card")
        card.setMaximumWidth(760)
        form = QGridLayout(card)
        form.setContentsMargins(22, 20, 22, 22)
        form.setHorizontalSpacing(15)
        form.setVerticalSpacing(12)
        title = QLabel("GPU 服务器")
        title.setObjectName("cardTitle")
        form.addWidget(title, 0, 0, 1, 2)
        self.host = QLineEdit(connection.host)
        self.port = QSpinBox()
        self.port.setRange(1, 65535)
        self.port.setValue(connection.port)
        self.user = QLineEdit(connection.username)
        self.password = QLineEdit(connection.password)
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        self.password.setPlaceholderText("SSH 密码（不会写入磁盘）")
        rows = (
            ("服务器地址", self.host),
            ("SSH 端口", self.port),
            ("用户名", self.user),
            ("密码", self.password),
        )
        for index, (label, widget) in enumerate(rows, start=1):
            form.addWidget(QLabel(label), index, 0)
            form.addWidget(widget, index, 1)
        actions = QHBoxLayout()
        self.check = QPushButton("检测连接")
        self.check.setObjectName("secondaryButton")
        self.check.clicked.connect(self.check_requested.emit)
        self.start = QPushButton("启动 ComfyUI")
        self.start.setObjectName("primaryButton")
        self.start.clicked.connect(self.start_comfy_requested.emit)
        self.identity_install = QPushButton("安装人脸身份参考")
        self.identity_install.setObjectName("secondaryButton")
        self.identity_install.clicked.connect(
            self.install_identity_adapter_requested.emit
        )
        actions.addWidget(self.check)
        actions.addWidget(self.start)
        actions.addWidget(self.identity_install)
        actions.addStretch()
        form.addLayout(actions, 5, 1)
        self.status = QLabel("尚未检测")
        self.status.setObjectName("pillOff")
        form.addWidget(self.status, 6, 1, Qt.AlignmentFlag.AlignLeft)
        cosy_actions = QHBoxLayout()
        self.cosy_check = QPushButton("检测 CosyVoice")
        self.cosy_check.setObjectName("secondaryButton")
        self.cosy_check.clicked.connect(self.check_cosy_requested.emit)
        self.cosy_deploy = QPushButton("安装/修复")
        self.cosy_deploy.setObjectName("secondaryButton")
        self.cosy_deploy.clicked.connect(self.deploy_cosy_requested.emit)
        self.cosy_start = QPushButton("启动 CosyVoice")
        self.cosy_start.setObjectName("primaryButton")
        self.cosy_start.clicked.connect(self.start_cosy_requested.emit)
        self.cosy_stop = QPushButton("停止并释放显存")
        self.cosy_stop.setObjectName("secondaryButton")
        self.cosy_stop.clicked.connect(self.stop_cosy_requested.emit)
        self.cosy_status = QLabel("本地音色尚未检测")
        self.cosy_status.setObjectName("pillOff")
        cosy_actions.addWidget(self.cosy_check)
        cosy_actions.addWidget(self.cosy_deploy)
        cosy_actions.addWidget(self.cosy_start)
        cosy_actions.addWidget(self.cosy_stop)
        cosy_actions.addWidget(self.cosy_status)
        cosy_actions.addStretch()
        form.addLayout(cosy_actions, 7, 1)
        lip_actions = QHBoxLayout()
        self.latentsync_check = QPushButton("检测 LatentSync")
        self.latentsync_check.setObjectName("secondaryButton")
        self.latentsync_check.clicked.connect(
            self.check_latentsync_requested.emit
        )
        self.latentsync_deploy = QPushButton("安装/修复 LatentSync 1.6")
        self.latentsync_deploy.setObjectName("primaryButton")
        self.latentsync_deploy.clicked.connect(
            self.deploy_latentsync_requested.emit
        )
        self.latentsync_status = QLabel("口型模型尚未检测")
        self.latentsync_status.setObjectName("pillOff")
        lip_actions.addWidget(self.latentsync_check)
        lip_actions.addWidget(self.latentsync_deploy)
        lip_actions.addWidget(self.latentsync_status)
        lip_actions.addStretch()
        form.addLayout(lip_actions, 8, 1)
        h3_actions = QHBoxLayout()
        self.h3_deploy = QPushButton("安装/修复 MiniMax H3（约 40GiB）")
        self.h3_deploy.setObjectName("primaryButton")
        self.h3_deploy.clicked.connect(self.deploy_h3_requested.emit)
        self.h3_status = QLabel("H3 原生音视频模型尚未检测")
        self.h3_status.setObjectName("pillOff")
        h3_actions.addWidget(self.h3_deploy)
        h3_actions.addWidget(self.h3_status)
        h3_actions.addStretch()
        form.addLayout(h3_actions, 9, 1)
        flf_actions = QHBoxLayout()
        self.flf_deploy = QPushButton("安装/修复 Wan FLF2V（约 28.9GB）")
        self.flf_deploy.setObjectName("primaryButton")
        self.flf_deploy.clicked.connect(self.deploy_flf_requested.emit)
        self.flf_status = QLabel("FLF2V 首尾帧模型尚未检测")
        self.flf_status.setObjectName("pillOff")
        flf_actions.addWidget(self.flf_deploy)
        flf_actions.addWidget(self.flf_status)
        flf_actions.addStretch()
        form.addLayout(flf_actions, 10, 1)
        kontext_actions = QHBoxLayout()
        self.kontext_deploy = QPushButton("安装/修复 FLUX.1 Kontext（约 11.9GB）")
        self.kontext_deploy.setObjectName("primaryButton")
        self.kontext_deploy.clicked.connect(self.deploy_kontext_requested.emit)
        self.kontext_status = QLabel("Kontext 动作尾帧编辑模型尚未检测")
        self.kontext_status.setObjectName("pillOff")
        kontext_actions.addWidget(self.kontext_deploy)
        kontext_actions.addWidget(self.kontext_status)
        kontext_actions.addStretch()
        form.addLayout(kontext_actions, 11, 1)
        layout.addWidget(card)

        llm_card = QFrame()
        llm_card.setObjectName("card")
        llm_card.setMaximumWidth(760)
        llm_layout = QVBoxLayout(llm_card)
        llm_layout.setContentsMargins(20, 17, 20, 17)
        llm_title = QLabel("本地文本模型（小说分析）")
        llm_title.setObjectName("cardTitle")
        llm_detail = QLabel(
            f"地址：{settings.llm_base_url}  ·  模型：{settings.llm_model}\n"
            f"文件：{settings.llm_model_path}"
        )
        llm_detail.setObjectName("muted")
        llm_detail.setWordWrap(True)
        llm_detail.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        llm_actions = QHBoxLayout()
        self.llm_check = QPushButton("检测文本模型")
        self.llm_check.setObjectName("secondaryButton")
        self.llm_check.clicked.connect(self.check_llm_requested.emit)
        self.llm_start = QPushButton("启动本地模型")
        self.llm_start.setObjectName("primaryButton")
        self.llm_start.clicked.connect(self.start_llm_requested.emit)
        self.llm_status = QLabel("尚未检测")
        self.llm_status.setObjectName("pillOff")
        llm_actions.addWidget(self.llm_check)
        llm_actions.addWidget(self.llm_start)
        llm_actions.addWidget(self.llm_status)
        llm_actions.addStretch()
        llm_layout.addWidget(llm_title)
        llm_layout.addWidget(llm_detail)
        llm_layout.addLayout(llm_actions)
        layout.addWidget(llm_card)

        model_card = QFrame()
        model_card.setObjectName("card")
        model_card.setMaximumWidth(980)
        model_layout = QVBoxLayout(model_card)
        model_layout.setContentsMargins(20, 17, 20, 17)
        model_header = QHBoxLayout()
        model_title = QLabel("本地生成模型中心")
        model_title.setObjectName("cardTitle")
        self.local_model_summary = QLabel("尚未检测本机模型")
        self.local_model_summary.setObjectName("pillOff")
        self.local_model_check = QPushButton("检测本机模型")
        self.local_model_check.setObjectName("secondaryButton")
        self.local_model_check.clicked.connect(
            self.check_local_models_requested.emit
        )
        model_header.addWidget(model_title)
        model_header.addStretch()
        model_header.addWidget(self.local_model_summary)
        model_header.addWidget(self.local_model_check)
        self.local_model_detail = QLabel(
            "检测模型文件、显存兼容性、ComfyUI服务和应用适配器"
        )
        self.local_model_detail.setObjectName("muted")
        self.local_model_detail.setWordWrap(True)
        self.local_model_table = QTableWidget(0, 5)
        self.local_model_table.setHorizontalHeaderLabels(
            ["模型", "用途", "文件", "显存", "当前状态"]
        )
        self.local_model_table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers
        )
        self.local_model_table.verticalHeader().setVisible(False)
        self.local_model_table.horizontalHeader().setSectionResizeMode(
            0,
            QHeaderView.ResizeMode.Stretch,
        )
        for column in (1, 2, 3, 4):
            self.local_model_table.horizontalHeader().setSectionResizeMode(
                column,
                QHeaderView.ResizeMode.ResizeToContents,
            )
        self.local_model_table.setMinimumHeight(230)
        model_layout.addLayout(model_header)
        model_layout.addWidget(self.local_model_detail)
        model_layout.addWidget(self.local_model_table)
        layout.addWidget(model_card)

        note = QFrame()
        note.setObjectName("card")
        note_layout = QVBoxLayout(note)
        note_layout.setContentsMargins(20, 17, 20, 17)
        note_title = QLabel("当前生成配置")
        note_title.setObjectName("cardTitle")
        note_body = QLabel(
            "可选模型：FLUX.1 Krea Dev FP8、Juggernaut XI（SDXL）\n"
            "支持：单模型或多模型同时生成；单人照、白底标准三视图、有背景三视图\n"
            "Krea：CLIP-L + T5XXL FP8 + ae.safetensors\n"
            "Juggernaut XI：SDXL Checkpoint\n"
            "服务器项目：/root/autodl-tmp/manju"
        )
        note_body.setObjectName("muted")
        note_body.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        note_layout.addWidget(note_title)
        note_layout.addWidget(note_body)
        layout.addWidget(note)
        layout.addStretch()

    def connection(self) -> GpuConnection:
        return GpuConnection(
            host=self.host.text().strip(),
            port=self.port.value(),
            username=self.user.text().strip(),
            password=self.password.text(),
        )

    def set_busy(self, busy: bool) -> None:
        self.check.setDisabled(busy)
        self.start.setDisabled(busy)
        self.identity_install.setDisabled(busy)

    def set_h3_busy(self, busy: bool) -> None:
        self.h3_deploy.setDisabled(busy)
        self.h3_deploy.setText(
            "正在安装 H3…" if busy else "安装/修复 MiniMax H3（约 40GiB）"
        )

    def set_flf_busy(self, busy: bool) -> None:
        self.flf_deploy.setDisabled(busy)
        self.flf_deploy.setText(
            "正在安装 FLF2V…"
            if busy
            else "安装/修复 Wan FLF2V（约 28.9GB）"
        )

    def set_kontext_busy(self, busy: bool) -> None:
        self.kontext_deploy.setDisabled(busy)
        self.kontext_deploy.setText(
            "正在安装 Kontext…"
            if busy
            else "安装/修复 FLUX.1 Kontext（约 11.9GB）"
        )

    def set_cosy_busy(self, busy: bool) -> None:
        self.cosy_check.setDisabled(busy)
        self.cosy_deploy.setDisabled(busy)
        self.cosy_start.setDisabled(busy)
        self.cosy_stop.setDisabled(busy)
        self.cosy_start.setText("正在启动…" if busy else "启动 CosyVoice")

    def set_cosy_status(self, status: CosyVoiceStatus) -> None:
        if status.online:
            text, object_name = f"{status.model} 已就绪", "pillGood"
        elif status.installed:
            text, object_name = "已安装，服务未启动", "pillWarn"
        else:
            text, object_name = status.message or "不可用", "pillOff"
        self.cosy_status.setText(text)
        self.cosy_status.setObjectName(object_name)
        self.cosy_status.style().unpolish(self.cosy_status)
        self.cosy_status.style().polish(self.cosy_status)

    def set_latentsync_busy(self, busy: bool) -> None:
        self.latentsync_check.setDisabled(busy)
        self.latentsync_deploy.setDisabled(busy)
        self.latentsync_deploy.setText(
            "正在安装…" if busy else "安装/修复 LatentSync 1.6"
        )

    def set_latentsync_status(self, status: LatentSyncStatus) -> None:
        if status.callable:
            text, object_name = status.message, "pillGood"
        elif status.installing:
            text, object_name = status.message, "pillWarn"
        elif status.installed:
            text, object_name = status.message, "pillWarn"
        else:
            text, object_name = status.message or "不可用", "pillOff"
        self.latentsync_status.setText(text)
        self.latentsync_status.setObjectName(object_name)
        self.latentsync_status.style().unpolish(self.latentsync_status)
        self.latentsync_status.style().polish(self.latentsync_status)

    def set_status(self, status: GpuStatus) -> None:
        if status.ssh_online and status.comfy_online and status.available_model_ids:
            labels = [
                IMAGE_MODEL_PRESETS[model_id].label
                for model_id in status.available_model_ids
                if model_id in IMAGE_MODEL_PRESETS
            ]
            identity = (
                "人脸身份参考已就绪"
                if status.identity_adapter_ready
                else "人脸身份参考未安装"
            )
            text = (
                f"服务器与 ComfyUI 已就绪 · 可用模型：{'、'.join(labels)}"
                f" · {identity}"
            )
            name = "pillGood"
        elif status.ssh_online:
            parts = ["SSH 已连接"]
            if not status.comfy_online:
                parts.append("ComfyUI 未启动")
            if not status.krea_ready:
                parts.append("Krea 文件不完整")
            text, name = " · ".join(parts), "pillWarn"
        else:
            text, name = status.message or "连接失败", "pillOff"
        self.status.setText(text)
        self.status.setObjectName(name)
        self.status.style().unpolish(self.status)
        self.status.style().polish(self.status)
        if status.h3_runtime_ready:
            h3_text, h3_name = "H3 FL2VA 已就绪 · 原生音效与配乐", "pillGood"
        elif status.h3_model_ready:
            h3_text, h3_name = "H3 文件已安装 · ComfyUI 节点未就绪", "pillWarn"
        else:
            h3_text, h3_name = "H3 尚未安装", "pillOff"
        self.h3_status.setText(h3_text)
        self.h3_status.setObjectName(h3_name)
        self.h3_status.style().unpolish(self.h3_status)
        self.h3_status.style().polish(self.h3_status)
        if status.flf_runtime_ready:
            flf_text, flf_name = "Wan FLF2V 14B 已就绪 · 首尾帧动作控制", "pillGood"
        elif status.flf_model_ready:
            flf_text, flf_name = "FLF2V 文件已安装 · ComfyUI 节点未就绪", "pillWarn"
        else:
            flf_text, flf_name = "FLF2V 尚未安装", "pillOff"
        self.flf_status.setText(flf_text)
        self.flf_status.setObjectName(flf_name)
        self.flf_status.style().unpolish(self.flf_status)
        self.flf_status.style().polish(self.flf_status)
        if status.kontext_runtime_ready:
            kontext_text, kontext_name = (
                "FLUX.1 Kontext 已就绪 · 一致性动作尾帧",
                "pillGood",
            )
        elif status.kontext_model_ready:
            kontext_text, kontext_name = (
                "Kontext 文件已安装 · ComfyUI 编辑节点未就绪",
                "pillWarn",
            )
        else:
            kontext_text, kontext_name = "Kontext 尚未安装", "pillOff"
        self.kontext_status.setText(kontext_text)
        self.kontext_status.setObjectName(kontext_name)
        self.kontext_status.style().unpolish(self.kontext_status)
        self.kontext_status.style().polish(self.kontext_status)

    def set_llm_busy(self, busy: bool) -> None:
        self.llm_check.setDisabled(busy)
        self.llm_start.setDisabled(busy)
        self.llm_start.setText("正在启动…" if busy else "启动本地模型")

    def set_llm_status(self, status: LocalLlmStatus) -> None:
        if status.online:
            object_name = "pillGood"
        elif status.model_ready:
            object_name = "pillWarn"
        else:
            object_name = "pillOff"
        self.llm_status.setText(status.message)
        self.llm_status.setObjectName(object_name)
        self.llm_status.style().unpolish(self.llm_status)
        self.llm_status.style().polish(self.llm_status)

    def set_local_models_busy(self, busy: bool) -> None:
        self.local_model_check.setDisabled(busy)
        self.local_model_check.setText("正在检测…" if busy else "检测本机模型")

    def set_local_models_status(
        self,
        inventory: LocalRuntimeInventory,
    ) -> None:
        self.set_local_models_busy(False)
        callable_count = sum(model.callable for model in inventory.models)
        installed_count = sum(model.installed for model in inventory.models)
        self.local_model_summary.setText(
            f"{callable_count} 可调用 / {installed_count} 已安装"
        )
        self.local_model_summary.setObjectName(
            "pillGood" if callable_count else "pillWarn"
        )
        self.local_model_summary.style().unpolish(self.local_model_summary)
        self.local_model_summary.style().polish(self.local_model_summary)
        comfy = "在线" if inventory.comfy_online else "离线"
        self.local_model_detail.setText(
            f"{inventory.gpu_name} · {inventory.vram_gb:g}GB 显存 · "
            f"{inventory.ram_gb:g}GB 内存 · 模型盘剩余 "
            f"{inventory.disk_free_gb:g}GB · 本机 ComfyUI {comfy}\n"
            f"目录：{inventory.model_root}"
        )
        self.local_model_table.setRowCount(len(inventory.models))
        for row, model in enumerate(inventory.models):
            values = (
                model.label,
                model.category,
                (
                    f"{model.installed_size_gb:g}GB"
                    if model.installed
                    else "未安装"
                ),
                f"≥{model.minimum_vram_gb:g}GB",
                model.message,
            )
            for column, value in enumerate(values):
                self.local_model_table.setItem(
                    row,
                    column,
                    QTableWidgetItem(str(value)),
                )
            self.local_model_table.setRowHeight(row, 36)


class NewProjectDialog(QDialog):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("新建项目")
        self.setMinimumWidth(440)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 20)
        layout.setSpacing(11)
        title = QLabel("创建一个独立的漫剧项目")
        title.setStyleSheet("font-size:19px;font-weight:700;color:#101828;")
        note = QLabel("每个项目会分别保存小说、分镜、角色定妆和生成结果。")
        note.setObjectName("muted")
        note.setWordWrap(True)
        self.display_name = QLineEdit()
        self.display_name.setPlaceholderText("例如：绝世武神")
        self.slug = QLineEdit()
        self.slug.setPlaceholderText("例如：jueshi（中英文、数字、下划线或连字符）")
        layout.addWidget(title)
        layout.addWidget(note)
        layout.addSpacing(5)
        layout.addWidget(QLabel("项目名称"))
        layout.addWidget(self.display_name)
        layout.addWidget(QLabel("项目标识"))
        layout.addWidget(self.slug)
        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Ok
        )
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setText("创建项目")
        self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        self.buttons.accepted.connect(self._accept_if_valid)
        self.buttons.rejected.connect(self.reject)
        layout.addSpacing(5)
        layout.addWidget(self.buttons)
        self.display_name.setFocus()

    def values(self) -> tuple[str, str]:
        display_name = self.display_name.text().strip()
        slug = self.slug.text().strip() or display_name
        return slug, display_name or slug

    def _accept_if_valid(self) -> None:
        slug, _display_name = self.values()
        if not slug:
            QMessageBox.warning(self, "信息不完整", "请输入项目名称或项目标识。")
            return
        self.accept()


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Novel2Anime Studio")
        screen = QApplication.primaryScreen()
        if screen is None:
            self.setMinimumSize(960, 640)
            self.resize(1440, 900)
        else:
            available = screen.availableGeometry().size()
            self.setMinimumSize(
                min(960, available.width()),
                min(640, available.height()),
            )
            self.resize(
                min(1440, available.width()),
                min(900, available.height()),
            )
        self.project_service = DesktopProjectService()
        self.gpu_service = GpuServerService()
        self.cosyvoice_service = CosyVoiceRemoteService(self.gpu_service)
        self.latentsync_service = LatentSyncRemoteService(self.gpu_service)
        self.lip_sync_batch_planner = LipSyncBatchPlanner()
        self.llm_service = LocalLlmService()
        self.local_model_service = LocalModelRuntimeService()
        self.local_comfy_service = LocalComfyGenerationService(
            self.local_model_service
        )
        self.voice_library_service = VoiceLibraryService(
            self.project_service.projects_dir
        )
        self.asset_package_service = AssetPackageService(
            self.project_service.projects_dir
        )
        self.video_service = VideoRenderService()
        self.dubbing_service = DubbingService()
        self.qt_settings = QSettings("novel2anime", "studio")
        default = default_gpu_connection()
        default.host = self.qt_settings.value("gpu/host", default.host, str)
        default.port = self.qt_settings.value("gpu/port", default.port, int)
        default.username = self.qt_settings.value("gpu/user", default.username, str)
        self.tasks: set[QThread] = set()
        self.current_project = ""
        self.current_snapshot: ProjectSnapshot | None = None
        self.episodes: list[EpisodeSnapshot] = []
        self.last_gpu_status = GpuStatus()

        root = QWidget()
        root.setObjectName("appRoot")
        self.setCentralWidget(root)
        shell = QHBoxLayout(root)
        shell.setContentsMargins(0, 0, 0, 0)
        shell.setSpacing(0)
        shell.addWidget(self._build_sidebar())

        workspace = QWidget()
        workspace_layout = QVBoxLayout(workspace)
        workspace_layout.setContentsMargins(0, 0, 0, 0)
        workspace_layout.setSpacing(0)
        workspace_layout.addWidget(self._build_topbar())
        self.stack = QStackedWidget()
        self.overview = OverviewPage()
        self.novel_import = NovelImportPage()
        self.characters = CharactersPage()
        self.storyboard = StoryboardPage()
        self.video_generation = VideoGenerationPage()
        self.jobs = JobsPage()
        self.settings_page = SettingsPage(default)
        self.voice_library = VoiceLibraryPage()
        self.asset_library = AssetLibraryPage()
        for page in (
            self.overview,
            self.novel_import,
            self.characters,
            self.storyboard,
            self.video_generation,
            self.jobs,
            self.settings_page,
            self.voice_library,
            self.asset_library,
        ):
            self.stack.addWidget(self._make_scrollable(page))
        workspace_layout.addWidget(self.stack, 1)
        shell.addWidget(workspace, 1)

        self.overview.check_server.connect(self.check_server)
        self.overview.continue_requested.connect(self.navigate)
        self.characters.generate_requested.connect(self.generate_character)
        self.characters.revision_requested.connect(self.revise_character_image)
        self.characters.save_prompt_requested.connect(self.save_character_prompt)
        self.characters.open_folder_requested.connect(self.open_character_folder)
        self.characters.selection_requested.connect(self.select_character_image)
        self.characters.unlock_requested.connect(self.unlock_character_image)
        self.characters.check_server_requested.connect(self.check_server)
        self.storyboard.save_prompt_requested.connect(self.save_shot_prompt)
        self.storyboard.generate_images_requested.connect(
            self.generate_missing_shot_images
        )
        self.storyboard.regenerate_sequence_requested.connect(
            self.regenerate_continuous_shot_images
        )
        self.storyboard.regenerate_rejected_requested.connect(
            self.regenerate_rejected_shot_images
        )
        self.storyboard.image_qc_requested.connect(self.set_shot_image_qc)
        self.storyboard.revision_requested.connect(self.revise_shot_image)
        self.storyboard.candidate_selected_requested.connect(
            self.select_shot_image_candidate
        )
        self.video_generation.generate_requested.connect(self.generate_shot_videos)
        self.video_generation.end_frames_requested.connect(
            self.generate_end_frames_then_videos
        )
        self.video_generation.source_requested.connect(self.set_shot_source_image)
        self.video_generation.end_source_requested.connect(self.set_shot_end_image)
        self.video_generation.save_settings_requested.connect(
            self.save_video_settings
        )
        self.video_generation.compose_requested.connect(self.compose_episode_preview)
        self.video_generation.dub_requested.connect(self.generate_episode_dubbing)
        self.video_generation.timeline_plan_requested.connect(
            self.optimize_episode_timing
        )
        self.video_generation.open_file_requested.connect(self.open_media_file)
        self.novel_import.process_requested.connect(self.process_novel)
        self.novel_import.reprocess_requested.connect(self.reprocess_novel)
        self.settings_page.check_requested.connect(self.check_server)
        self.settings_page.start_comfy_requested.connect(self.start_comfy)
        self.settings_page.install_identity_adapter_requested.connect(
            self.install_identity_adapter
        )
        self.settings_page.check_llm_requested.connect(self.check_llm)
        self.settings_page.start_llm_requested.connect(self.start_local_llm)
        self.settings_page.check_cosy_requested.connect(self.check_cosyvoice)
        self.settings_page.start_cosy_requested.connect(self.start_cosyvoice)
        self.settings_page.stop_cosy_requested.connect(self.stop_cosyvoice)
        self.settings_page.deploy_cosy_requested.connect(self.deploy_cosyvoice)
        self.settings_page.check_latentsync_requested.connect(
            self.check_latentsync
        )
        self.settings_page.deploy_latentsync_requested.connect(
            self.deploy_latentsync
        )
        self.settings_page.deploy_h3_requested.connect(self.deploy_minimax_h3)
        self.settings_page.deploy_flf_requested.connect(self.deploy_wan22_flf2v)
        self.settings_page.deploy_kontext_requested.connect(
            self.deploy_flux_kontext
        )
        self.settings_page.check_local_models_requested.connect(
            self.check_local_models
        )
        self.video_generation.voice_preview_requested.connect(
            self.preview_shot_voice
        )
        self.video_generation.lip_sync_requested.connect(
            self.generate_shot_lip_sync
        )
        self.voice_library.add_voice_requested.connect(self.add_voice_profile)
        self.voice_library.delete_voice_requested.connect(
            self.delete_voice_profile
        )
        self.voice_library.preview_requested.connect(self.preview_library_voice)
        self.voice_library.auto_match_requested.connect(
            self.auto_match_character_voices
        )
        self.voice_library.save_assignments_requested.connect(
            self.save_character_voice_assignments
        )
        self.voice_library.apply_assignments_requested.connect(
            self.apply_character_voice_assignments
        )
        self.asset_library.open_requested.connect(self.open_asset_package)
        self.asset_library.organize_requested.connect(self.organize_asset_packages)
        self.video_generation.lip_sync_batch_requested.connect(
            self.generate_episode_lip_sync
        )
        self._load_projects()
        self.navigate(0)
        self.append_log("桌面应用已启动")
        if default.password:
            QTimer.singleShot(300, self.check_server)
        QTimer.singleShot(500, self.check_video_runtime)
        QTimer.singleShot(650, self.check_dubbing_runtime)
        QTimer.singleShot(900, self.ensure_local_llm)
        QTimer.singleShot(1100, self.check_local_models)

    @staticmethod
    def _make_scrollable(page: QWidget) -> QScrollArea:
        """Keep every workbench page usable on small or high-DPI screens."""
        scroll = QScrollArea()
        scroll.setObjectName("pageScrollArea")
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )
        scroll.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )
        scroll.setWidget(page)
        return scroll

    def _build_sidebar(self) -> QWidget:
        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(224)
        layout = QVBoxLayout(sidebar)
        layout.setContentsMargins(16, 22, 16, 18)
        layout.setSpacing(8)
        brand = QLabel("Novel2Anime")
        brand.setObjectName("brandMark")
        sub = QLabel("LOCAL PRODUCTION STUDIO")
        sub.setObjectName("brandSub")
        layout.addWidget(brand)
        layout.addWidget(sub)
        layout.addSpacing(25)
        caption = QLabel("工作台")
        caption.setObjectName("brandSub")
        layout.addWidget(caption)
        self.nav_buttons: list[QPushButton] = []
        items = (
            ("  项目总览", 0),
            ("  小说导入", 1),
            ("  角色定妆", 2),
            ("  声音角色库", 7),
            ("  本地资源包", 8),
            ("  分镜脚本", 3),
            ("  视频生成", 4),
            ("  任务与日志", 5),
            ("  连接设置", 6),
        )
        for text, index in items:
            button = QPushButton(text)
            button.setObjectName("navButton")
            button.setCheckable(True)
            button.setMinimumHeight(44)
            button.setProperty("page_index", index)
            button.clicked.connect(lambda _checked=False, value=index: self.navigate(value))
            self.nav_buttons.append(button)
            layout.addWidget(button)
        layout.addStretch()
        version = QLabel("V0.3 · Video Pipeline")
        version.setObjectName("brandSub")
        layout.addWidget(version)
        return sidebar

    def _build_topbar(self) -> QWidget:
        bar = QFrame()
        bar.setFixedHeight(68)
        bar.setStyleSheet("QFrame{background:#FFFFFF;border-bottom:1px solid #E5EAF0;}")
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(28, 0, 28, 0)
        title = QLabel("AI 漫剧制作")
        title.setStyleSheet("font-size:16px;font-weight:650;color:#182230;")
        self.project_combo = QComboBox()
        self.project_combo.setMinimumWidth(180)
        self.project_combo.currentIndexChanged.connect(self._on_project_changed)
        self.new_project_button = QPushButton("＋ 新建项目")
        self.new_project_button.setObjectName("primaryButton")
        self.new_project_button.clicked.connect(self.create_project)
        self.refresh_button = QPushButton("刷新")
        self.refresh_button.setObjectName("secondaryButton")
        self.refresh_button.clicked.connect(self.refresh_project)
        self.activity = QLabel("就绪")
        self.activity.setObjectName("pillGood")
        layout.addWidget(title)
        layout.addStretch()
        layout.addWidget(QLabel("项目"))
        layout.addWidget(self.project_combo)
        layout.addWidget(self.new_project_button)
        layout.addWidget(self.refresh_button)
        layout.addWidget(self.activity)
        return bar

    def _load_projects(self) -> None:
        projects = self.project_service.list_projects()
        previous = self.current_project
        self.project_combo.blockSignals(True)
        self.project_combo.clear()
        for slug in projects:
            try:
                display_name = self.project_service.load_project(slug).display_name
            except Exception:
                display_name = slug
            self.project_combo.addItem(display_name, slug)
        self.project_combo.blockSignals(False)
        if projects:
            selected = previous if previous in projects else projects[0]
            index = self.project_combo.findData(selected)
            self.project_combo.setCurrentIndex(max(0, index))
            self.load_project(selected)

    def _on_project_changed(self, index: int) -> None:
        slug = self.project_combo.itemData(index)
        if slug:
            self.load_project(str(slug))

    def create_project(self) -> None:
        dialog = NewProjectDialog(self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        slug, display_name = dialog.values()
        try:
            self.project_service.create_project(slug, display_name)
        except Exception as exc:
            self.show_error("项目创建失败", str(exc))
            return
        self.current_project = slug
        self._load_projects()
        self.set_activity("项目已创建", "good")
        self.append_log(f"已创建项目：{display_name}（{slug}）")
        self.navigate(0)

    def load_project(self, slug: str) -> None:
        if not slug:
            return
        self.current_project = slug
        self.refresh_project()

    def refresh_project(self) -> None:
        if not self.current_project:
            return
        try:
            self.current_snapshot = self.project_service.load_project(self.current_project)
            self.episodes = self.project_service.load_episodes(self.current_project)
            self.overview.set_project(self.current_snapshot)
            episode = self.episodes[0] if self.episodes else None
            self.novel_import.set_data(
                self.project_service.load_chapters(self.current_project),
                self.episodes,
                self.current_snapshot.chapter_count,
            )
            self.characters.set_episode(episode)
            self.storyboard.set_episodes(self.episodes)
            self.video_generation.set_episodes(self.episodes)
            self._refresh_voice_library()
            self._refresh_asset_library()
            self.jobs.set_jobs(self.project_service.load_jobs(self.current_project))
            self.append_log(f"已刷新项目：{self.current_project}")
        except Exception as exc:
            self.show_error("项目加载失败", str(exc))

    def navigate(self, index: int) -> None:
        self.stack.setCurrentIndex(index)
        for button in self.nav_buttons:
            button.setChecked(int(button.property("page_index")) == index)

    def _refresh_voice_library(self) -> None:
        if not self.current_project:
            return
        state = self.voice_library_service.load_state(self.current_project)
        self.voice_library.set_state(
            state.profiles,
            state.traits,
            state.assignments,
        )

    def _refresh_asset_library(self) -> None:
        if not self.current_project:
            return
        state = self.asset_package_service.load_state(self.current_project)
        self.asset_library.set_state(state)

    def open_asset_package(self, key: str) -> None:
        if not self.current_project:
            return
        try:
            path = self.asset_package_service.package_path(
                self.current_project,
                key,
            )
            os.startfile(path)  # type: ignore[attr-defined]
        except Exception as exc:
            self.show_error("打开资源包失败", str(exc))

    def organize_asset_packages(self) -> None:
        if not self.current_project:
            return
        slug = self.current_project
        self.set_activity("正在整理资源包", "warn")
        self.append_log("正在整理人物、场景、人声和合成内容资源包")

        def success(result: OrganizeResult) -> None:
            self.refresh_project()
            self.set_activity("资源包已整理", "good")
            self.append_log(
                f"资源包整理完成：人物 {result.character_files}、"
                f"场景 {result.location_files}、人声 {result.voice_files}、"
                f"成片文件 {result.deliverable_files}"
            )

        def failure(detail: str) -> None:
            self.set_activity("资源包整理失败", "off")
            self.show_error("资源包整理失败", detail)

        self._start_task(
            lambda: self.asset_package_service.organize(slug),
            success,
            failure,
        )

    def add_voice_profile(self, payload: dict[str, Any]) -> None:
        try:
            profile = self.voice_library_service.add_cloned_voice(**payload)
            self._refresh_voice_library()
        except Exception as exc:
            self.show_error("导入声音失败", str(exc))
            return
        self.set_activity("声音已导入", "good")
        self.append_log(f"已导入授权声音：{profile.name}")

    def delete_voice_profile(self, profile_id: str) -> None:
        try:
            self.voice_library_service.delete_profile(profile_id)
            self._refresh_voice_library()
        except Exception as exc:
            self.show_error("删除声音失败", str(exc))
            return
        self.set_activity("声音已删除", "good")
        self.append_log(f"已删除声音档案：{profile_id}")

    def auto_match_character_voices(self) -> None:
        if not self.current_project:
            return
        try:
            assignments = self.voice_library_service.auto_match(
                self.current_project,
                preserve_manual=True,
            )
            self._refresh_voice_library()
        except Exception as exc:
            self.show_error("自动匹配失败", str(exc))
            return
        self.set_activity("人物声音已自动匹配", "good")
        self.append_log(f"已自动匹配 {len(assignments)} 个人物声音")

    def save_character_voice_assignments(
        self,
        selections: dict[str, str],
    ) -> None:
        if not self.current_project:
            return
        try:
            self.voice_library_service.save_manual_assignments(
                self.current_project,
                selections,
            )
            self._refresh_voice_library()
        except Exception as exc:
            self.show_error("保存声音分配失败", str(exc))
            return
        self.set_activity("手动声音分配已保存", "good")
        self.append_log(f"已保存 {len(selections)} 个人物的手动声音分配")

    def apply_character_voice_assignments(self) -> None:
        if not self.current_project:
            return
        answer = QMessageBox.question(
            self,
            "应用人物声音",
            "将把当前人物—声音映射写入全部分镜。\n"
            "如果已完成口型的镜头声音发生变化，对应口型会安全地回到待重做。继续吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Yes,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            result = self.voice_library_service.apply_assignments(
                self.current_project
            )
            self.refresh_project()
        except Exception as exc:
            self.show_error("应用声音分配失败", str(exc))
            return
        reset_count = len(result.lip_sync_reset_shots)
        self.set_activity("人物声音已应用", "good")
        self.append_log(
            f"声音分配已应用到 {result.episodes_updated} 集、"
            f"{result.shots_updated} 个镜头；{reset_count} 个口型待重做"
        )
        QMessageBox.information(
            self,
            "声音分配已应用",
            f"更新 {result.shots_updated} 个镜头。\n"
            f"需要重新生成口型：{reset_count} 个镜头。",
        )

    def preview_library_voice(self, profile_id: str, text: str) -> None:
        if not self.current_project or not self.current_snapshot:
            return
        profile = next(
            (
                item
                for item in self.voice_library_service.load_profiles()
                if item.profile_id == profile_id
            ),
            None,
        )
        if profile is None:
            self.show_error("试听失败", "声音档案不存在。")
            return
        config = self.settings_page.connection()
        if profile.engine == "cosyvoice" and not config.password:
            self.show_error(
                "缺少 GPU 密码",
                "克隆音色由 3090 上的 CosyVoice 3 生成，请先填写 SSH 密码。",
            )
            return
        suffix = ".wav" if profile.engine == "cosyvoice" else ".mp3"
        destination = (
            self.current_snapshot.root
            / "production"
            / "audio"
            / "voice_previews"
            / f"{profile.profile_id}_{datetime.now():%Y%m%d_%H%M%S}{suffix}"
        )
        spec = DubbingLineSpec(
            episode_number=1,
            shot_number=1,
            source_video=self.current_snapshot.root / "project.json",
            mode="dialogue",
            text=text,
            speaker=profile.name,
            voice_id=profile.edge_voice_id,
            engine=profile.engine,
            reference_audio=profile.reference_audio,
            reference_text=profile.reference_text,
            instruct_text=profile.default_instruction,
            fallback_to_edge=True,
            rate=profile.speech_rate,
            volume=profile.speech_volume,
            pitch=profile.speech_pitch,
        )
        self.set_activity("正在生成声音试听", "warn")

        def render() -> Path:
            if profile.engine == "cosyvoice":
                self.cosyvoice_service.ensure_online(config)
            return self.dubbing_service.synthesize_preview(
                spec,
                destination,
                external_synthesizers={
                    "cosyvoice": lambda item, audio, _subtitle: (
                        self.cosyvoice_service.synthesize(config, item, audio)
                    )
                }
                if profile.engine == "cosyvoice"
                else None,
            )

        def success(path: Path) -> None:
            self.set_activity("声音试听已生成", "good")
            self.append_log(f"声音库试听已生成：{path}")
            self.open_media_file(path)

        def failure(detail: str) -> None:
            self.set_activity("声音试听失败", "off")
            self.show_error("声音试听失败", detail)

        self._start_task(render, success, failure)

    def check_server(self) -> None:
        config = self.settings_page.connection()
        self._save_connection(config)
        self.settings_page.set_busy(True)
        self.set_activity("正在检测", "warn")
        self.append_log(f"检测 GPU 服务器 {config.host}:{config.port}")
        self._start_task(
            lambda: self.gpu_service.check_status(config),
            self._on_gpu_status,
            self._on_gpu_error,
        )

    def start_comfy(self) -> None:
        config = self.settings_page.connection()
        self._save_connection(config)
        self.settings_page.set_busy(True)
        self.set_activity("正在启动", "warn")
        self.append_log("正在启动远程 ComfyUI")
        self._start_task(
            lambda: self.gpu_service.start_comfy(config),
            self._on_gpu_status,
            self._on_gpu_error,
        )

    def install_identity_adapter(self) -> None:
        config = self.settings_page.connection()
        if not config.password:
            self.show_error(
                "缺少 GPU 密码",
                "请先在“连接与设置”填写 SSH 密码。",
            )
            return
        self._save_connection(config)
        self.settings_page.set_busy(True)
        self.set_activity("人脸身份参考安装中", "warn")
        self.append_log("正在通过 HF 镜像安装 SDXL IP-Adapter 人脸身份参考")
        self._start_progress_task(
            lambda report: self.gpu_service.install_identity_adapter(
                config,
                progress_callback=report,
            ),
            self._on_gpu_status,
            self._on_gpu_error,
            self.video_generation.set_progress,
        )

    def check_llm(self) -> None:
        self.settings_page.set_llm_busy(True)
        self.append_log(f"检测文本模型服务 {settings.llm_base_url}")
        self._start_task(
            self.llm_service.check_status,
            self._on_llm_status,
            self._on_llm_error,
        )

    def ensure_local_llm(self) -> None:
        status = self.llm_service.check_status(timeout=1)
        self.settings_page.set_llm_status(status)
        if status.online:
            self._on_llm_status(status)
        elif status.model_ready:
            self.start_local_llm()
        else:
            self.append_log(status.message)

    def start_local_llm(self) -> None:
        self.settings_page.set_llm_busy(True)
        self.set_activity("文本模型启动中", "warn")
        self.append_log("正在启动本地 Qwen3.5-9B 文本模型")
        self._start_task(
            self.llm_service.start,
            self._on_llm_status,
            self._on_llm_error,
        )

    def check_video_runtime(self) -> None:
        self._start_task(
            self.video_service.check_status,
            self._on_video_runtime_status,
            self._on_video_runtime_error,
        )

    def check_local_models(self) -> None:
        self.settings_page.set_local_models_busy(True)
        self._start_task(
            self.local_model_service.check_status,
            self._on_local_models_status,
            self._on_local_models_error,
        )

    def check_dubbing_runtime(self) -> None:
        self._start_task(
            self.dubbing_service.check_status,
            self._on_dubbing_runtime_status,
            self._on_dubbing_runtime_error,
        )

    def check_cosyvoice(self) -> None:
        config = self.settings_page.connection()
        if not config.password:
            self.show_error(
                "缺少 GPU 密码",
                "请先在“连接与设置”填写 SSH 密码。",
            )
            return
        self.settings_page.set_cosy_busy(True)
        self._start_task(
            lambda: self.cosyvoice_service.check_status(config),
            self._on_cosyvoice_status,
            self._on_cosyvoice_error,
        )

    def start_cosyvoice(self) -> None:
        config = self.settings_page.connection()
        if not config.password:
            self.show_error(
                "缺少 GPU 密码",
                "请先在“连接与设置”填写 SSH 密码。",
            )
            return
        self.settings_page.set_cosy_busy(True)
        self.set_activity("CosyVoice 启动中", "warn")
        self.append_log("正在启动 GPU 服务器上的 CosyVoice 3")
        self._start_task(
            lambda: self.cosyvoice_service.start(config),
            self._on_cosyvoice_status,
            self._on_cosyvoice_error,
        )

    def stop_cosyvoice(self) -> None:
        config = self.settings_page.connection()
        if not config.password:
            self.show_error(
                "缺少 GPU 密码",
                "请先在“连接与设置”填写 SSH 密码。",
            )
            return
        self.settings_page.set_cosy_busy(True)
        self.append_log("正在停止 CosyVoice 并释放 GPU 显存")
        self._start_task(
            lambda: self.cosyvoice_service.stop(config),
            self._on_cosyvoice_status,
            self._on_cosyvoice_error,
        )

    def deploy_cosyvoice(self) -> None:
        config = self.settings_page.connection()
        if not config.password:
            self.show_error(
                "缺少 GPU 密码",
                "请先在“连接与设置”填写 SSH 密码。",
            )
            return
        self.settings_page.set_cosy_busy(True)
        self.set_activity("CosyVoice 部署中", "warn")
        self.append_log("正在部署 CosyVoice 3；首次安装需要下载约 5GiB 模型")
        self._start_progress_task(
            lambda report: self.cosyvoice_service.deploy(
                config,
                progress_callback=report,
            ),
            self._on_cosyvoice_status,
            self._on_cosyvoice_error,
            self.video_generation.set_progress,
        )

    def check_latentsync(self) -> None:
        config = self.settings_page.connection()
        if not config.password:
            self.show_error(
                "缺少 GPU 密码",
                "请先在“连接与设置”填写 SSH 密码。",
            )
            return
        self.settings_page.set_latentsync_busy(True)
        self._start_task(
            lambda: self.latentsync_service.check_status(config),
            self._on_latentsync_status,
            self._on_latentsync_error,
        )

    def deploy_latentsync(self) -> None:
        config = self.settings_page.connection()
        if not config.password:
            self.show_error(
                "缺少 GPU 密码",
                "请先在“连接与设置”填写 SSH 密码。",
            )
            return
        self.settings_page.set_latentsync_busy(True)
        self.set_activity("LatentSync 部署中", "warn")
        self.append_log(
            "正在部署 LatentSync 1.6；使用 HF 镜像下载约 5GB 主权重"
        )
        self._start_progress_task(
            lambda report: self.latentsync_service.deploy(
                config,
                progress_callback=report,
            ),
            self._on_latentsync_status,
            self._on_latentsync_error,
            self.video_generation.set_progress,
        )

    def deploy_minimax_h3(self) -> None:
        config = self.settings_page.connection()
        if not config.password:
            self.show_error(
                "缺少 GPU 密码",
                "请先在“连接与设置”填写 SSH 密码。",
            )
            return
        self._save_connection(config)
        self.settings_page.set_h3_busy(True)
        self.set_activity("MiniMax H3 部署中", "warn")
        self.append_log(
            "正在更新 ComfyUI 并通过 HF 镜像断点续传 H3 FL2VA，约 40GiB"
        )
        self._start_progress_task(
            lambda report: self.gpu_service.install_minimax_h3(
                config,
                progress_callback=report,
            ),
            self._on_gpu_status,
            self._on_gpu_error,
            self.video_generation.set_progress,
        )

    def deploy_wan22_flf2v(self) -> None:
        config = self.settings_page.connection()
        if not config.password:
            self.show_error(
                "缺少 GPU 密码",
                "请先在“连接与设置”填写 SSH 密码。",
            )
            return
        self._save_connection(config)
        self.settings_page.set_flf_busy(True)
        self.set_activity("Wan FLF2V 部署中", "warn")
        self.append_log(
            "正在通过 HF 镜像断点续传 Wan2.2 FLF2V 14B，约 28.9GB；"
            "保留 H3，不删除不确定模型"
        )
        self._start_progress_task(
            lambda report: self.gpu_service.install_wan22_flf2v(
                config,
                progress_callback=report,
            ),
            self._on_gpu_status,
            self._on_gpu_error,
            self.video_generation.set_progress,
        )

    def deploy_flux_kontext(self) -> None:
        config = self.settings_page.connection()
        if not config.password:
            self.show_error(
                "缺少 GPU 密码",
                "请先在“连接与设置”填写 SSH 密码。",
            )
            return
        self._save_connection(config)
        self.settings_page.set_kontext_busy(True)
        self.set_activity("FLUX.1 Kontext 部署中", "warn")
        self.append_log(
            "正在通过 HF 镜像断点续传 FLUX.1 Kontext；它将专门用于生成同一人物、同一场景的动作尾帧。"
        )
        self._start_progress_task(
            lambda report: self.gpu_service.install_flux_kontext(
                config,
                progress_callback=report,
            ),
            self._on_gpu_status,
            self._on_gpu_error,
            self.video_generation.set_progress,
        )

    def generate_character(
        self,
        character: str,
        prompt: str,
        style: str,
        model_ids: list[str],
        layout_preset: str,
        count: int,
        seed: int,
    ) -> None:
        if not self.current_project:
            return
        config = self.settings_page.connection()
        use_remote = bool(config.password)
        self.save_character_prompt(character, prompt, style, layout_preset)
        episode_path = self.project_service.episode_path(self.current_project)
        run_name = f"{character}_{time.strftime('%Y%m%d_%H%M%S')}"
        local_output = self.project_service.local_image_output_dir(
            self.current_project,
            run_name,
        )
        self.characters.set_generating(True)
        self.set_activity(
            "GPU 服务器生成中" if use_remote else "本机生成中",
            "warn",
        )
        model_names = "、".join(
            IMAGE_MODEL_PRESETS[model_id].label
            for model_id in model_ids
            if model_id in IMAGE_MODEL_PRESETS
        )
        total = count * len(model_ids)
        self.append_log(
            f"开始生成 {character}：{model_names}，每模型 {count} 张，"
            f"共 {total} 张，{character_layout_label(layout_preset)}，"
            f"风格={style}，seed={seed}"
        )

        def render_character(report):
            if use_remote:
                self._release_cosyvoice_for_gpu(config, report)
                return self.gpu_service.generate_character(
                    config,
                    project_slug=self.current_project,
                    episode_path=episode_path,
                    character=character,
                    model_ids=model_ids,
                    layout_preset=layout_preset,
                    count=count,
                    seed=seed,
                    local_output_dir=local_output,
                    prompt=prompt,
                    style_prompt=style_prompt(style),
                    progress_callback=report,
                )
            return self.local_comfy_service.generate_character(
                episode_path=episode_path,
                character=character,
                model_ids=model_ids,
                layout_preset=layout_preset,
                count=count,
                seed=seed,
                local_output_dir=local_output,
                prompt=prompt,
                style_prompt=style_prompt(style),
                progress_callback=report,
            )

        self._start_progress_task(
            render_character,
            self._on_generation_success,
            self._on_generation_error,
            self.characters.set_generation_progress,
        )

    def _on_generation_success(self, result: GenerationResult) -> None:
        self.characters.set_generating(False)
        self.characters.finish_generation(
            result.elapsed_seconds,
            f"已生成并下载 {len(result.images)} 张",
        )
        self.set_activity("生成完成", "good")
        self.append_log(
            f"生成完成：{len(result.images)} 张，用时 "
            f"{result.elapsed_seconds:.1f} 秒，已下载到 {result.local_dir}"
        )
        self.refresh_project()

    def revise_character_image(
        self,
        character: str,
        source_image: Path,
        payload: dict[str, Any],
    ) -> None:
        if not self.current_project or not self.current_snapshot:
            return
        config = self.settings_page.connection()
        if not config.password:
            self.show_error(
                "需要 GPU 服务器",
                "基于当前图修改需要 FLUX.1 Kontext。请先在连接与设置中填写 SSH 密码并检测服务器。",
            )
            return
        self._save_connection(config)
        prompt = str(payload.get("prompt") or "")
        self.save_character_prompt(
            character,
            prompt,
            self.characters.style.currentText(),
            str(self.characters.layout_preset.currentData()),
        )
        output_dir = (
            self.current_snapshot.root
            / "outputs"
            / "image_revisions"
            / f"revision_{datetime.now():%Y%m%d_%H%M%S}_{uuid4().hex[:6]}"
        )
        self.characters.set_generating(True)
        self.set_activity("正在修改角色图片", "warn")
        self.append_log(
            f"开始修改 {character} 的图片：{payload.get('issue', '')}；"
            f"保留模式={payload.get('preservation', 'balanced')}"
        )

        def task(report):
            self._release_cosyvoice_for_gpu(config, report)
            return self.gpu_service.revise_image(
                config,
                source_image=source_image,
                local_output_dir=output_dir,
                prompt=prompt,
                issue=str(payload.get("issue") or ""),
                negative_prompt=str(payload.get("negative_prompt") or ""),
                preservation=str(payload.get("preservation") or "balanced"),
                candidate_count=int(payload.get("candidate_count") or 2),
                seed=int(payload.get("seed") or 1),
                width=int(payload.get("width") or 832),
                height=int(payload.get("height") or 480),
                context_type="character",
                context_id=character,
                progress_callback=report,
            )

        def success(result: GenerationResult) -> None:
            self.characters.set_generating(False)
            self.characters.finish_generation(
                result.elapsed_seconds,
                f"已生成 {len(result.images)} 个修改候选",
            )
            dialog = ImageRevisionResultDialog(result.images, parent=self)
            dialog.exec()
            if dialog.selected_path:
                self.select_character_image(character, dialog.selected_path)
            else:
                self.refresh_project()
            self.set_activity("角色图片修改完成", "good")
            self.append_log(
                f"{character} 图片修改完成：{len(result.images)} 个候选，"
                f"用时 {result.elapsed_seconds:.1f} 秒"
            )

        def failure(detail: str) -> None:
            self.characters.set_generating(False)
            self._on_gpu_error(detail)

        self._start_progress_task(
            task,
            success,
            failure,
            self.characters.set_generation_progress,
        )

    def revise_shot_image(
        self,
        episode_number: int,
        shot_number: int,
        source_image: Path,
        payload: dict[str, Any],
    ) -> None:
        if not self.current_project or not self.current_snapshot:
            return
        config = self.settings_page.connection()
        if not config.password:
            self.show_error(
                "需要 GPU 服务器",
                "基于当前图修改需要 FLUX.1 Kontext。请先在连接与设置中填写 SSH 密码并检测服务器。",
            )
            return
        self._save_connection(config)
        prompt = str(payload.get("prompt") or "")
        self.save_shot_prompt(
            episode_number,
            shot_number,
            prompt,
            self.storyboard.style.currentText(),
        )
        try:
            archived_source = self.project_service.archive_shot_source_candidate(
                self.current_project,
                episode_number,
                shot_number,
                source_image,
            )
        except Exception as exc:
            self.show_error("原图归档失败", str(exc))
            return
        output_dir = (
            self.current_snapshot.root
            / "production"
            / "shots"
            / f"episode_{episode_number:03d}"
            / "revisions"
            / f"shot_{shot_number:03d}_{datetime.now():%Y%m%d_%H%M%S}_{uuid4().hex[:6]}"
        )
        self.storyboard.set_image_generation(True, f"正在修改镜头 {shot_number:02d}")
        self.set_activity("正在修改分镜图片", "warn")
        self.append_log(
            f"开始修改镜头 {shot_number:02d}：{payload.get('issue', '')}；"
            f"保留模式={payload.get('preservation', 'balanced')}；"
            f"修改前版本={archived_source.name}"
        )

        def task(report):
            self._release_cosyvoice_for_gpu(config, report)
            return self.gpu_service.revise_image(
                config,
                source_image=source_image,
                local_output_dir=output_dir,
                prompt=prompt,
                issue=str(payload.get("issue") or ""),
                negative_prompt=str(payload.get("negative_prompt") or ""),
                preservation=str(payload.get("preservation") or "balanced"),
                candidate_count=int(payload.get("candidate_count") or 2),
                seed=int(payload.get("seed") or 1),
                width=int(payload.get("width") or 832),
                height=int(payload.get("height") or 480),
                context_type="shot",
                context_id=str(shot_number),
                progress_callback=report,
            )

        def success(result: GenerationResult) -> None:
            records = {
                str(item.get("file") or ""): item
                for item in (result.manifest.get("images") or [])
                if isinstance(item, dict)
            }
            dialog = ImageRevisionResultDialog(result.images, parent=self)
            dialog.exec()
            selected = dialog.selected_path.resolve() if dialog.selected_path else None
            manifest_path = result.local_dir / "manifest.json"
            for image in result.images:
                self.project_service.save_shot_image_result(
                    self.current_project,
                    episode_number,
                    shot_number,
                    image,
                    manifest_path,
                    records.get(image.name, {}),
                    select=bool(selected and image.resolve() == selected),
                )
            self.storyboard.set_image_generation(False, "图片修改完成")
            self.refresh_project()
            self.set_activity("分镜图片修改完成", "good")
            self.append_log(
                f"镜头 {shot_number:02d} 图片修改完成：{len(result.images)} 个候选，"
                f"用时 {result.elapsed_seconds:.1f} 秒；"
                + ("已替换当前首帧" if selected else "候选已保存，未替换当前首帧")
            )

        def failure(detail: str) -> None:
            self.storyboard.set_image_generation(False, "图片修改失败")
            self._on_gpu_error(detail)

        self._start_progress_task(
            task,
            success,
            failure,
            self.storyboard.set_revision_progress,
        )

    def save_character_prompt(
        self,
        character: str,
        prompt: str,
        style: str,
        generation_preset: str,
    ) -> None:
        if not self.current_project or not self.episodes:
            return
        try:
            self.project_service.save_character_prompt(
                self.current_project,
                self.episodes[0].number,
                character,
                prompt,
                style,
                generation_preset,
            )
        except Exception as exc:
            self.show_error("角色提示词保存失败", str(exc))
            return
        self.set_activity("提示词已保存", "good")
        self.append_log(
            f"已保存 {character} 的提示词、风格和构图："
            f"{style} / {character_layout_label(generation_preset)}"
        )

    def save_shot_prompt(
        self,
        episode_number: int,
        shot_number: int,
        prompt: str,
        style: str,
    ) -> None:
        if not self.current_project:
            return
        try:
            self.project_service.save_shot_prompt(
                self.current_project,
                episode_number,
                shot_number,
                prompt,
                style,
            )
        except Exception as exc:
            self.show_error("镜头提示词保存失败", str(exc))
            return
        self.storyboard.set_saved()
        self.set_activity("镜头提示词已保存", "good")
        self.append_log(f"已保存镜头 {shot_number:02d} 的提示词：{style}")

    def set_shot_image_qc(
        self,
        episode_number: int,
        shot_number: int,
        status: str,
        note: str,
    ) -> None:
        if not self.current_project:
            return
        try:
            self.project_service.set_shot_image_qc(
                self.current_project,
                episode_number,
                shot_number,
                status,
                note,
            )
        except Exception as exc:
            self.show_error("首帧质检保存失败", str(exc))
            return
        label = "已通过" if status == "approved" else "已驳回"
        self.set_activity(f"首帧{label}", "good" if status == "approved" else "warn")
        detail = f"：{note}" if note else ""
        self.append_log(f"镜头 {shot_number:02d} 首帧{label}{detail}")
        self.refresh_project()
        self.storyboard.select_next_qc(shot_number)

    def generate_missing_shot_images(self, episode_number: int) -> None:
        if not self.current_project:
            return
        try:
            self.project_service.prepare_shot_automation(
                self.current_project,
                episode_numbers={episode_number},
            )
            self.refresh_project()
        except Exception as exc:
            self.show_error("分镜自动补全失败", str(exc))
            return
        self._begin_missing_shot_generation(episode_number=episode_number)

    def regenerate_continuous_shot_images(self, episode_number: int) -> None:
        """Rebuild continuity links and render the complete episode in sequence."""

        if not self.current_project:
            return
        try:
            stats = self.project_service.prepare_shot_automation(
                self.current_project,
                episode_numbers={episode_number},
                force_continuity=True,
            )
            self.refresh_project()
        except Exception as exc:
            self.show_error("连续分镜规划失败", str(exc))
            return
        self.append_log(
            f"第 {episode_number} 集连续性已重建："
            f"{stats['continuity_reference_links']} 个镜头承接关系；"
            "旧首帧候选将保留"
        )
        self._begin_missing_shot_generation(
            episode_number=episode_number,
            force_regenerate=True,
        )

    def regenerate_rejected_shot_images(self, episode_number: int) -> None:
        """Render only keyframes explicitly rejected during visual review."""

        episode = next(
            (
                item
                for item in self.episodes
                if item.number == episode_number
            ),
            None,
        )
        rejected = {
            shot.number
            for shot in (episode.shots if episode else [])
            if shot.image_qc_status == "rejected"
        }
        if not rejected:
            self.append_log("本集没有已驳回的首帧")
            return
        self.append_log(
            "准备重做已驳回首帧："
            + "、".join(f"{number:02d}" for number in sorted(rejected))
        )
        self._begin_missing_shot_generation(
            episode_number=episode_number,
            target_shot_numbers=rejected,
        )

    def _begin_missing_shot_generation(
        self,
        *,
        episode_number: int = 0,
        process_result: dict[str, Any] | None = None,
        reprocess: bool = False,
        force_regenerate: bool = False,
        target_shot_numbers: set[int] | None = None,
    ) -> bool:
        if (
            not self.current_project
            or not self.current_snapshot
            or not self.episodes
        ):
            return False
        requests = [
            (
                episode,
                [
                    shot.number
                    for shot in episode.shots
                    if (
                        shot.number in target_shot_numbers
                        if target_shot_numbers is not None
                        else force_regenerate or not shot.source_image
                    )
                ],
            )
            for episode in self.episodes
            if not episode_number or episode.number == episode_number
        ]
        requests = [
            (episode, shot_numbers)
            for episode, shot_numbers in requests
            if shot_numbers
        ]
        if not requests:
            self.append_log("所有分镜均已有首帧，无需重复生成")
            if process_result is not None:
                self._finish_novel_processing(
                    process_result,
                    reprocess=reprocess,
                    image_count=0,
                )
                return True
            return False

        config = self.settings_page.connection()
        self._save_connection(config)
        if not config.password:
            detail = "未填写 GPU 服务器 SSH 密码，分镜和提示词已保存，首帧等待补全"
            self.append_log(detail)
            if process_result is not None:
                self._finish_novel_processing(
                    process_result,
                    reprocess=reprocess,
                    image_error=detail,
                )
                return True
            self.show_error("无法自动生成分镜画面", detail)
            return False

        project_slug = self.current_project
        project_root = self.current_snapshot.root
        run_stamp = time.strftime("%Y%m%d_%H%M%S")
        self.project_combo.setDisabled(True)
        self.new_project_button.setDisabled(True)
        self.storyboard.set_image_generation(True, "正在连接 GPU")
        self.set_activity("自动生成分镜画面", "warn")
        if process_result is not None:
            self.novel_import.set_progress(82, "分镜完成，正在自动生成缺失首帧")
        operation = (
            "驳回镜头重绘"
            if target_shot_numbers is not None
            else "连续重绘"
            if force_regenerate
            else "自动补全"
        )
        self.append_log(
            f"开始{operation} {sum(len(numbers) for _, numbers in requests)} "
            "个镜头首帧"
        )

        def task(report):
            self._release_cosyvoice_for_gpu(config, report)
            status = self.gpu_service.start_comfy(config)
            if not status.ssh_online:
                raise RuntimeError(status.message or "GPU 服务器未连接")
            if not status.kontext_runtime_ready:
                raise RuntimeError(
                    "FLUX.1 Kontext 尚未就绪；请先在“连接与设置”中安装/修复 Kontext。"
                )
            model_id = (
                DEFAULT_IMAGE_MODEL_ID
                if DEFAULT_IMAGE_MODEL_ID in status.available_model_ids
                else status.available_model_ids[0]
                if status.available_model_ids
                else DEFAULT_IMAGE_MODEL_ID
            )
            batches: list[tuple[int, GenerationResult]] = []
            for index, (episode, shot_numbers) in enumerate(requests):
                local_output = (
                    project_root
                    / "production"
                    / "shots"
                    / f"episode_{episode.number:03d}"
                    / (
                        "generated_continuity"
                        if force_regenerate
                        else "generated"
                    )
                    / run_stamp
                )

                def episode_progress(
                    percent: int,
                    message: str,
                    request_index: int = index,
                ) -> None:
                    overall = int(
                        (request_index + percent / 100) / len(requests) * 100
                    )
                    report(overall, message)

                result = self.gpu_service.generate_shot_images(
                    config,
                    project_slug=project_slug,
                    episode_path=episode.path,
                    shot_numbers=shot_numbers,
                    model_ids=[model_id],
                    local_output_dir=local_output,
                    style_prompt=style_prompt(DEFAULT_STYLE),
                    progress_callback=episode_progress,
                )
                batches.append((episode.number, result))
            return {"status": status, "batches": batches}

        if process_result is not None:
            def progress_handler(percent: int, message: str) -> None:
                self.novel_import.set_progress(
                    80 + int(percent * 0.2),
                    message,
                )
        else:
            def progress_handler(percent: int, message: str) -> None:
                self.storyboard.set_image_generation(
                    True,
                    f"{percent}% · {message}",
                )
        self._start_progress_task(
            task,
            lambda payload: self._on_shot_images_success(
                project_slug,
                payload,
                process_result=process_result,
                reprocess=reprocess,
            ),
            lambda detail: self._on_shot_images_error(
                detail,
                process_result=process_result,
                reprocess=reprocess,
            ),
            progress_handler,
        )
        return True

    def _on_shot_images_success(
        self,
        project_slug: str,
        payload: dict[str, Any],
        *,
        process_result: dict[str, Any] | None,
        reprocess: bool,
    ) -> None:
        status = payload.get("status")
        if isinstance(status, GpuStatus):
            self.last_gpu_status = status
            self._on_gpu_status(status)
        selected_shots: set[tuple[int, int]] = set()
        saved_count = 0
        elapsed = 0.0
        for episode_number, result in payload.get("batches") or []:
            elapsed += result.elapsed_seconds
            manifest_path = result.local_dir / "manifest.json"
            for record in result.manifest.get("images") or []:
                if not isinstance(record, dict):
                    continue
                shot_number = int(record.get("shot_number") or 0)
                shot_key = (int(episode_number), shot_number)
                if not shot_number or shot_key in selected_shots:
                    continue
                image_path = result.local_dir / str(record.get("file") or "")
                if not image_path.is_file() or not manifest_path.is_file():
                    continue
                self.project_service.save_shot_image_result(
                    project_slug,
                    int(episode_number),
                    shot_number,
                    image_path,
                    manifest_path,
                    record,
                    select=True,
                )
                selected_shots.add(shot_key)
                saved_count += 1
        self.project_combo.setDisabled(False)
        self.new_project_button.setDisabled(False)
        self.storyboard.set_image_generation(
            False,
            f"已自动回填 {saved_count} 个首帧",
        )
        self.set_activity("分镜画面已补全", "good")
        self.append_log(
            f"分镜首帧生成并回填完成：{saved_count} 个，用时 {elapsed:.1f} 秒"
        )
        self.refresh_project()
        if process_result is not None:
            self._finish_novel_processing(
                process_result,
                reprocess=reprocess,
                image_count=saved_count,
            )

    def _on_shot_images_error(
        self,
        detail: str,
        *,
        process_result: dict[str, Any] | None,
        reprocess: bool,
    ) -> None:
        self.project_combo.setDisabled(False)
        self.new_project_button.setDisabled(False)
        self.storyboard.set_image_generation(False, "首帧生成失败，等待重试")
        self.append_log(f"自动生成分镜首帧失败：{detail}")
        if process_result is not None:
            self._finish_novel_processing(
                process_result,
                reprocess=reprocess,
                image_error=detail,
            )
        else:
            self.set_activity("分镜首帧生成失败", "off")
            self.show_error("分镜首帧生成失败", detail)

    def _finish_novel_processing(
        self,
        result: dict[str, Any],
        *,
        reprocess: bool,
        image_count: int | None = None,
        image_error: str = "",
    ) -> None:
        self.novel_import.set_processing(False)
        self.project_combo.setDisabled(False)
        self.new_project_button.setDisabled(False)
        prefix = "重新处理完成" if reprocess else "完成"
        message = (
            f"{prefix}：{result['chapters']} 章、{result['episodes']} 集、"
            f"{result['shots']} 个镜头"
        )
        if image_count is not None:
            message += f"、自动回填 {image_count} 个首帧"
        elif image_error:
            message += "；分镜提示词已保存，但首帧自动生成失败"
        self.novel_import.finish_processing(message)
        self.set_activity(
            "自动处理完成" if not image_error else "首帧等待补全",
            "good" if not image_error else "warn",
        )
        self.append_log(message)
        if result.get("backup_dir"):
            self.append_log(f"原分镜已备份到：{result['backup_dir']}")
        if image_error:
            self.show_error("首帧自动生成未完成", image_error)
        self.refresh_project()

    def set_shot_source_image(
        self,
        episode_number: int,
        shot_number: int,
        source_path: Path,
    ) -> None:
        if not self.current_project:
            return
        try:
            destination = self.project_service.set_shot_source_image(
                self.current_project,
                episode_number,
                shot_number,
                source_path,
            )
        except Exception as exc:
            self.show_error("首帧保存失败", str(exc))
            return
        self.set_activity("首帧已设置", "good")
        self.append_log(
            f"已为第 {episode_number} 集镜头 {shot_number:02d} "
            f"设置首帧：{destination.name}"
        )
        self.refresh_project()

    def select_shot_image_candidate(
        self,
        episode_number: int,
        shot_number: int,
        source_path: Path,
    ) -> None:
        if not self.current_project:
            return
        try:
            destination = self.project_service.select_shot_image_candidate(
                self.current_project,
                episode_number,
                shot_number,
                source_path,
            )
        except Exception as exc:
            self.show_error("历史首帧切换失败", str(exc))
            return
        self.set_activity("已切换历史首帧", "good")
        self.append_log(
            f"第 {episode_number} 集镜头 {shot_number:02d} 已切换历史首帧："
            f"{source_path.name} → {destination.name}"
        )
        self.refresh_project()

    def set_shot_end_image(
        self,
        episode_number: int,
        shot_number: int,
        source_path: Path,
    ) -> None:
        if not self.current_project:
            return
        try:
            destination = self.project_service.set_shot_end_image(
                self.current_project,
                episode_number,
                shot_number,
                source_path,
            )
        except Exception as exc:
            self.show_error("结束帧保存失败", str(exc))
            return
        self.set_activity("结束帧已设置", "good")
        self.append_log(
            f"已为第 {episode_number} 集镜头 {shot_number:02d} "
            f"设置结束帧：{destination.name}"
        )
        self.refresh_project()

    def generate_end_frames_then_videos(
        self,
        episode_number: int,
        payloads: list[dict[str, Any]],
        width: int,
        height: int,
        fps: int,
    ) -> None:
        """Generate missing FLF end keyframes, bind them, then continue video."""

        if not self.current_project or not self.current_snapshot:
            return
        project_slug = self.current_project
        project_root = self.current_snapshot.root
        missing = [
            int(payload["shot_number"])
            for payload in payloads
            if not payload.get("end_image")
        ]
        if not missing:
            self.generate_shot_videos(
                episode_number,
                payloads,
                width,
                height,
                fps,
            )
            return
        config = self.settings_page.connection()
        self._save_connection(config)
        if not config.password:
            self.show_error(
                "缺少 GPU 密码",
                "请先在“连接设置”填写 SSH 密码并检测服务器。",
            )
            return
        episode_path = self.project_service.episode_path(
            project_slug,
            episode_number,
        )
        run_stamp = f"{datetime.now():%Y%m%d_%H%M%S}_{uuid4().hex[:6]}"
        output_dir = (
            project_root
            / "production"
            / "shots"
            / f"episode_{episode_number:03d}"
            / "generated_end_frames"
            / run_stamp
        )
        self.video_generation.set_generating(
            True,
            f"正在自动生成 {len(missing)} 个动作结束关键帧",
        )
        self.append_log(
            "FLF2V 自动准备结束帧："
            + "、".join(f"{number:02d}" for number in missing)
        )

        def task(report):
            self._release_cosyvoice_for_gpu(config, report)
            status = self.gpu_service.start_comfy(config)
            if not status.ssh_online:
                raise RuntimeError(status.message or "GPU 服务器未连接")
            if not status.available_model_ids:
                raise RuntimeError("GPU 服务器没有可用的生图模型")
            model_id = (
                DEFAULT_IMAGE_MODEL_ID
                if DEFAULT_IMAGE_MODEL_ID in status.available_model_ids
                else status.available_model_ids[0]
            )
            result = self.gpu_service.generate_shot_images(
                config,
                project_slug=project_slug,
                episode_path=episode_path,
                shot_numbers=missing,
                model_ids=[model_id],
                local_output_dir=output_dir,
                candidate_count=2,
                width=width,
                height=height,
                style_prompt=style_prompt(DEFAULT_STYLE),
                frame_role="end",
                progress_callback=report,
            )
            return result

        def success(result: GenerationResult) -> None:
            records = result.manifest.get("images") or []
            candidates_by_shot: dict[int, list[Path]] = {}
            model_by_candidate: dict[Path, str] = {}
            for record in records:
                if not isinstance(record, dict):
                    continue
                number = int(record.get("shot_number") or 0)
                candidate = result.local_dir / str(record.get("file") or "")
                if number in missing and candidate.is_file():
                    candidates_by_shot.setdefault(number, []).append(candidate)
                    model_by_candidate[candidate] = str(
                        record.get("model_id") or ""
                    )
            payload_by_shot = {
                int(payload["shot_number"]): payload for payload in payloads
            }
            selected_by_shot: dict[int, Path] = {}
            rejected_scores: dict[int, list[str]] = {}
            for number, candidates in candidates_by_shot.items():
                start = Path(payload_by_shot[number]["source_image"])
                scored = [
                    (
                        self.video_service.keyframe_continuity_score(start, path),
                        self.video_service.keyframe_layout_score(start, path),
                        path,
                    )
                    for path in candidates
                ]
                valid = [
                    item
                    for item in scored
                    if (
                        0.48 <= item[1] <= 0.94
                        and 0.18 <= item[0] <= 0.85
                        if model_by_candidate.get(item[2]) == "flux_kontext"
                        else 0.62 <= item[0] <= 0.985
                    )
                ]
                if valid:
                    selected_by_shot[number] = min(
                        valid,
                        key=lambda item: (
                            -item[1]
                            if model_by_candidate.get(item[2]) == "flux_kontext"
                            else -item[0]
                        ),
                    )[2]
                else:
                    rejected_scores[number] = [
                        f"detail={item[0]:.3f}, layout={item[1]:.3f}"
                        for item in scored
                    ]
            unresolved = [number for number in missing if number not in selected_by_shot]
            if unresolved:
                self.video_generation.fail_generation("结束关键帧回填不完整")
                self.show_error(
                    "结束关键帧连续性未通过",
                    "以下镜头的候选与首帧过度漂移或几乎完全冻结，已保留候选但不会自动绑定：\n"
                    + "\n".join(
                        f"镜头 {number:02d}：{rejected_scores.get(number, [])}"
                        for number in unresolved
                    ),
                )
                return
            bound: dict[int, Path] = {}
            for number, source in selected_by_shot.items():
                bound[number] = self.project_service.set_shot_end_image(
                    project_slug,
                    episode_number,
                    number,
                    source,
                )
            for payload in payloads:
                number = int(payload["shot_number"])
                if number in bound:
                    payload["end_image"] = bound[number]
            self.append_log(
                f"已自动生成并绑定 {len(bound)} 个结束关键帧，继续 FLF2V 视频任务"
            )
            self.refresh_project()
            self.video_generation.set_generating(False)
            QTimer.singleShot(
                0,
                lambda: self.generate_shot_videos(
                    episode_number,
                    payloads,
                    width,
                    height,
                    fps,
                ),
            )

        self._start_progress_task(
            task,
            success,
            lambda detail: self.video_generation.fail_generation(detail),
            lambda percent, message: self.video_generation.set_progress(
                percent,
                message,
            ),
        )

    def save_video_settings(
        self,
        episode_number: int,
        shot_number: int,
        payload: dict[str, Any],
    ) -> None:
        if not self.current_project:
            return
        try:
            video_payload = dict(payload)
            audio_payload = video_payload.pop("audio_generation", None)
            lip_sync_payload = video_payload.pop("lip_sync", None)
            self.project_service.save_video_settings(
                self.current_project,
                episode_number,
                shot_number,
                **video_payload,
            )
            if isinstance(audio_payload, dict):
                audio_payload = dict(audio_payload)
                audio_payload.pop("shot_number", None)
                audio_payload.pop("source_video", None)
                self.project_service.save_audio_settings(
                    self.current_project,
                    episode_number,
                    shot_number,
                    **audio_payload,
                )
            if isinstance(lip_sync_payload, dict):
                self.project_service.save_lip_sync_settings(
                    self.current_project,
                    episode_number,
                    shot_number,
                    **lip_sync_payload,
                )
        except Exception as exc:
            self.show_error("视频参数保存失败", str(exc))
            return
        self.set_activity("视频参数已保存", "good")
        self.append_log(
            f"已保存镜头 {shot_number:02d} 视频参数："
            f"{payload.get('engine_profile', 'comic_motion')} / "
            f"{payload.get('camera_movement', 'auto')} / "
            f"{float(payload.get('duration_seconds', 3.0)):.1f} 秒"
        )
        self.refresh_project()

    def generate_shot_videos(
        self,
        episode_number: int,
        payloads: list[dict[str, Any]],
        width: int,
        height: int,
        fps: int,
    ) -> None:
        if not self.current_project or not self.current_snapshot:
            return
        project_slug = self.current_project
        project_root = self.current_snapshot.root
        fresh_episode = next(
            (
                episode
                for episode in self.project_service.load_episodes(project_slug)
                if episode.number == episode_number
            ),
            None,
        )
        approved_by_shot = {
            shot.number: shot.image_qc_status == "approved"
            for shot in (fresh_episode.shots if fresh_episode else [])
        }
        unapproved = sorted(
            int(payload.get("shot_number") or 0)
            for payload in payloads
            if not approved_by_shot.get(
                int(payload.get("shot_number") or 0),
                False,
            )
        )
        if unapproved:
            numbers = "、".join(f"{number:02d}" for number in unapproved)
            self.show_error(
                "首帧尚未通过质检",
                f"镜头 {numbers} 的当前首帧未通过质检。"
                "请先在“分镜脚本”页面检查并通过，重新换图后需要再次审核。",
            )
            return
        specs: list[VideoRenderSpec] = []
        try:
            for payload in payloads:
                shot_number = int(payload["shot_number"])
                engine_profile = str(
                    payload.get("engine_profile") or "comic_motion"
                )
                subject_motion = str(payload.get("subject_motion") or "")
                environment_motion = str(
                    payload.get("environment_motion") or ""
                )
                continuity_constraints = str(
                    payload.get("continuity_constraints") or ""
                )
                negative_prompt = str(payload.get("negative_prompt") or "")
                end_frame_prompt = str(payload.get("end_frame_prompt") or "")
                motion_prompt = str(payload.get("motion_prompt") or "")
                native_audio_mode = str(
                    payload.get("native_audio_mode") or "ambience_sfx_music"
                )
                dialogue_prompt = str(payload.get("dialogue_prompt") or "")
                sound_effect_prompt = str(
                    payload.get("sound_effect_prompt") or ""
                )
                music_prompt = str(payload.get("music_prompt") or "")
                camera_movement = str(payload.get("camera_movement") or "auto")
                motion_strength = str(payload.get("motion_strength") or "low")
                screen_direction = str(payload.get("screen_direction") or "auto")
                transition_out = str(payload.get("transition_out") or "cut")
                transition_frames = int(payload.get("transition_frames") or 0)
                handle_frames = int(payload.get("handle_frames") or 0)
                candidate_count = int(payload.get("candidate_count") or 1)
                duration_seconds = float(payload.get("duration_seconds") or 3.0)
                self.project_service.save_video_settings(
                    project_slug,
                    episode_number,
                    shot_number,
                    engine_profile=engine_profile,
                    subject_motion=subject_motion,
                    environment_motion=environment_motion,
                    continuity_constraints=continuity_constraints,
                    negative_prompt=negative_prompt,
                    end_frame_prompt=end_frame_prompt,
                    native_audio_mode=native_audio_mode,
                    dialogue_prompt=dialogue_prompt,
                    sound_effect_prompt=sound_effect_prompt,
                    music_prompt=music_prompt,
                    motion_prompt=motion_prompt,
                    camera_movement=camera_movement,
                    motion_strength=motion_strength,
                    screen_direction=screen_direction,
                    transition_out=transition_out,
                    transition_frames=transition_frames,
                    handle_frames=handle_frames,
                    candidate_count=candidate_count,
                    duration_seconds=duration_seconds,
                )
                specs.append(
                    VideoRenderSpec(
                        episode_number=episode_number,
                        shot_number=shot_number,
                        source_image=Path(payload["source_image"]),
                        end_image=(
                            Path(payload["end_image"])
                            if payload.get("end_image")
                            else None
                        ),
                        scene_description=str(payload.get("scene_description") or ""),
                        subject_motion=subject_motion,
                        environment_motion=environment_motion,
                        continuity_constraints=continuity_constraints,
                        negative_prompt=negative_prompt,
                        motion_prompt=motion_prompt,
                        native_audio_mode=native_audio_mode,
                        dialogue_prompt=dialogue_prompt,
                        sound_effect_prompt=sound_effect_prompt,
                        music_prompt=music_prompt,
                        camera_movement=camera_movement,
                        motion_strength=motion_strength,
                        screen_direction=screen_direction,
                        transition_out=transition_out,
                        transition_frames=transition_frames,
                        handle_frames=handle_frames,
                        candidate_count=candidate_count,
                        duration_seconds=duration_seconds,
                        fps=fps,
                        width=width,
                        height=height,
                        engine_profile=engine_profile,
                    )
                )
        except Exception as exc:
            self.show_error("视频任务参数错误", str(exc))
            return
        profiles = {spec.engine_profile for spec in specs}
        unsupported_profiles = profiles - {
            "comic_motion",
            "wan22_ti2v_5b",
            "wan22_flf2v",
            "minimax_h3_fl2va",
        }
        if unsupported_profiles:
            self.show_error(
                "视频引擎尚未开放",
                "以下引擎的任务字段已经就绪，但远程工作流尚未实现："
                + "、".join(sorted(unsupported_profiles)),
            )
            return
        remote_profiles = profiles - {"comic_motion"}
        config = None
        if remote_profiles:
            config = self.settings_page.connection()
            self._save_connection(config)
            if not config.password:
                self.show_error(
                    "缺少 GPU 密码",
                    "请先在“连接设置”填写 SSH 密码并检测服务器。",
                )
                return

        grouped = {
            profile: [spec for spec in specs if spec.engine_profile == profile]
            for profile in sorted(profiles)
        }

        def render_task(report):
            batches: list[VideoBatchResult] = []
            selected_shots: set[tuple[int, int]] = set()

            def persist_completed_clip(clip: VideoClipResult) -> None:
                shot_key = (clip.episode_number, clip.shot_number)
                self.project_service.save_shot_video_result(
                    project_slug,
                    clip.episode_number,
                    clip.shot_number,
                    clip.video_path,
                    clip.manifest_path,
                    select=shot_key not in selected_shots,
                )
                selected_shots.add(shot_key)

            if config is not None:
                self._release_cosyvoice_for_gpu(config, report)
            for group_index, (profile, group_specs) in enumerate(grouped.items()):
                def group_progress(
                    percent: int,
                    message: str,
                    index: int = group_index,
                ) -> None:
                    overall = int(
                        (index + percent / 100) / max(len(grouped), 1) * 100
                    )
                    report(overall, message)

                if profile == "comic_motion":
                    batch = self.video_service.generate_clips(
                        project_root,
                        group_specs,
                        progress_callback=group_progress,
                    )
                elif profile in {"wan22_ti2v_5b", "wan22_flf2v"}:
                    assert config is not None
                    batch = self.gpu_service.generate_wan_videos(
                        config,
                        project_root,
                        group_specs,
                        progress_callback=group_progress,
                    )
                else:
                    assert config is not None
                    batch = self.gpu_service.generate_h3_videos(
                        config,
                        project_root,
                        group_specs,
                        progress_callback=group_progress,
                        clip_callback=persist_completed_clip,
                    )
                batches.append(batch)
            return VideoBatchResult(
                clips=[clip for batch in batches for clip in batch.clips],
                job_id="+".join(batch.job_id for batch in batches if batch.job_id),
                elapsed_seconds=sum(batch.elapsed_seconds for batch in batches),
            )
        self.project_combo.setDisabled(True)
        self.new_project_button.setDisabled(True)
        self.video_generation.set_generating(
            True,
            f"正在准备 {len(specs)} 个镜头视频",
        )
        self.set_activity("视频生成中", "warn")
        self.append_log(
            f"开始生成第 {episode_number} 集 {len(specs)} 个镜头视频，"
            f"{width}×{height} / {fps}fps / {' + '.join(sorted(profiles))}"
        )
        self._start_progress_task(
            render_task,
            lambda result: self._on_video_generation_success(project_slug, result),
            self._on_video_generation_error,
            self.video_generation.set_progress,
        )

    def _release_cosyvoice_for_gpu(
        self,
        config: GpuConnection,
        report: Callable[[int, str], None],
    ) -> None:
        status = self.cosyvoice_service.check_status(config)
        if status.online:
            report(1, "正在停止 CosyVoice 并释放显存")
            self.cosyvoice_service.stop(config)

    def _on_video_generation_success(
        self,
        project_slug: str,
        result: VideoBatchResult,
    ) -> None:
        selected_shots: set[tuple[int, int]] = set()
        for clip in sorted(
            result.clips,
            key=lambda item: (
                item.episode_number,
                item.shot_number,
                item.candidate_index,
            ),
        ):
            shot_key = (clip.episode_number, clip.shot_number)
            self.project_service.save_shot_video_result(
                project_slug,
                clip.episode_number,
                clip.shot_number,
                clip.video_path,
                clip.manifest_path,
                select=shot_key not in selected_shots,
            )
            selected_shots.add(shot_key)
        self.project_combo.setDisabled(False)
        self.new_project_button.setDisabled(False)
        self.video_generation.finish_generation(
            f"已生成 {len(result.clips)} 个镜头视频，"
            f"用时 {result.elapsed_seconds:.1f} 秒"
        )
        self.set_activity("视频生成完成", "good")
        self.append_log(
            f"视频生成完成：{len(result.clips)} 个镜头，"
            f"任务 {result.job_id[:8]}，用时 {result.elapsed_seconds:.1f} 秒"
        )
        self.refresh_project()

    def _on_video_generation_error(self, detail: str) -> None:
        self.project_combo.setDisabled(False)
        self.new_project_button.setDisabled(False)
        self.video_generation.fail_generation("视频生成失败，请查看错误")
        self._on_gpu_error(detail)
        self.refresh_project()

    def compose_episode_preview(self, episode_number: int) -> None:
        if not self.current_project or not self.current_snapshot:
            return
        episode = next(
            (item for item in self.episodes if item.number == episode_number),
            None,
        )
        timeline = [
            EpisodeClipSpec(
                path=shot.video_path,
                shot_number=shot.number,
                duration_seconds=shot.duration_seconds,
                transition_out=shot.transition_out,
                transition_frames=shot.transition_frames,
            )
            for shot in (episode.shots if episode else [])
            if shot.video_path and shot.video_path.is_file()
        ]
        if not timeline:
            QMessageBox.information(
                self,
                "没有镜头视频",
                "请先至少生成一个镜头视频。",
            )
            return
        self.project_combo.setDisabled(True)
        self.new_project_button.setDisabled(True)
        self.video_generation.set_generating(
            True,
            f"正在合成 {len(timeline)} 个镜头",
        )
        self.set_activity("整集合成中", "warn")
        self._start_progress_task(
            lambda report: self.video_service.compose_episode(
                self.current_snapshot.root,
                episode_number,
                timeline,
                progress_callback=report,
            ),
            self._on_episode_compose_success,
            self._on_video_generation_error,
            self.video_generation.set_progress,
        )

    def _on_episode_compose_success(self, result: EpisodeComposeResult) -> None:
        self.project_combo.setDisabled(False)
        self.new_project_button.setDisabled(False)
        self.video_generation.finish_generation(
            f"整集预览已合成：{result.clip_count} 个镜头"
        )
        self.set_activity("整集预览完成", "good")
        self.append_log(
            f"第 {result.episode_number} 集无声预览已生成："
            f"{result.video_path.name}，用时 {result.elapsed_seconds:.1f} 秒"
        )
        self.refresh_project()
        self.open_media_file(result.video_path)

    def generate_episode_dubbing(
        self,
        episode_number: int,
        payloads: list[dict[str, Any]],
    ) -> None:
        if not self.current_project or not self.current_snapshot:
            return
        project_slug = self.current_project
        specs: list[DubbingLineSpec] = []
        try:
            for payload in payloads:
                shot_number = int(payload["shot_number"])
                mode = str(payload.get("mode") or "auto_narration")
                text = str(payload.get("text") or "").strip()
                self.project_service.save_audio_settings(
                    project_slug,
                    episode_number,
                    shot_number,
                    mode=mode,
                    speaker=str(payload.get("speaker") or "旁白"),
                    text=text,
                    engine=str(payload.get("engine") or "edge_tts"),
                    voice_id=str(
                        payload.get("voice_id")
                        or "zh-CN-YunyangNeural"
                    ),
                    reference_audio=str(
                        payload.get("reference_audio") or ""
                    ),
                    reference_text=str(
                        payload.get("reference_text") or ""
                    ),
                    instruct_text=str(
                        payload.get("instruct_text") or ""
                    ),
                    fallback_to_edge=bool(
                        payload.get("fallback_to_edge", True)
                    ),
                    rate=str(payload.get("rate") or "+5%"),
                    volume=str(payload.get("volume") or "+0%"),
                    pitch=str(payload.get("pitch") or "-5Hz"),
                    subtitle_enabled=bool(
                        payload.get("subtitle_enabled", True)
                    ),
                    preserve_source_audio=bool(
                        payload.get("preserve_source_audio", True)
                    ),
                    source_audio_gain_db=float(
                        payload.get("source_audio_gain_db", -6.0)
                    ),
                    ducking_gain_db=float(
                        payload.get("ducking_gain_db", -12.0)
                    ),
                )
                if not text and mode != "mute":
                    continue
                specs.append(
                    DubbingLineSpec(
                        episode_number=episode_number,
                        shot_number=shot_number,
                        source_video=Path(payload["source_video"]),
                        mode=mode,
                        text=text,
                        speaker=str(payload.get("speaker") or "旁白"),
                        voice_id=str(
                            payload.get("voice_id")
                            or "zh-CN-YunyangNeural"
                        ),
                        engine=str(payload.get("engine") or "edge_tts"),
                        reference_audio=(
                            Path(str(payload["reference_audio"]))
                            if payload.get("reference_audio")
                            else None
                        ),
                        reference_text=str(
                            payload.get("reference_text") or ""
                        ),
                        instruct_text=str(
                            payload.get("instruct_text") or ""
                        ),
                        fallback_to_edge=bool(
                            payload.get("fallback_to_edge", True)
                        ),
                        rate=str(payload.get("rate") or "+5%"),
                        volume=str(payload.get("volume") or "+0%"),
                        pitch=str(payload.get("pitch") or "-5Hz"),
                        subtitle_enabled=bool(
                            payload.get("subtitle_enabled", True)
                        ),
                        preserve_source_audio=bool(
                            payload.get("preserve_source_audio", True)
                        ),
                        source_audio_gain_db=float(
                            payload.get("source_audio_gain_db", -6.0)
                        ),
                        ducking_gain_db=float(
                            payload.get("ducking_gain_db", -12.0)
                        ),
                    )
                )
        except Exception as exc:
            self.show_error("配音参数错误", str(exc))
            return
        if not specs:
            self.show_error("没有配音文案", "请保留自动旁白或输入角色对白")
            return

        self.project_combo.setDisabled(True)
        self.new_project_button.setDisabled(True)
        self.video_generation.set_generating(
            True,
            f"正在生成 {len(specs)} 个镜头的配音",
        )
        self.set_activity("配音与字幕生成中", "warn")
        config = self.settings_page.connection()
        uses_cosyvoice = any(
            spec.engine == "cosyvoice" and spec.mode != "mute"
            for spec in specs
        )
        if uses_cosyvoice and not config.password:
            self.project_combo.setDisabled(False)
            self.new_project_button.setDisabled(False)
            self.video_generation.set_generating(False)
            self.show_error(
                "缺少 GPU 密码",
                "CosyVoice 运行在 3090 服务器，请先在“连接与设置”填写 SSH 密码。",
            )
            return

        def generate_dubbing(report):
            if uses_cosyvoice:
                report(1, "正在启动并检测 CosyVoice 3")
                self.cosyvoice_service.ensure_online(config)
                self._prepare_automatic_voice_references(
                    project_slug,
                    episode_number,
                    specs,
                    report,
                )
            return self.dubbing_service.dub_episode(
                self.current_snapshot.root,
                episode_number,
                specs,
                progress_callback=report,
                external_synthesizers={
                    "cosyvoice": lambda spec, audio, _subtitle: (
                        self.cosyvoice_service.synthesize(
                            config,
                            spec,
                            audio,
                        )
                    )
                }
                if uses_cosyvoice
                else None,
            )

        self._start_progress_task(
            generate_dubbing,
            lambda result: self._on_dubbing_success(project_slug, result),
            self._on_dubbing_error,
            self.video_generation.set_progress,
        )

    def optimize_episode_timing(
        self,
        episode_number: int,
        payloads: list[dict[str, Any]],
    ) -> None:
        """Persist editable speech settings and plan video length without a GPU."""

        if not self.current_project:
            return
        try:
            for payload in payloads:
                settings_payload = dict(payload)
                shot_number = int(settings_payload.pop("shot_number"))
                settings_payload.pop("source_video", None)
                self.project_service.save_audio_settings(
                    self.current_project,
                    episode_number,
                    shot_number,
                    **settings_payload,
                )
            summary = self.project_service.optimize_audio_timeline(
                self.current_project,
                episode_number,
            )
        except Exception as exc:
            self.show_error("配音时长规划失败", str(exc))
            return
        self.set_activity("配音时长已规划", "good")
        self.append_log(
            f"第 {episode_number} 集已按配音规划："
            f"{summary.shot_count} 个镜头，预计 "
            f"{summary.total_duration_seconds:.1f} 秒，"
            f"{summary.needs_split_shots} 个长对白建议拆镜"
        )
        self.refresh_project()
        QMessageBox.information(
            self,
            "配音时长规划完成",
            (
                f"预计整集 {summary.total_duration_seconds:.1f} 秒。\n"
                f"已调整 {summary.changed_shots} 个镜头；"
                f"{summary.needs_split_shots} 个长对白镜头已标记为建议拆分。\n\n"
                "这些时长会用于下一次视频生成。"
            ),
        )

    def _prepare_automatic_voice_references(
        self,
        project_slug: str,
        episode_number: int,
        specs: list[DubbingLineSpec],
        report: Callable[[int, str], None],
    ) -> None:
        if not self.current_snapshot:
            return
        root = self.current_snapshot.root
        reference_dir = root / "production" / "audio" / "voice_refs"
        reference_dir.mkdir(parents=True, exist_ok=True)
        cosy_specs = [
            spec
            for spec in specs
            if spec.engine == "cosyvoice" and spec.mode != "mute"
        ]
        speaker_bindings: dict[str, tuple[Path, str, str]] = {}
        for spec in cosy_specs:
            if not spec.reference_audio:
                continue
            reference = Path(spec.reference_audio)
            if not reference.is_absolute():
                reference = root / reference
            if not spec.reference_text.strip():
                raise ValueError(
                    f"{spec.speaker} 的参考音频缺少逐字对应的参考台词"
                )
            speaker_bindings[spec.speaker] = (
                reference.resolve(),
                spec.reference_text,
                spec.instruct_text,
            )
        for index, spec in enumerate(cosy_specs, start=1):
            binding = speaker_bindings.get(spec.speaker)
            if binding:
                reference, reference_text, instruct_text = binding
                spec.reference_audio = reference
                spec.reference_text = reference_text
                if not spec.instruct_text:
                    spec.instruct_text = instruct_text
            else:
                safe_speaker = re.sub(
                    r"[^A-Za-z0-9_\-\u3400-\u9fff]+",
                    "_",
                    spec.speaker.strip() or "speaker",
                ).strip("_")
                reference = (
                    reference_dir / f"auto_{safe_speaker or 'speaker'}.mp3"
                )
                if not reference.is_file():
                    report(
                        max(2, min(8, 1 + index)),
                        f"正在为 {spec.speaker} 创建基础参考音色",
                    )
                    self.dubbing_service.create_reference_seed(
                        reference,
                        voice_id=spec.voice_id,
                        text=AUTO_VOICE_REFERENCE_TEXT,
                    )
            spec.reference_audio = reference
            spec.reference_text = AUTO_VOICE_REFERENCE_TEXT
            speaker_bindings[spec.speaker] = (
                reference,
                spec.reference_text,
                spec.instruct_text,
            )
            self.project_service.save_audio_settings(
                project_slug,
                episode_number,
                spec.shot_number,
                mode=spec.mode,
                speaker=spec.speaker,
                text=spec.text,
                engine=spec.engine,
                voice_id=spec.voice_id,
                reference_audio=reference,
                reference_text=spec.reference_text,
                instruct_text=spec.instruct_text,
                fallback_to_edge=spec.fallback_to_edge,
                rate=spec.rate,
                volume=spec.volume,
                pitch=spec.pitch,
                subtitle_enabled=spec.subtitle_enabled,
            )

    def preview_shot_voice(
        self,
        episode_number: int,
        shot_number: int,
        payload: dict[str, Any],
    ) -> None:
        if not self.current_project or not self.current_snapshot:
            return
        try:
            spec = DubbingLineSpec(
                episode_number=episode_number,
                shot_number=shot_number,
                source_video=Path(
                    payload.get("source_video") or self.current_snapshot.root
                ),
                mode=str(payload.get("mode") or "dialogue"),
                text=str(payload.get("text") or "").strip(),
                speaker=str(payload.get("speaker") or "旁白"),
                voice_id=str(
                    payload.get("voice_id") or "zh-CN-YunyangNeural"
                ),
                engine=str(payload.get("engine") or "edge_tts"),
                reference_audio=(
                    Path(str(payload["reference_audio"]))
                    if payload.get("reference_audio")
                    else None
                ),
                reference_text=str(payload.get("reference_text") or ""),
                instruct_text=str(payload.get("instruct_text") or ""),
                fallback_to_edge=bool(
                    payload.get("fallback_to_edge", True)
                ),
                rate=str(payload.get("rate") or "+5%"),
                volume=str(payload.get("volume") or "+0%"),
                pitch=str(payload.get("pitch") or "-5Hz"),
                subtitle_enabled=bool(
                    payload.get("subtitle_enabled", True)
                ),
            )
        except Exception as exc:
            self.show_error("试听参数错误", str(exc))
            return
        config = self.settings_page.connection()
        if spec.engine == "cosyvoice" and not config.password:
            self.show_error(
                "缺少 GPU 密码",
                "CosyVoice 运行在 3090 服务器，请先填写 SSH 密码。",
            )
            return
        project_slug = self.current_project
        root = self.current_snapshot.root
        suffix = ".wav" if spec.engine == "cosyvoice" else ".mp3"
        destination = (
            root
            / "production"
            / "audio"
            / "previews"
            / (
                f"episode_{episode_number:03d}_shot_{shot_number:03d}_"
                f"{datetime.now():%Y%m%d_%H%M%S}{suffix}"
            )
        )
        self.video_generation.set_generating(True, "正在生成音色试听")

        def render_preview(report):
            if spec.engine == "cosyvoice":
                self.cosyvoice_service.ensure_online(config)
                self._prepare_automatic_voice_references(
                    project_slug,
                    episode_number,
                    [spec],
                    report,
                )
            report(20, "正在合成试听音频")
            return self.dubbing_service.synthesize_preview(
                spec,
                destination,
                external_synthesizers={
                    "cosyvoice": lambda item, audio, _subtitle: (
                        self.cosyvoice_service.synthesize(
                            config,
                            item,
                            audio,
                        )
                    )
                }
                if spec.engine == "cosyvoice"
                else None,
            )

        self._start_progress_task(
            render_preview,
            self._on_voice_preview_success,
            self._on_voice_preview_error,
            self.video_generation.set_progress,
        )

    def _on_voice_preview_success(self, path: Path) -> None:
        self.video_generation.finish_generation(f"试听已生成：{path.name}")
        self.set_activity("音色试听已完成", "good")
        self.append_log(f"音色试听已生成：{path}")
        self.refresh_project()
        self.open_media_file(path)

    def _on_voice_preview_error(self, detail: str) -> None:
        self.video_generation.fail_generation("音色试听失败")
        self.set_activity("音色试听失败", "off")
        self.show_error("音色试听失败", detail)

    def generate_shot_lip_sync(
        self,
        episode_number: int,
        shot_number: int,
        payload: dict[str, Any],
    ) -> None:
        if not self.current_project or not self.current_snapshot:
            return
        config = self.settings_page.connection()
        if not config.password:
            self.show_error(
                "缺少 GPU 密码",
                "LatentSync 运行在 3090 服务器，请先填写 SSH 密码。",
            )
            return
        lip_sync = payload.get("lip_sync") or {}
        if not isinstance(lip_sync, dict):
            lip_sync = {}
        try:
            spec = DubbingLineSpec(
                episode_number=episode_number,
                shot_number=shot_number,
                source_video=Path(str(payload["source_video"])),
                mode="dialogue",
                text=str(payload.get("text") or "").strip(),
                speaker=str(payload.get("speaker") or "").strip(),
                voice_id=str(
                    payload.get("voice_id") or "zh-CN-YunyangNeural"
                ),
                engine=str(payload.get("engine") or "edge_tts"),
                reference_audio=(
                    Path(str(payload["reference_audio"]))
                    if payload.get("reference_audio")
                    else None
                ),
                reference_text=str(payload.get("reference_text") or ""),
                instruct_text=str(payload.get("instruct_text") or ""),
                fallback_to_edge=bool(payload.get("fallback_to_edge", True)),
                rate=str(payload.get("rate") or "+5%"),
                volume=str(payload.get("volume") or "+0%"),
                pitch=str(payload.get("pitch") or "-5Hz"),
                subtitle_enabled=True,
            )
        except Exception as exc:
            self.show_error("口型任务参数错误", str(exc))
            return
        if not spec.text:
            self.show_error("对白为空", "请先填写当前人物的对白文案。")
            return
        if not spec.source_video.is_file():
            self.show_error("镜头视频不存在", str(spec.source_video))
            return

        project_slug = self.current_project
        project_root = self.current_snapshot.root
        self.project_service.save_audio_settings(
            project_slug,
            episode_number,
            shot_number,
            mode="dialogue",
            speaker=spec.speaker,
            text=spec.text,
            engine=spec.engine,
            voice_id=spec.voice_id,
            reference_audio=str(spec.reference_audio or ""),
            reference_text=spec.reference_text,
            instruct_text=spec.instruct_text,
            fallback_to_edge=spec.fallback_to_edge,
            rate=spec.rate,
            volume=spec.volume,
            pitch=spec.pitch,
            subtitle_enabled=True,
        )
        self.project_service.save_lip_sync_settings(
            project_slug,
            episode_number,
            shot_number,
            enabled=True,
            engine=str(lip_sync.get("engine") or "latentsync_1_6"),
            target_character=str(
                lip_sync.get("target_character") or spec.speaker
            ),
            mode=str(lip_sync.get("mode") or "auto_single_face"),
            inference_steps=int(lip_sync.get("inference_steps") or 20),
            guidance_scale=float(lip_sync.get("guidance_scale") or 1.5),
        )
        suffix = ".wav" if spec.engine == "cosyvoice" else ".mp3"
        audio_path = (
            project_root
            / "production"
            / "audio"
            / f"episode_{episode_number:03d}"
            / (
                f"shot_{shot_number:03d}_lipsync_input_"
                f"{datetime.now():%Y%m%d_%H%M%S}{suffix}"
            )
        )
        self.project_combo.setDisabled(True)
        self.new_project_button.setDisabled(True)
        self.video_generation.set_generating(
            True,
            f"正在生成镜头 {shot_number:02d} 的配音与口型",
        )
        self.set_activity("口型生成中", "warn")

        def render_lip_sync(report):
            report(2, "正在生成当前对白的最终配音")
            if spec.engine == "cosyvoice":
                self.cosyvoice_service.ensure_online(config)
                self._prepare_automatic_voice_references(
                    project_slug,
                    episode_number,
                    [spec],
                    report,
                )
            generated_audio = self.dubbing_service.synthesize_preview(
                spec,
                audio_path,
                external_synthesizers={
                    "cosyvoice": lambda item, audio, _subtitle: (
                        self.cosyvoice_service.synthesize(
                            config,
                            item,
                            audio,
                        )
                    )
                }
                if spec.engine == "cosyvoice"
                else None,
            )
            report(20, "配音已生成，正在执行 LatentSync 1.6")
            return self.latentsync_service.synchronize(
                config,
                project_root,
                episode_number=episode_number,
                shot_number=shot_number,
                source_video=spec.source_video,
                audio_path=generated_audio,
                inference_steps=int(lip_sync.get("inference_steps") or 20),
                guidance_scale=float(lip_sync.get("guidance_scale") or 1.5),
                target_character=str(
                    lip_sync.get("target_character") or spec.speaker
                ),
                face_selection_mode=str(
                    lip_sync.get("mode") or "auto_single_face"
                ),
                progress_callback=lambda percent, message: report(
                    20 + int(percent * 0.8),
                    message,
                ),
            )

        self._start_progress_task(
            render_lip_sync,
            lambda result: self._on_lip_sync_success(project_slug, result),
            lambda detail: self._on_lip_sync_failure(
                project_slug,
                episode_number,
                shot_number,
                detail,
            ),
            self.video_generation.set_progress,
        )

    def generate_episode_lip_sync(
        self,
        episode_number: int,
        regenerate_completed: bool = False,
    ) -> None:
        if not self.current_project or not self.current_snapshot:
            return
        config = self.settings_page.connection()
        if not config.password:
            self.show_error(
                "缺少 GPU 密码",
                "整集口型运行在 3090 服务器，请先填写 SSH 密码。",
            )
            return
        project_slug = self.current_project
        project_root = self.current_snapshot.root
        plan = self.lip_sync_batch_planner.plan(
            project_root,
            episode_number,
            regenerate_completed=regenerate_completed,
        )
        blocked_preview = "\n".join(
            f"镜头 {item.shot_number:02d}：{item.reason}"
            for item in plan.blocked[:8]
        )
        if len(plan.blocked) > 8:
            blocked_preview += f"\n……另有 {len(plan.blocked) - 8} 个受阻镜头"
        if not plan.ready:
            QMessageBox.information(
                self,
                "暂无可执行的口型镜头",
                f"{plan.summary()}\n\n{blocked_preview or '没有待处理镜头。'}",
            )
            return
        detail = (
            f"{plan.summary()}\n\n"
            "本次只处理校验通过的角色对白镜头；旁白、已完成和受阻镜头会跳过。"
        )
        if blocked_preview:
            detail += f"\n\n受阻原因：\n{blocked_preview}"
        answer = QMessageBox.question(
            self,
            "开始整集口型续跑",
            detail,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Yes,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return

        self.project_combo.setDisabled(True)
        self.new_project_button.setDisabled(True)
        self.video_generation.set_generating(
            True,
            f"正在准备第 {episode_number} 集批量口型",
        )
        self.set_activity("整集口型生成中", "warn")

        def render_batch(report):
            run = LipSyncBatchRunResult(
                episode_number=episode_number,
                skipped_completed=[item.shot_number for item in plan.completed],
                blocked_shots={
                    item.shot_number: item.reason for item in plan.blocked
                },
            )
            total = len(plan.ready)
            comfy_was_online = self.gpu_service.check_status(config).comfy_online
            try:
                for index, item in enumerate(plan.ready, start=1):
                    assert item.source_video is not None
                    start_percent = int((index - 1) * 100 / total)
                    span = max(1, int(100 / total))

                    def item_report(
                        percent: int,
                        message: str,
                        *,
                        _start: int = start_percent,
                        _span: int = span,
                        _shot: int = item.shot_number,
                        _index: int = index,
                    ) -> None:
                        overall = min(
                            99,
                            _start + int(_span * percent / 100),
                        )
                        report(
                            overall,
                            f"镜头 {_shot:02d}（{_index}/{total}）· {message}",
                        )

                    try:
                        spec = DubbingLineSpec(
                            episode_number=episode_number,
                            shot_number=item.shot_number,
                            source_video=item.source_video,
                            mode="dialogue",
                            text=item.text,
                            speaker=item.speaker,
                            voice_id=item.voice_id,
                            engine=item.tts_engine,
                            reference_audio=item.reference_audio,
                            reference_text=item.reference_text,
                            instruct_text=item.instruct_text,
                            fallback_to_edge=item.fallback_to_edge,
                            rate=item.rate,
                            volume=item.volume,
                            pitch=item.pitch,
                            subtitle_enabled=True,
                        )
                        suffix = ".wav" if spec.engine == "cosyvoice" else ".mp3"
                        audio_path = (
                            project_root
                            / "production"
                            / "audio"
                            / f"episode_{episode_number:03d}"
                            / (
                                f"shot_{item.shot_number:03d}_lipsync_batch_"
                                f"{datetime.now():%Y%m%d_%H%M%S}{suffix}"
                            )
                        )
                        item_report(2, "正在生成对白配音")
                        if spec.engine == "cosyvoice":
                            self.cosyvoice_service.ensure_online(config)
                            self._prepare_automatic_voice_references(
                                project_slug,
                                episode_number,
                                [spec],
                                item_report,
                            )
                        generated_audio = self.dubbing_service.synthesize_preview(
                            spec,
                            audio_path,
                            external_synthesizers={
                                "cosyvoice": lambda line, audio, _subtitle: (
                                    self.cosyvoice_service.synthesize(
                                        config,
                                        line,
                                        audio,
                                    )
                                )
                            }
                            if spec.engine == "cosyvoice"
                            else None,
                        )
                        result = self.latentsync_service.synchronize(
                            config,
                            project_root,
                            episode_number=episode_number,
                            shot_number=item.shot_number,
                            source_video=item.source_video,
                            audio_path=generated_audio,
                            inference_steps=item.inference_steps,
                            guidance_scale=item.guidance_scale,
                            target_character=item.target_character,
                            face_reference=item.face_reference,
                            face_selection_mode=item.face_selection_mode,
                            restore_comfy=False,
                            progress_callback=lambda percent, message: item_report(
                                15 + int(percent * 0.85),
                                message,
                            ),
                        )
                        self.project_service.save_lip_sync_result(
                            project_slug,
                            result.episode_number,
                            result.shot_number,
                            result.video_path,
                            result.audio_path,
                            result.source_video,
                            result.manifest_path,
                            elapsed_seconds=result.elapsed_seconds,
                            face_match_similarity=result.face_match_similarity,
                            select=True,
                        )
                        run.completed_shots.append(item.shot_number)
                    except Exception as exc:
                        detail = str(exc)
                        self.project_service.save_lip_sync_failure(
                            project_slug,
                            episode_number,
                            item.shot_number,
                            detail,
                        )
                        run.failed_shots[item.shot_number] = detail
            finally:
                if comfy_was_online:
                    try:
                        report(99, "正在恢复 ComfyUI")
                        self.gpu_service.start_comfy(config)
                    except Exception as exc:
                        run.failed_shots[0] = f"ComfyUI 自动恢复失败：{exc}"
            report(100, "整集口型批次已结束")
            return run

        self._start_progress_task(
            render_batch,
            self._on_episode_lip_sync_success,
            self._on_episode_lip_sync_error,
            self.video_generation.set_progress,
        )

    def _on_episode_lip_sync_success(self, result: LipSyncBatchRunResult) -> None:
        self.project_combo.setDisabled(False)
        self.new_project_button.setDisabled(False)
        completed = len(result.completed_shots)
        failed = len([shot for shot in result.failed_shots if shot > 0])
        self.video_generation.finish_generation(
            f"整集口型批次完成：成功 {completed}，失败 {failed}"
        )
        self.set_activity(
            "整集口型完成" if failed == 0 else "整集口型部分完成",
            "good" if failed == 0 else "warn",
        )
        self.append_log(
            f"第 {result.episode_number} 集口型续跑：成功 {completed}，"
            f"失败 {failed}，已完成跳过 {len(result.skipped_completed)}，"
            f"受阻跳过 {len(result.blocked_shots)}"
        )
        self.refresh_project()
        QTimer.singleShot(0, self.check_server)
        if result.failed_shots:
            failures = "\n".join(
                f"镜头 {shot:02d}：{detail}"
                if shot > 0
                else detail
                for shot, detail in list(result.failed_shots.items())[:8]
            )
            QMessageBox.warning(
                self,
                "整集口型部分失败",
                f"已成功 {completed} 个镜头。再次点击批量按钮会从失败处续跑。\n\n{failures}",
            )

    def _on_episode_lip_sync_error(self, detail: str) -> None:
        self.project_combo.setDisabled(False)
        self.new_project_button.setDisabled(False)
        self.video_generation.fail_generation("整集口型任务失败")
        self.set_activity("整集口型任务失败", "off")
        self.show_error("整集口型任务失败", detail)

    def _on_lip_sync_success(
        self,
        project_slug: str,
        result: LatentSyncResult,
    ) -> None:
        self.project_service.save_lip_sync_result(
            project_slug,
            result.episode_number,
            result.shot_number,
            result.video_path,
            result.audio_path,
            result.source_video,
            result.manifest_path,
            elapsed_seconds=result.elapsed_seconds,
            face_match_similarity=result.face_match_similarity,
            select=True,
        )
        self.project_combo.setDisabled(False)
        self.new_project_button.setDisabled(False)
        self.video_generation.finish_generation(
            f"镜头 {result.shot_number:02d} 口型已完成"
        )
        self.set_activity("口型生成完成", "good")
        self.append_log(
            f"镜头 {result.shot_number:02d} LatentSync 1.6 已完成："
            f"{result.video_path.name}，用时 {result.elapsed_seconds:.1f} 秒"
        )
        self.refresh_project()
        self.open_media_file(result.video_path)
        QTimer.singleShot(0, self.check_server)

    def _on_lip_sync_failure(
        self,
        project_slug: str,
        episode_number: int,
        shot_number: int,
        detail: str,
    ) -> None:
        self.project_service.save_lip_sync_failure(
            project_slug,
            episode_number,
            shot_number,
            detail,
        )
        self.project_combo.setDisabled(False)
        self.new_project_button.setDisabled(False)
        self.video_generation.fail_generation("口型生成失败，请查看错误")
        self.set_activity("口型生成失败", "off")
        self.append_log(
            f"镜头 {shot_number:02d} 口型生成失败："
            f"{detail.splitlines()[0] if detail else '未知错误'}"
        )
        self.refresh_project()
        self.show_error("LatentSync 口型生成失败", detail)

    def _on_dubbing_success(
        self,
        project_slug: str,
        result: DubbingComposeResult,
    ) -> None:
        for line in result.lines:
            self.project_service.save_shot_audio_result(
                project_slug,
                line.episode_number,
                line.shot_number,
                line.audio_path,
                line.subtitle_path,
                line.manifest_path,
                audio_duration_seconds=line.audio_duration_seconds,
                timeline_duration_seconds=line.timeline_duration_seconds,
            )
        self.project_service.save_episode_dubbing_result(
            project_slug,
            result.episode_number,
            result.video_path,
            result.subtitle_path,
            result.manifest_path,
        )
        self.project_combo.setDisabled(False)
        self.new_project_button.setDisabled(False)
        self.video_generation.finish_generation(
            f"带声成片已完成：{len(result.lines)} 个镜头"
        )
        self.set_activity("配音成片已完成", "good")
        self.append_log(
            f"第 {result.episode_number} 集配音、字幕和带声成片已生成："
            f"{result.video_path.name}，用时 {result.elapsed_seconds:.1f} 秒"
        )
        self.refresh_project()
        self.open_media_file(result.video_path)

    def _on_dubbing_error(self, detail: str) -> None:
        self.project_combo.setDisabled(False)
        self.new_project_button.setDisabled(False)
        self.video_generation.fail_generation("配音生成失败，请查看错误")
        self.set_activity("配音生成失败", "off")
        self.append_log(f"配音生成失败：{detail.splitlines()[0] if detail else '未知错误'}")
        self.show_error("配音生成失败", detail)

    def open_media_file(self, path: Path) -> None:
        candidate = Path(path)
        if not candidate.is_file():
            self.show_error("媒体文件不存在", str(candidate))
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(candidate.resolve())))

    def _on_video_runtime_status(self, status: VideoRuntimeStatus) -> None:
        self.video_generation.set_runtime_status(status)
        self.append_log(status.message)

    def _on_video_runtime_error(self, detail: str) -> None:
        self.video_generation.set_runtime_status(
            VideoRuntimeStatus(False, message=detail)
        )
        self.append_log(detail)

    def _on_dubbing_runtime_status(self, status: DubbingRuntimeStatus) -> None:
        self.video_generation.set_dubbing_status(status)
        self.append_log(status.message)

    def _on_dubbing_runtime_error(self, detail: str) -> None:
        self.video_generation.set_dubbing_status(
            DubbingRuntimeStatus(False, message=detail)
        )
        self.append_log(detail)

    def _on_local_models_status(
        self,
        inventory: LocalRuntimeInventory,
    ) -> None:
        self.settings_page.set_local_models_status(inventory)
        self.append_log(
            f"本机模型：{inventory.message}；"
            f"{inventory.gpu_name} / {inventory.vram_gb:g}GB"
        )

    def _on_local_models_error(self, detail: str) -> None:
        self.settings_page.set_local_models_busy(False)
        self.append_log(f"本机模型检测失败：{detail.splitlines()[0]}")

    def _on_cosyvoice_status(self, status: CosyVoiceStatus) -> None:
        self.settings_page.set_cosy_busy(False)
        self.settings_page.set_cosy_status(status)
        self.video_generation.set_cosyvoice_status(status)
        if status.online:
            self.set_activity("CosyVoice 已就绪", "good")
        self.append_log(status.message)

    def _on_cosyvoice_error(self, detail: str) -> None:
        self.settings_page.set_cosy_busy(False)
        status = CosyVoiceStatus(message=detail.splitlines()[0] if detail else "")
        self.settings_page.set_cosy_status(status)
        self.video_generation.set_cosyvoice_status(status)
        self.append_log(f"CosyVoice：{status.message}")
        self.show_error("CosyVoice 操作失败", detail)

    def _on_latentsync_status(self, status: LatentSyncStatus) -> None:
        self.settings_page.set_latentsync_busy(False)
        self.settings_page.set_latentsync_status(status)
        self.video_generation.set_latentsync_status(status)
        if status.callable:
            self.set_activity("LatentSync 已就绪", "good")
        self.append_log(status.message)

    def _on_latentsync_error(self, detail: str) -> None:
        self.settings_page.set_latentsync_busy(False)
        status = LatentSyncStatus(
            message=detail.splitlines()[0] if detail else "检测失败"
        )
        self.settings_page.set_latentsync_status(status)
        self.video_generation.set_latentsync_status(status)
        self.append_log(f"LatentSync：{status.message}")
        self.show_error("LatentSync 操作失败", detail)

    def process_novel(self, source_path: Path, analysis_limit: int) -> None:
        if not self.current_project:
            return
        if not self._require_llm_ready():
            return
        project_slug = self.current_project
        self.novel_import.set_processing(True, "import")
        self.project_combo.setDisabled(True)
        self.new_project_button.setDisabled(True)
        self.set_activity("小说处理中", "warn")
        self.append_log(f"开始导入 {source_path.name}，自动分析 {analysis_limit or '全部'} 章")
        self._start_progress_task(
            lambda report: self.project_service.process_novel(
                project_slug,
                source_path,
                analysis_limit=analysis_limit,
                progress_callback=report,
            ),
            self._on_novel_process_success,
            self._on_novel_process_error,
            self.novel_import.set_progress,
        )

    def _on_novel_process_success(self, result: dict[str, Any]) -> None:
        self.refresh_project()
        if self.novel_import.auto_generate_images.isChecked():
            if self._begin_missing_shot_generation(
                process_result=result,
                reprocess=False,
            ):
                return
        self._finish_novel_processing(result, reprocess=False)

    def _on_novel_process_error(self, detail: str) -> None:
        detail = self._friendly_processing_error(detail)
        self.novel_import.set_processing(False)
        self.project_combo.setDisabled(False)
        self.new_project_button.setDisabled(False)
        self.novel_import.set_progress(
            self.novel_import.progress.value(),
            "自动处理失败，请查看详细错误",
        )
        self._on_gpu_error(detail)

    def reprocess_novel(self, analysis_limit: int) -> None:
        if not self.current_project:
            return
        if not self._require_llm_ready():
            return
        project_slug = self.current_project
        self.novel_import.set_processing(True, "reprocess")
        self.project_combo.setDisabled(True)
        self.new_project_button.setDisabled(True)
        self.set_activity("重新处理中", "warn")
        self.append_log(
            f"开始强制重新处理已有内容：{analysis_limit or '全部'} 章"
        )
        self._start_progress_task(
            lambda report: self.project_service.reprocess_novel(
                project_slug,
                analysis_limit=analysis_limit,
                progress_callback=report,
            ),
            self._on_reprocess_success,
            self._on_reprocess_error,
            self.novel_import.set_progress,
        )

    def _on_reprocess_success(self, result: dict[str, Any]) -> None:
        self.refresh_project()
        if self.novel_import.auto_generate_images.isChecked():
            if self._begin_missing_shot_generation(
                process_result=result,
                reprocess=True,
            ):
                return
        self._finish_novel_processing(result, reprocess=True)

    def _on_reprocess_error(self, detail: str) -> None:
        detail = self._friendly_processing_error(detail)
        self.novel_import.set_processing(False)
        self.project_combo.setDisabled(False)
        self.new_project_button.setDisabled(False)
        self.novel_import.set_progress(
            self.novel_import.progress.value(),
            "重新处理失败，请查看详细错误",
        )
        self._on_gpu_error(detail)

    def select_character_image(self, character: str, image_path: Path) -> None:
        if not self.current_project:
            return
        try:
            self.project_service.select_character_image(
                self.current_project,
                character,
                image_path,
            )
        except Exception as exc:
            self.show_error("定妆照保存失败", str(exc))
            return
        self.set_activity("定妆照已选定", "good")
        self.append_log(f"已为 {character} 选定定妆照：{image_path.name}")
        self.refresh_project()

    def unlock_character_image(self, character: str) -> None:
        if not self.current_project:
            return
        try:
            self.project_service.clear_character_selection(
                self.current_project,
                character,
            )
        except Exception as exc:
            self.show_error("解除定妆失败", str(exc))
            return
        self.set_activity("定妆已解除", "good")
        self.append_log(f"已解除 {character} 的定妆选择；候选图片保持不变")
        self.refresh_project()

    def _on_generation_error(self, detail: str) -> None:
        self.characters.set_generating(False)
        self.characters.generation_stage.setText("生成失败，请查看错误")
        self._on_gpu_error(detail)

    def _on_gpu_status(self, status: GpuStatus) -> None:
        self.last_gpu_status = status
        self.settings_page.set_busy(False)
        self.settings_page.set_h3_busy(False)
        self.settings_page.set_flf_busy(False)
        self.settings_page.set_kontext_busy(False)
        self.settings_page.set_status(status)
        self.overview.set_gpu_status(status)
        self.characters.set_gpu_status(status)
        self.video_generation.set_ai_model_status(
            server_online=status.ssh_online,
            model_ready=status.video_model_ready,
            adapter_ready=status.video_runtime_ready,
            model_name=status.video_model_name,
            h3_model_ready=status.h3_model_ready,
            h3_adapter_ready=status.h3_runtime_ready,
            h3_model_name=status.h3_model_name,
            flf_model_ready=status.flf_model_ready,
            flf_adapter_ready=status.flf_runtime_ready,
            flf_model_name=status.flf_model_name,
        )
        if status.ssh_online and status.comfy_online and status.available_model_ids:
            self.set_activity("GPU 已就绪", "good")
        else:
            self.set_activity("需要检查", "warn")
        self.append_log(status.message or "服务器状态已更新")
        if status.ssh_online and self.settings_page.connection().password:
            QTimer.singleShot(0, self.check_cosyvoice)
            QTimer.singleShot(100, self.check_latentsync)

    def _require_llm_ready(self) -> bool:
        status = self.llm_service.check_status(timeout=1)
        self.settings_page.set_llm_status(status)
        if status.online:
            return True
        self.navigate(6)
        QMessageBox.warning(
            self,
            "文本模型未就绪",
            (
                f"{status.message}\n\n"
                "小说导入、人物提取和重新处理需要本地文本模型。"
                "请在“连接与设置”中点击“启动本地模型”，等待状态变为已就绪。"
            ),
        )
        return False

    def _on_llm_status(self, status: LocalLlmStatus) -> None:
        self.settings_page.set_llm_busy(False)
        self.settings_page.set_llm_status(status)
        if status.online:
            self.set_activity("文本模型已就绪", "good")
        else:
            self.set_activity("文本模型未就绪", "warn")
        self.append_log(status.message)

    def _on_llm_error(self, detail: str) -> None:
        self.settings_page.set_llm_busy(False)
        status = self.llm_service.check_status(timeout=1)
        self.settings_page.set_llm_status(status)
        self.set_activity("文本模型启动失败", "off")
        self.append_log(status.message)
        self.show_error("文本模型启动失败", detail)

    @staticmethod
    def _friendly_processing_error(detail: str) -> str:
        lowered = detail.lower()
        if "httpconnectionpool" in lowered and "1234" in lowered:
            return (
                "本地文本模型服务在处理过程中断开。\n\n"
                "请到“连接与设置”重新启动文本模型，然后再次执行处理。"
            )
        return detail

    def _on_gpu_error(self, detail: str) -> None:
        self.settings_page.set_busy(False)
        self.settings_page.set_h3_busy(False)
        self.settings_page.set_flf_busy(False)
        self.settings_page.set_kontext_busy(False)
        self.set_activity("操作失败", "off")
        first_line = detail.splitlines()[0] if detail else "未知错误"
        self.append_log(f"操作失败：{first_line}")
        self.show_error("操作失败", detail)

    def _start_task(
        self,
        callback: Callable[[], Any],
        success: Callable[[Any], None],
        failure: Callable[[str], None],
    ) -> None:
        task = BackgroundTask(callback, self)
        self.tasks.add(task)
        task.succeeded.connect(success)
        task.failed.connect(failure)
        task.finished.connect(lambda: self.tasks.discard(task))
        task.finished.connect(task.deleteLater)
        task.start()

    def _start_progress_task(
        self,
        callback: Callable[[Callable[[int, str], None]], Any],
        success: Callable[[Any], None],
        failure: Callable[[str], None],
        progress: Callable[[int, str], None],
    ) -> None:
        task = ProgressTask(callback, self)
        self.tasks.add(task)
        task.progress.connect(progress)
        task.succeeded.connect(success)
        task.failed.connect(failure)
        task.finished.connect(lambda: self.tasks.discard(task))
        task.finished.connect(task.deleteLater)
        task.start()

    def open_character_folder(self, character: str) -> None:
        if not self.current_project:
            return
        root = settings.projects_dir / self.current_project / "outputs" / "server_test"
        root.mkdir(parents=True, exist_ok=True)
        os.startfile(root)  # type: ignore[attr-defined]

    def append_log(self, message: str) -> None:
        self.jobs.append_log(message)

    def set_activity(self, text: str, state: str) -> None:
        self.activity.setText(text)
        self.activity.setObjectName({"good": "pillGood", "warn": "pillWarn"}.get(state, "pillOff"))
        self.activity.style().unpolish(self.activity)
        self.activity.style().polish(self.activity)

    def _save_connection(self, config: GpuConnection) -> None:
        self.qt_settings.setValue("gpu/host", config.host)
        self.qt_settings.setValue("gpu/port", config.port)
        self.qt_settings.setValue("gpu/user", config.username)

    def show_error(self, title: str, detail: str) -> None:
        dialog = QMessageBox(self)
        dialog.setIcon(QMessageBox.Icon.Critical)
        dialog.setWindowTitle(title)
        dialog.setText(detail.splitlines()[0] if detail else title)
        dialog.setDetailedText(detail)
        dialog.exec()

    def closeEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        if any(task.isRunning() for task in self.tasks):
            answer = QMessageBox.question(
                self,
                "后台任务仍在运行",
                "关闭窗口不会终止已经提交到 GPU 的任务。确定关闭吗？",
            )
            if answer != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
        event.accept()


def run_desktop_app() -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("Novel2Anime Studio")
    app.setOrganizationName("novel2anime")
    app.setStyle("Fusion")
    app.setStyleSheet(APP_STYLE)
    app.setFont(QFont("Microsoft YaHei UI", 10))
    window = MainWindow()
    preview_page = os.getenv("NOVEL2ANIME_SCREENSHOT_PAGE", "").strip()
    if preview_page.isdigit():
        window.navigate(max(0, min(int(preview_page), window.stack.count() - 1)))
    window.show()

    screenshot_path = os.getenv("NOVEL2ANIME_SCREENSHOT_PATH", "").strip()
    if screenshot_path:

        def capture() -> None:
            path = Path(screenshot_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            window.grab().save(str(path))
            app.quit()

        QTimer.singleShot(1200, capture)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(run_desktop_app())
