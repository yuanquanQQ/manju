"""Validated full-document editor for an episode story plan."""

from __future__ import annotations

import json

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
)

from app.domain.narrative import EpisodePlan


class StoryPlanEditorDialog(QDialog):
    def __init__(self, plan: EpisodePlan, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"编辑第 {plan.episode_number} 集完整编剧计划")
        self.resize(980, 760)
        self._plan = plan

        layout = QVBoxLayout(self)
        help_text = QLabel(
            "可编辑全部场次、人物弧光、长线伏笔和对白来源。场次顺序按 scenes 列表决定，"
            "保存时会执行完整领域校验。"
        )
        help_text.setWordWrap(True)
        layout.addWidget(help_text)

        self.editor = QTextEdit()
        self.editor.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap)
        self.editor.setPlainText(
            json.dumps(plan.model_dump(mode="json"), ensure_ascii=False, indent=2)
        )
        layout.addWidget(self.editor, 1)

        actions = QHBoxLayout()
        renumber = QPushButton("按列表顺序重编号场次")
        renumber.clicked.connect(self._renumber_scenes)
        actions.addWidget(renumber)
        actions.addStretch()
        layout.addLayout(actions)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("校验并保存")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    @property
    def plan(self) -> EpisodePlan:
        return self._plan

    def _read_document(self) -> dict[str, object]:
        value = json.loads(self.editor.toPlainText())
        if not isinstance(value, dict):
            raise ValueError("编剧计划顶层必须是 JSON 对象")
        return value

    def _renumber_scenes(self) -> None:
        try:
            value = self._read_document()
            scenes = value.get("scenes")
            if not isinstance(scenes, list):
                raise ValueError("scenes 必须是数组")
            for index, scene in enumerate(scenes, start=1):
                if not isinstance(scene, dict):
                    raise ValueError(f"第 {index} 个场次必须是 JSON 对象")
                scene["scene_number"] = index
            self.editor.setPlainText(json.dumps(value, ensure_ascii=False, indent=2))
        except Exception as exc:
            QMessageBox.warning(self, "无法重编号", str(exc))

    def accept(self) -> None:
        try:
            self._plan = EpisodePlan.model_validate(self._read_document())
        except Exception as exc:
            QMessageBox.warning(self, "编剧计划校验失败", str(exc))
            return
        super().accept()
