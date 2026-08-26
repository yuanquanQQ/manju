"""Voice library and automatic character casting workbench."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from app.services.voice_library_service import (
    CharacterVoiceTraits,
    VoiceAssignment,
    VoiceProfile,
)


class VoiceProfileDialog(QDialog):
    """Collect one authorized zero-shot voice reference."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("导入克隆音色")
        self.setMinimumSize(650, 720)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 20)
        title = QLabel("建立一个可复用的声音档案")
        title.setStyleSheet("font-size:20px;font-weight:700;color:#101828;")
        note = QLabel(
            "建议使用 3–15 秒、无背景音乐、无混响、只有一个人的清晰语音。"
            "参考台词必须和音频逐字一致。"
        )
        note.setObjectName("muted")
        note.setWordWrap(True)
        layout.addWidget(title)
        layout.addWidget(note)

        form = QFormLayout()
        form.setSpacing(11)
        self.name = QLineEdit()
        self.name.setPlaceholderText("例如：冷峻青年男声 A")
        form.addRow("声音名称", self.name)

        audio_row = QHBoxLayout()
        self.audio = QLineEdit()
        self.audio.setPlaceholderText("选择 WAV / MP3 / FLAC / M4A / OGG")
        browse = QPushButton("选择音频")
        browse.setObjectName("secondaryButton")
        browse.clicked.connect(self._browse_audio)
        audio_row.addWidget(self.audio, 1)
        audio_row.addWidget(browse)
        form.addRow("参考音频", audio_row)

        self.reference_text = QTextEdit()
        self.reference_text.setMaximumHeight(90)
        self.reference_text.setPlaceholderText("填写参考音频中实际说出的完整台词")
        form.addRow("逐字参考台词", self.reference_text)

        self.gender = QComboBox()
        self.gender.addItems(["男声", "女声", "中性"])
        self.age_group = QComboBox()
        self.age_group.addItems(["儿童", "少年", "青年", "中年", "老年"])
        self.age_group.setCurrentText("青年")
        self.temperament = QComboBox()
        self.temperament.addItems(
            ["沉稳", "冷峻", "温柔", "活泼", "威严", "阴沉", "热血"]
        )
        self.pitch = QComboBox()
        self.pitch.addItems(["高", "中高", "中", "中低", "低"])
        self.pitch.setCurrentText("中")
        self.pace = QComboBox()
        self.pace.addItems(["慢", "中", "快"])
        self.pace.setCurrentText("中")
        form.addRow("声音性别", self.gender)
        form.addRow("年龄感", self.age_group)
        form.addRow("表演气质", self.temperament)
        form.addRow("基础音高", self.pitch)
        form.addRow("常用语速", self.pace)

        self.tags = QLineEdit()
        self.tags.setPlaceholderText("逗号分隔，例如：主角,仙侠,克制")
        form.addRow("标签", self.tags)
        self.instruction = QLineEdit(
            "自然、克制、像真人表演，避免播音腔"
        )
        form.addRow("默认表演指令", self.instruction)
        self.source_label = QLineEdit()
        self.source_label.setPlaceholderText("例如：本人录制 / 配音演员合同编号")
        form.addRow("声音来源说明", self.source_label)
        self.authorization = QComboBox()
        self.authorization.addItem("本人声音", "self")
        self.authorization.addItem("已获明确授权", "licensed")
        self.authorization.addItem("原创合成音色", "synthetic")
        form.addRow("授权类型", self.authorization)
        self.consent_note = QLineEdit()
        self.consent_note.setPlaceholderText("可填写合同、授权时间或素材来源")
        form.addRow("授权备注", self.consent_note)
        layout.addLayout(form)

        consent_card = QFrame()
        consent_card.setObjectName("card")
        consent_layout = QVBoxLayout(consent_card)
        self.consent = QCheckBox(
            "我确认拥有该声音的使用与克隆授权，不用于冒充、欺诈或未授权发布。"
        )
        consent_layout.addWidget(self.consent)
        layout.addWidget(consent_card)

        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Cancel
            | QDialogButtonBox.StandardButton.Ok
        )
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setText("导入声音库")
        self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        self.buttons.accepted.connect(self._accept_if_valid)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)

    def _browse_audio(self) -> None:
        selected, _filter = QFileDialog.getOpenFileName(
            self,
            "选择授权参考音频",
            "",
            "音频 (*.wav *.mp3 *.flac *.m4a *.ogg)",
        )
        if selected:
            self.audio.setText(selected)

    def _accept_if_valid(self) -> None:
        if not self.name.text().strip():
            QMessageBox.warning(self, "信息不完整", "请输入声音名称。")
            return
        if not Path(self.audio.text().strip()).is_file():
            QMessageBox.warning(self, "信息不完整", "请选择存在的参考音频。")
            return
        if not self.reference_text.toPlainText().strip():
            QMessageBox.warning(self, "信息不完整", "请填写逐字参考台词。")
            return
        if not self.consent.isChecked():
            QMessageBox.warning(self, "需要授权确认", "请先确认声音授权与用途。")
            return
        self.accept()

    def values(self) -> dict[str, object]:
        tags = self.tags.text().replace("，", ",").split(",")
        return {
            "name": self.name.text().strip(),
            "source_audio": Path(self.audio.text().strip()),
            "reference_text": self.reference_text.toPlainText().strip(),
            "gender": self.gender.currentText(),
            "age_group": self.age_group.currentText(),
            "temperament": self.temperament.currentText(),
            "pitch": self.pitch.currentText(),
            "pace": self.pace.currentText(),
            "tags": [item.strip() for item in tags if item.strip()],
            "default_instruction": self.instruction.text().strip(),
            "source_label": self.source_label.text().strip(),
            "authorization": str(self.authorization.currentData()),
            "consent_note": self.consent_note.text().strip(),
        }


class VoiceLibraryPage(QWidget):
    """Shared voices plus per-project automatic/manual casting."""

    add_voice_requested = Signal(dict)
    delete_voice_requested = Signal(str)
    preview_requested = Signal(str, str)
    auto_match_requested = Signal()
    save_assignments_requested = Signal(dict)
    apply_assignments_requested = Signal()

    def __init__(self) -> None:
        super().__init__()
        self._profiles: list[VoiceProfile] = []
        self._profile_by_id: dict[str, VoiceProfile] = {}
        self._loading_assignments = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(30, 26, 30, 32)
        layout.setSpacing(18)

        title = QLabel("声音角色库")
        title.setObjectName("pageTitle")
        subtitle = QLabel(
            "导入经过授权的参考声音，自动分析小说人物并匹配音色；每个人物都可以手动改配。"
        )
        subtitle.setObjectName("pageSubtitle")
        subtitle.setWordWrap(True)
        layout.addWidget(title)
        layout.addWidget(subtitle)

        self.summary = QLabel("尚未加载声音库")
        self.summary.setObjectName("pillOff")
        layout.addWidget(self.summary, 0, Qt.AlignmentFlag.AlignLeft)

        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_library_tab(), "声音库")
        self.tabs.addTab(self._build_casting_tab(), "人物自动选声")
        layout.addWidget(self.tabs, 1)

    def _build_library_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(0, 16, 0, 0)
        actions = QHBoxLayout()
        add = QPushButton("＋ 导入克隆音色")
        add.setObjectName("primaryButton")
        add.clicked.connect(self._open_add_dialog)
        delete = QPushButton("删除所选音色")
        delete.setObjectName("dangerButton")
        delete.clicked.connect(self._request_delete)
        actions.addWidget(add)
        actions.addWidget(delete)
        actions.addStretch()
        layout.addLayout(actions)

        self.voice_table = QTableWidget(0, 8)
        self.voice_table.setHorizontalHeaderLabels(
            ["声音名称", "引擎", "性别", "年龄感", "气质", "音高/语速", "来源授权", "状态"]
        )
        self.voice_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self.voice_table.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection
        )
        self.voice_table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers
        )
        self.voice_table.verticalHeader().setVisible(False)
        self.voice_table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch
        )
        for column in range(1, 8):
            self.voice_table.horizontalHeader().setSectionResizeMode(
                column, QHeaderView.ResizeMode.ResizeToContents
            )
        self.voice_table.setMinimumHeight(310)
        layout.addWidget(self.voice_table)

        preview_card = QFrame()
        preview_card.setObjectName("card")
        preview_layout = QHBoxLayout(preview_card)
        preview_layout.setContentsMargins(16, 14, 16, 14)
        self.preview_text = QLineEdit(
            "少侠，前路虽然凶险，但真正的强者从不会停下脚步。"
        )
        preview = QPushButton("试听所选音色")
        preview.setObjectName("secondaryButton")
        preview.clicked.connect(self._request_preview)
        preview_layout.addWidget(QLabel("试听台词"))
        preview_layout.addWidget(self.preview_text, 1)
        preview_layout.addWidget(preview)
        layout.addWidget(preview_card)
        return tab

    def _build_casting_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(0, 16, 0, 0)
        explanation = QLabel(
            "自动匹配依据：人物性别、年龄感、性格、主配角定位、常用音高和语速。"
            "手动选择后会锁定，不再被下一次自动匹配覆盖。"
        )
        explanation.setObjectName("muted")
        explanation.setWordWrap(True)
        layout.addWidget(explanation)
        actions = QHBoxLayout()
        auto = QPushButton("分析人物并自动匹配")
        auto.setObjectName("primaryButton")
        auto.clicked.connect(self.auto_match_requested.emit)
        save = QPushButton("保存手动分配")
        save.setObjectName("secondaryButton")
        save.clicked.connect(self._request_save_assignments)
        apply = QPushButton("应用到全部分镜")
        apply.setObjectName("secondaryButton")
        apply.clicked.connect(self.apply_assignments_requested.emit)
        actions.addWidget(auto)
        actions.addWidget(save)
        actions.addWidget(apply)
        actions.addStretch()
        layout.addLayout(actions)

        self.cast_table = QTableWidget(0, 6)
        self.cast_table.setHorizontalHeaderLabels(
            ["人物", "识别特征", "对白数", "当前声音", "分配方式", "匹配依据"]
        )
        self.cast_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self.cast_table.verticalHeader().setVisible(False)
        self.cast_table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.ResizeToContents
        )
        self.cast_table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.Stretch
        )
        self.cast_table.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.ResizeMode.ResizeToContents
        )
        self.cast_table.horizontalHeader().setSectionResizeMode(
            3, QHeaderView.ResizeMode.Stretch
        )
        self.cast_table.horizontalHeader().setSectionResizeMode(
            4, QHeaderView.ResizeMode.ResizeToContents
        )
        self.cast_table.horizontalHeader().setSectionResizeMode(
            5, QHeaderView.ResizeMode.Stretch
        )
        self.cast_table.setMinimumHeight(430)
        layout.addWidget(self.cast_table)
        warning = QLabel(
            "注意：把新声音应用到已完成口型的镜头，会自动恢复干净 Wan 源视频并把口型设为待重做。"
        )
        warning.setObjectName("pillWarn")
        warning.setWordWrap(True)
        layout.addWidget(warning)
        return tab

    def set_state(
        self,
        profiles: list[VoiceProfile],
        traits: list[CharacterVoiceTraits],
        assignments: dict[str, VoiceAssignment],
    ) -> None:
        self._profiles = profiles
        self._profile_by_id = {item.profile_id: item for item in profiles}
        cloned = sum(not item.builtin for item in profiles)
        manual = sum(item.mode == "manual" for item in assignments.values())
        self.summary.setText(
            f"{len(profiles)} 个声音（{cloned} 个克隆） · "
            f"{len(traits)} 个人物 · {len(assignments)} 个已分配 · {manual} 个手动锁定"
        )
        self.summary.setObjectName("pillGood" if assignments else "pillWarn")
        self.summary.style().unpolish(self.summary)
        self.summary.style().polish(self.summary)
        self._populate_voice_table()
        self._populate_cast_table(traits, assignments)

    def _populate_voice_table(self) -> None:
        self.voice_table.setRowCount(len(self._profiles))
        for row, profile in enumerate(self._profiles):
            authorization = {
                "self": "本人",
                "licensed": "已授权",
                "synthetic": "合成音色",
            }.get(profile.authorization, profile.authorization)
            values = (
                profile.name,
                "CosyVoice 3" if profile.engine == "cosyvoice" else "Edge TTS",
                profile.gender,
                profile.age_group,
                profile.temperament,
                f"{profile.pitch}/{profile.pace}",
                f"{profile.source_label or '内置'} · {authorization}",
                "参考音频就绪" if profile.reference_audio else "内置可用",
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                if column == 0:
                    item.setData(Qt.ItemDataRole.UserRole, profile.profile_id)
                self.voice_table.setItem(row, column, item)
            self.voice_table.setRowHeight(row, 38)

    def _populate_cast_table(
        self,
        traits: list[CharacterVoiceTraits],
        assignments: dict[str, VoiceAssignment],
    ) -> None:
        self._loading_assignments = True
        self.cast_table.setRowCount(len(traits))
        try:
            for row, trait in enumerate(traits):
                assignment = assignments.get(trait.character)
                self.cast_table.setItem(row, 0, QTableWidgetItem(trait.character))
                self.cast_table.setItem(row, 1, QTableWidgetItem(trait.summary()))
                self.cast_table.setItem(
                    row, 2, QTableWidgetItem(str(trait.dialogue_count))
                )
                combo = QComboBox()
                combo.addItem("未分配", "")
                for profile in self._profiles:
                    combo.addItem(profile.name, profile.profile_id)
                if assignment:
                    index = combo.findData(assignment.profile_id)
                    combo.setCurrentIndex(max(0, index))
                combo.setProperty("character", trait.character)
                combo.setProperty(
                    "assignment_mode", assignment.mode if assignment else "manual"
                )
                combo.currentIndexChanged.connect(
                    lambda _index, widget=combo: self._mark_manual(widget)
                )
                self.cast_table.setCellWidget(row, 3, combo)
                mode = "手动锁定" if assignment and assignment.mode == "manual" else "自动推荐" if assignment else "未分配"
                self.cast_table.setItem(row, 4, QTableWidgetItem(mode))
                reason = ""
                if assignment:
                    reason = "、".join(assignment.reasons)
                    reason += f"（{assignment.confidence:.0%}）"
                self.cast_table.setItem(row, 5, QTableWidgetItem(reason))
                self.cast_table.setRowHeight(row, 42)
        finally:
            self._loading_assignments = False

    def assignment_selections(self) -> dict[str, str]:
        values: dict[str, str] = {}
        for row in range(self.cast_table.rowCount()):
            character_item = self.cast_table.item(row, 0)
            combo = self.cast_table.cellWidget(row, 3)
            if character_item is None or not isinstance(combo, QComboBox):
                continue
            if str(combo.property("assignment_mode")) != "manual":
                continue
            values[character_item.text()] = str(combo.currentData() or "")
        return values

    def _mark_manual(self, combo: QComboBox) -> None:
        if self._loading_assignments:
            return
        combo.setProperty("assignment_mode", "manual")
        for row in range(self.cast_table.rowCount()):
            if self.cast_table.cellWidget(row, 3) is combo:
                self.cast_table.setItem(row, 4, QTableWidgetItem("手动锁定"))
                self.cast_table.setItem(row, 5, QTableWidgetItem("尚未保存"))
                break

    def _open_add_dialog(self) -> None:
        dialog = VoiceProfileDialog(self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.add_voice_requested.emit(dialog.values())

    def _selected_profile_id(self) -> str:
        row = self.voice_table.currentRow()
        item = self.voice_table.item(row, 0) if row >= 0 else None
        return str(item.data(Qt.ItemDataRole.UserRole) or "") if item else ""

    def _request_delete(self) -> None:
        profile_id = self._selected_profile_id()
        if not profile_id:
            QMessageBox.information(self, "尚未选择", "请先选择一个声音。")
            return
        profile = self._profile_by_id.get(profile_id)
        if profile and profile.builtin:
            QMessageBox.information(self, "不能删除", "内置 Edge 音色不能删除。")
            return
        answer = QMessageBox.question(
            self,
            "删除声音",
            f"确定删除“{profile.name if profile else profile_id}”及其参考音频吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
        )
        if answer == QMessageBox.StandardButton.Yes:
            self.delete_voice_requested.emit(profile_id)

    def _request_preview(self) -> None:
        profile_id = self._selected_profile_id()
        if not profile_id:
            QMessageBox.information(self, "尚未选择", "请先选择要试听的声音。")
            return
        text = self.preview_text.text().strip()
        if not text:
            QMessageBox.information(self, "缺少台词", "请输入试听台词。")
            return
        self.preview_requested.emit(profile_id, text)

    def _request_save_assignments(self) -> None:
        self.save_assignments_requested.emit(self.assignment_selections())
