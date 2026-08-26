"""Pure planning contracts for resumable episode lip-sync batches."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

LipSyncPlanStatus = Literal["ready", "completed", "blocked", "disabled"]


@dataclass(slots=True)
class LipSyncBatchItem:
    episode_number: int
    shot_number: int
    status: LipSyncPlanStatus
    reason: str
    speaker: str = ""
    text: str = ""
    target_character: str = ""
    face_selection_mode: str = "speaker_tracking"
    source_video: Path | None = None
    face_reference: Path | None = None
    tts_engine: str = "edge_tts"
    voice_id: str = "zh-CN-YunyangNeural"
    reference_audio: Path | None = None
    reference_text: str = ""
    instruct_text: str = ""
    fallback_to_edge: bool = True
    rate: str = "+5%"
    volume: str = "+0%"
    pitch: str = "-5Hz"
    inference_steps: int = 20
    guidance_scale: float = 1.5


@dataclass(slots=True)
class LipSyncBatchPlan:
    episode_number: int
    items: list[LipSyncBatchItem] = field(default_factory=list)

    @property
    def ready(self) -> list[LipSyncBatchItem]:
        return [item for item in self.items if item.status == "ready"]

    @property
    def completed(self) -> list[LipSyncBatchItem]:
        return [item for item in self.items if item.status == "completed"]

    @property
    def blocked(self) -> list[LipSyncBatchItem]:
        return [item for item in self.items if item.status == "blocked"]

    @property
    def disabled(self) -> list[LipSyncBatchItem]:
        return [item for item in self.items if item.status == "disabled"]

    def summary(self) -> str:
        return (
            f"可执行 {len(self.ready)} · 已完成 {len(self.completed)} · "
            f"受阻 {len(self.blocked)} · 无需口型 {len(self.disabled)}"
        )


@dataclass(slots=True)
class LipSyncBatchRunResult:
    episode_number: int
    completed_shots: list[int] = field(default_factory=list)
    failed_shots: dict[int, str] = field(default_factory=dict)
    skipped_completed: list[int] = field(default_factory=list)
    blocked_shots: dict[int, str] = field(default_factory=dict)


class LipSyncBatchPlanner:
    """Build a deterministic batch plan without touching GPU or UI state."""

    def plan(
        self,
        project_root: Path,
        episode_number: int,
        *,
        regenerate_completed: bool = False,
    ) -> LipSyncBatchPlan:
        root = Path(project_root).resolve()
        episode_path = (
            root
            / "production"
            / "episodes"
            / f"episode_{episode_number:03d}.json"
        )
        episode = self._read_json(episode_path)
        selections = self._cast_selections(root)
        plan = LipSyncBatchPlan(episode_number=episode_number)
        for raw in episode.get("shots") or []:
            if not isinstance(raw, dict):
                continue
            plan.items.append(
                self._plan_shot(
                    root,
                    episode_number,
                    raw,
                    selections,
                    regenerate_completed=regenerate_completed,
                )
            )
        return plan

    def _plan_shot(
        self,
        root: Path,
        episode_number: int,
        shot: dict[str, Any],
        selections: dict[str, str],
        *,
        regenerate_completed: bool,
    ) -> LipSyncBatchItem:
        number = int(shot.get("shot_number") or 0)
        audio = self._mapping(shot.get("audio_generation"))
        lip_sync = self._mapping(shot.get("lip_sync"))
        video = self._mapping(shot.get("video_generation"))
        speaker = str(audio.get("speaker") or self._dialogue_speaker(shot)).strip()
        text = str(audio.get("text") or self._dialogue_text(shot)).strip()
        target = str(lip_sync.get("target_character") or speaker).strip()
        mode = str(lip_sync.get("mode") or "speaker_tracking").strip()
        base = LipSyncBatchItem(
            episode_number=episode_number,
            shot_number=number,
            status="ready",
            reason="校验通过",
            speaker=speaker,
            text=text,
            target_character=target,
            face_selection_mode=mode,
            tts_engine=str(audio.get("engine") or "edge_tts"),
            voice_id=str(audio.get("voice_id") or "zh-CN-YunyangNeural"),
            reference_audio=self._project_path(
                root,
                str(audio.get("reference_audio") or ""),
            ),
            reference_text=str(audio.get("reference_text") or ""),
            instruct_text=str(audio.get("instruct_text") or ""),
            fallback_to_edge=bool(audio.get("fallback_to_edge", True)),
            rate=str(audio.get("rate") or "+5%"),
            volume=str(audio.get("volume") or "+0%"),
            pitch=str(audio.get("pitch") or "-5Hz"),
            inference_steps=max(10, min(int(lip_sync.get("inference_steps") or 20), 50)),
            guidance_scale=max(
                1.0,
                min(float(lip_sync.get("guidance_scale") or 1.5), 3.0),
            ),
        )
        if not bool(lip_sync.get("enabled", False)):
            return self._status(base, "disabled", "未启用人物口型")
        if str(audio.get("mode") or "") != "dialogue" or speaker == "旁白":
            return self._status(base, "disabled", "旁白或非人物对白")
        if not text:
            return self._status(base, "blocked", "对白文案为空")
        visible_characters = self._character_names(shot)
        if target and target not in visible_characters:
            return self._status(base, "blocked", f"目标人物“{target}”不在画面角色清单")
        if mode == "manual_anchor":
            return self._status(base, "blocked", "手动目标脸尚未设置锚点")
        if mode not in {"speaker_tracking", "auto_single_face"}:
            return self._status(base, "blocked", f"不支持的目标脸模式：{mode}")

        completed_output = self._project_path(
            root,
            str(lip_sync.get("output_file") or ""),
        )
        if (
            str(lip_sync.get("status") or "") == "succeeded"
            and completed_output is not None
            and completed_output.is_file()
            and not regenerate_completed
        ):
            base.source_video = self._source_video(root, video, lip_sync)
            return self._status(base, "completed", "已有成功结果，续跑时跳过")

        base.source_video = self._source_video(root, video, lip_sync)
        if base.source_video is None or not base.source_video.is_file():
            return self._status(base, "blocked", "缺少已选中的源视频")
        if mode == "speaker_tracking":
            configured_reference = str(
                lip_sync.get("face_reference") or selections.get(target, "")
            )
            base.face_reference = self._project_path(root, configured_reference)
            if base.face_reference is None or not base.face_reference.is_file():
                return self._status(base, "blocked", f"目标人物“{target}”尚未选择定妆照")
        return base

    @classmethod
    def _source_video(
        cls,
        root: Path,
        video: dict[str, Any],
        lip_sync: dict[str, Any],
    ) -> Path | None:
        # Never recursively drive a new lip-sync pass from an older lip-sync
        # output. Prefer the recorded clean Wan source when it exists.
        clean_source = cls._project_path(
            root,
            str(lip_sync.get("source_video") or ""),
        )
        selected = cls._project_path(
            root,
            str(video.get("selected_video") or ""),
        )
        if selected and "_lipsync_" in selected.name and clean_source:
            return clean_source
        return selected or clean_source

    @staticmethod
    def _character_names(shot: dict[str, Any]) -> list[str]:
        values = shot.get("characters")
        return [
            str(item.get("name") or "").strip()
            for item in values
            if isinstance(item, dict) and str(item.get("name") or "").strip()
        ] if isinstance(values, list) else []

    @staticmethod
    def _dialogue_speaker(shot: dict[str, Any]) -> str:
        dialogue = str(shot.get("dialogue") or "")
        return dialogue.split("：", 1)[0].strip() if "：" in dialogue else ""

    @staticmethod
    def _dialogue_text(shot: dict[str, Any]) -> str:
        dialogue = str(shot.get("dialogue") or "")
        return dialogue.split("：", 1)[1].strip() if "：" in dialogue else dialogue.strip()

    @staticmethod
    def _mapping(value: Any) -> dict[str, Any]:
        return dict(value) if isinstance(value, dict) else {}

    @staticmethod
    def _status(
        item: LipSyncBatchItem,
        status: LipSyncPlanStatus,
        reason: str,
    ) -> LipSyncBatchItem:
        item.status = status
        item.reason = reason
        return item

    @classmethod
    def _cast_selections(cls, root: Path) -> dict[str, str]:
        value = cls._read_json(root / "production" / "cast_selection.json")
        selections = value.get("selections") if isinstance(value, dict) else None
        return (
            {str(name): str(path) for name, path in selections.items()}
            if isinstance(selections, dict)
            else {}
        )

    @staticmethod
    def _project_path(root: Path, value: str) -> Path | None:
        if not value:
            return None
        candidate = (root / value).resolve()
        try:
            candidate.relative_to(root)
        except ValueError:
            return None
        return candidate

    @staticmethod
    def _read_json(path: Path) -> dict[str, Any]:
        if not path.is_file():
            return {}
        try:
            value = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            return {}
        return value if isinstance(value, dict) else {}
