"""One-click local resource package browser."""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from app.services.asset_package_service import ResourcePackageState


class AssetLibraryPage(QWidget):
    """Show stable folders for assets and finished episode packages."""

    open_requested = Signal(str)
    organize_requested = Signal()

    def __init__(self) -> None:
        super().__init__()
        self._cards: dict[str, tuple[QLabel, QLabel, QLabel]] = {}
        layout = QVBoxLayout(self)
        layout.setContentsMargins(30, 26, 30, 32)
        layout.setSpacing(18)

        header = QHBoxLayout()
        text = QVBoxLayout()
        title = QLabel("本地资源包")
        title.setObjectName("pageTitle")
        subtitle = QLabel(
            "人物、场景、人声与成片使用固定目录；一键整理后可直接在资源管理器打开。"
        )
        subtitle.setObjectName("pageSubtitle")
        subtitle.setWordWrap(True)
        text.addWidget(title)
        text.addWidget(subtitle)
        organize = QPushButton("整理并刷新资源包")
        organize.setObjectName("primaryButton")
        organize.clicked.connect(self.organize_requested.emit)
        header.addLayout(text, 1)
        header.addWidget(organize)
        layout.addLayout(header)

        note = QLabel(
            "文件名统一使用拼音、数字和下划线。成片包按集分开，并进一步拆分视频、字幕、音频、清单与质检文件。"
        )
        note.setObjectName("pillGood")
        note.setWordWrap(True)
        layout.addWidget(note)

        grid = QGridLayout()
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(14)
        definitions = (
            ("characters", "人物资源包", "定妆照与人物资料"),
            ("locations", "场景资源包", "场景参考图与地点资料"),
            ("voices", "人声资源包", "声音档案、授权与试听"),
            ("deliverables", "合成内容包", "视频、字幕、音频与清单"),
        )
        for index, (key, label, description) in enumerate(definitions):
            card = self._build_card(key, label, description)
            grid.addWidget(card, index // 2, index % 2)
        layout.addLayout(grid)

        self.summary = QLabel("尚未加载资源包")
        self.summary.setObjectName("muted")
        layout.addWidget(self.summary)
        layout.addStretch()

    def _build_card(self, key: str, label: str, description: str) -> QFrame:
        card = QFrame()
        card.setObjectName("card")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(8)
        title = QLabel(label)
        title.setObjectName("cardTitle")
        detail = QLabel(description)
        detail.setObjectName("muted")
        count = QLabel("0 个文件")
        count.setObjectName("pillOff")
        path = QLabel("尚未创建")
        path.setObjectName("muted")
        path.setWordWrap(True)
        path.setTextInteractionFlags(path.textInteractionFlags())
        button = QPushButton(f"打开{label}")
        button.setObjectName("secondaryButton")
        button.clicked.connect(
            lambda _checked=False, value=key: self.open_requested.emit(value)
        )
        layout.addWidget(title)
        layout.addWidget(detail)
        layout.addWidget(count, 0)
        layout.addWidget(path)
        layout.addSpacing(4)
        layout.addWidget(button)
        self._cards[key] = (count, path, detail)
        return card

    def set_state(self, state: ResourcePackageState) -> None:
        total = 0
        for package in state.packages:
            widgets = self._cards.get(package.key)
            if widgets is None:
                continue
            count, path, detail = widgets
            total += package.file_count
            count.setText(
                f"{package.file_count} 个文件 · {self._format_size(package.total_bytes)}"
            )
            count.setObjectName("pillGood" if package.file_count else "pillWarn")
            count.style().unpolish(count)
            count.style().polish(count)
            path.setText(str(package.path))
            detail.setText(package.description)
        self.summary.setText(
            f"当前项目共整理 {total} 个资源文件；源文件保留，资源包使用硬链接或安全副本。"
        )

    @staticmethod
    def _format_size(value: int) -> str:
        size = float(value)
        for unit in ("B", "KB", "MB", "GB"):
            if size < 1024 or unit == "GB":
                return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
            size /= 1024
        return f"{size:.1f} GB"
