"""Read-only presentation service for the desktop application."""

from __future__ import annotations

import json
import math
import re
import shutil
import sqlite3
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from app.compiler.importer import import_novel
from app.compiler.repository import persist_import
from app.core.config import settings
from app.core.files import atomic_write_json
from app.database.db import init_db
from app.pipeline.audio_timing import (
    AudioTimingSummary,
    optimize_episode_audio_timing,
)
from app.pipeline.compile_novel import run_compile_novel
from app.pipeline.continuity import plan_episode_continuity
from app.pipeline.storyboard import generate_storyboard
from app.services.character_presets import DEFAULT_CHARACTER_LAYOUT_ID
from app.services.image_models import image_model_label
from app.services.project_service import create_project

DEFAULT_VIDEO_NEGATIVE_PROMPT = (
    "face morphing, identity change, age change, costume change, extra limbs, "
    "extra fingers, deformed hands, body distortion, duplicate person, flicker, "
    "frame jitter, camera shake, warped background, missing face, faceless, "
    "face blur, facial feature loss, asymmetric eyes, crossed eyes, missing eyes, "
    "mouth distortion, melted face, occluded face, cropped face, fast motion, "
    "sudden motion, rapid head turn, exaggerated expression, talking, lip sync, "
    "open mouth, crowd motion, moving background people, text, logo, watermark"
)

DEFAULT_H3_SOUND_EFFECT_PROMPT = (
    "清晰可闻、与画面地点一致的自然环境声，人物动作对应的脚步、衣料摩擦和道具声，"
    "所有声音严格跟随可见动作，不增加画外爆炸或夸张冲击"
)

DEFAULT_H3_MUSIC_PROMPT = (
    "清晰可闻但克制的电影感中国仙侠配乐，无歌词，古琴与低弦轻柔铺底，"
    "为旁白和对白保留清晰频段，结尾自然收束"
)

LOCOMOTION_PROMPT_TERMS = (
    "行走",
    "走",
    "快步",
    "奔跑",
    "跑",
    "踏步",
    "上前",
    "跟随",
    "walk",
    "walking",
    "run",
    "running",
    "step forward",
    "approach",
    "follow",
)

LOCOMOTION_FRAMING_PROMPT = (
    "motion-ready extreme wide full-body composition, camera at least eight meters "
    "from the actor, the complete standing figure occupies only 45 to 55 percent of "
    "image height, visible from head to both feet, both feet fully visible and footwear "
    "in contact with the ground, generous empty ground visible below the soles, never "
    "crop the knees, calves, ankles or feet, generous clear movement space, "
    "balanced stable starting pose suitable for image-to-video animation"
)

LEGACY_LOCOMOTION_FRAMING_PROMPT = (
    "motion-ready wide full-body composition, complete body visible from head "
    "to both feet, both feet fully visible in contact with the ground, uncropped "
    "legs and footwear, generous clear movement space in the travel direction, "
    "balanced stable starting pose suitable for image-to-video animation"
)

CLOSE_FRAMING_TERMS = (
    "近景",
    "中近景",
    "特写",
    "close-up",
    "close up",
    "medium close",
)

HIGH_ACTION_TERMS = LOCOMOTION_PROMPT_TERMS + (
    "后退",
    "退半步",
    "踏半步",
    "起身",
    "站起",
    "蹲下",
    "弯腰",
    "转身",
    "回头",
    "拔剑",
    "拔刀",
    "拔出",
    "挥剑",
    "刺剑",
    "冲刺",
    "跃起",
    "跳",
    "倒下",
    "摔倒",
    "推开",
    "拉开",
    "抱拳",
    "收紧包围",
    "walk",
    "step back",
    "stand up",
    "sit down",
    "turn around",
    "draws the sword",
    "sword strike",
    "jump",
    "fall down",
)

ROUTING_VERSION = 4
END_FRAME_PROMPT_VERSION = 5


def _automatic_video_route(
    shot: dict[str, Any],
    subject_motion: str,
) -> tuple[str, str, str]:
    """Route production shots through MiniMax H3's native audio-video graph."""

    motion = subject_motion.lower()
    high_terms: list[str] = []
    for term in HIGH_ACTION_TERMS:
        position = motion.find(term)
        if position < 0:
            continue
        prefix = motion[max(0, position - 4):position]
        if any(negation in prefix for negation in ("不", "不要", "不得", "没有", "no ")):
            continue
        high_terms.append(term)
    characters = shot.get("characters")
    character_count = (
        len([item for item in characters if isinstance(item, dict)])
        if isinstance(characters, list)
        else 0
    )
    if high_terms:
        reason = (
            f"检测到明确位移/姿态变化“{high_terms[0]}”"
            + (f"，且画面含 {character_count} 名人物" if character_count > 1 else "")
            + "；使用 MiniMax H3 FL2VA，可选尾帧约束动作并同步生成环境音效"
        )
        return "minimax_h3_fl2va", "high", reason
    return (
        "minimax_h3_fl2va",
        "low",
        "统一使用 MiniMax H3 FL2VA 生成画面、环境音效与配乐，保留后期对白配音空间",
    )


def _end_frame_prompt(
    shot: dict[str, Any],
    subject_motion: str,
    continuity_constraints: str,
) -> str:
    """Describe a physically reachable end keyframe in the same continuous shot."""

    scene = str(shot.get("scene_description") or "").strip()
    action = subject_motion or scene
    locomotion_endpoint = ""
    if any(term in action.lower() for term in LOCOMOTION_PROMPT_TERMS):
        locomotion_endpoint = (
            "EXACT WALKING ENDPOINT: advance the actor by one complete natural step "
            "and visibly REVERSE the leg arrangement from the start image: whichever "
            "foot was behind in the reference is now one stride forward and fully "
            "planted, while the foot that was forward now trails behind with heel "
            "down. Hips and shoulders show believable transferred weight, both legs "
            "must be unmistakably different from the start pose, and the requested "
            "head and eye turn is complete while any hand-to-body or hand-to-prop "
            "contact remains intact. "
        )
    return (
        "END KEYFRAME OF THE SAME CONTINUOUS SHOT, not a starting pose and not a "
        f"new composition. The requested action is now visibly complete: {action}. "
        f"{locomotion_endpoint}"
        "Show the settled result of that action, with a clearly changed but physically "
        "reachable body pose compared with the start frame. If the action involves "
        "walking or stepping, place both feet visibly on the ground in a natural "
        "staggered stopping stance and show the requested final gaze direction. "
        "Preserve the exact same actors, face identity, apparent age, hairstyle, "
        "costume, props, location, lighting, camera height, lens, composition and "
        "screen direction as the start keyframe. Show a physically reachable final "
        "pose with natural balance, correct foot contact and believable hand-to-prop "
        "contact; the action has progressed only one small beat, with no scene cut, "
        "no teleportation, no new person and no identity change. "
        f"Continuity requirement: {continuity_constraints}"
    )[:2400]

CAMERA_MOVEMENT_MAP = {
    "static": "still",
    "pan": "pan_right",
    "tilt": "tilt_up",
    "zoom": "slow_push",
    "dolly": "slow_push",
    "handheld": "still",
    "crane": "tilt_up",
    "tracking": "pan_right",
}

TRANSITION_MAP = {
    "cut": "cut",
    "fade": "fade_black",
    "dissolve": "dissolve",
    "wipe": "match_cut",
}


@dataclass(slots=True)
class ProjectSnapshot:
    slug: str
    display_name: str
    root: Path
    chapter_count: int = 0
    analysis_count: int = 0
    entity_count: int = 0
    event_count: int = 0
    episode_count: int = 0
    image_count: int = 0
    video_count: int = 0
    cast_character_count: int = 0
    cast_selected_count: int = 0
    job_counts: dict[str, int] = field(default_factory=dict)


@dataclass(slots=True)
class ImageSnapshot:
    path: Path
    model_id: str
    model_label: str
    generated_at: str
    layout_label: str = "单人定妆照"


@dataclass(slots=True)
class CharacterSnapshot:
    name: str
    profile: str
    style: str = "真人电影"
    generation_preset: str = DEFAULT_CHARACTER_LAYOUT_ID
    images: list[ImageSnapshot] = field(default_factory=list)
    selected_image: Path | None = None


@dataclass(slots=True)
class ShotSnapshot:
    number: int
    description: str
    prompt: str
    style: str = "真人电影"
    duration_seconds: float = 3.0
    camera_movement: str = "auto"
    motion_prompt: str = ""
    end_frame_prompt: str = ""
    routing_reason: str = ""
    routing_locked: bool = False
    subject_motion: str = ""
    environment_motion: str = ""
    continuity_constraints: str = ""
    negative_prompt: str = ""
    native_audio_mode: str = "ambience_sfx_music"
    dialogue_prompt: str = ""
    sound_effect_prompt: str = ""
    music_prompt: str = ""
    engine_profile: str = "minimax_h3_fl2va"
    motion_strength: str = "low"
    screen_direction: str = "auto"
    transition_out: str = "cut"
    transition_frames: int = 8
    handle_frames: int = 8
    candidate_count: int = 1
    continuity_group: str = "scene_01"
    beat_type: str = "dialogue"
    action_phase: str = "anticipation"
    entry_state: str = ""
    exit_state: str = ""
    match_anchor: str = ""
    reference_shot_number: int = 0
    reference_denoise: float = 0.76
    source_image: Path | None = None
    image_candidates: list[ImageSnapshot] = field(default_factory=list)
    image_qc_status: str = "missing"
    image_qc_note: str = ""
    image_qc_checked_at: str = ""
    end_image: Path | None = None
    video_path: Path | None = None
    dialogue: str = ""
    audio_mode: str = "auto_narration"
    speaker: str = "旁白"
    tts_engine: str = "edge_tts"
    voice_id: str = "zh-CN-YunyangNeural"
    voice_reference_path: Path | None = None
    voice_reference_text: str = ""
    voice_instruct_text: str = ""
    fallback_to_edge: bool = True
    speech_rate: str = "+5%"
    speech_volume: str = "+0%"
    speech_pitch: str = "-5Hz"
    subtitle_enabled: bool = True
    audio_path: Path | None = None
    subtitle_path: Path | None = None
    estimated_audio_duration_seconds: float = 0.0
    planned_timeline_duration_seconds: float = 0.0
    timing_status: str = "unplanned"
    recommended_segments: int = 1
    lip_sync_enabled: bool = False
    lip_sync_engine: str = "latentsync_1_6"
    lip_sync_target_character: str = ""
    lip_sync_mode: str = "speaker_tracking"
    lip_sync_status: str = "disabled"
    lip_sync_score: float = 0.0
    lip_sync_output_path: Path | None = None


@dataclass(slots=True)
class ChapterSnapshot:
    order: int
    title: str
    character_count: int
    preview: str


@dataclass(slots=True)
class EpisodeSnapshot:
    number: int
    title: str
    path: Path
    characters: list[CharacterSnapshot]
    shots: list[ShotSnapshot]
    dubbed_video_path: Path | None = None
    dubbing_manifest_path: Path | None = None


@dataclass(slots=True)
class JobSnapshot:
    job_id: str
    job_type: str
    status: str
    progress: float
    error: str
    updated_at: str


class DesktopProjectService:
    """Collects project data without coupling Qt views to persistence."""

    def __init__(self, projects_dir: Path | None = None) -> None:
        self.projects_dir = Path(projects_dir or settings.projects_dir)

    def list_projects(self) -> list[str]:
        if not self.projects_dir.exists():
            return []
        return sorted(
            item.name
            for item in self.projects_dir.iterdir()
            if item.is_dir() and (item / "project.json").exists()
        )

    def create_project(self, slug: str, display_name: str) -> Path:
        return create_project(
            slug,
            display_name=display_name,
            projects_dir=self.projects_dir,
        )

    def load_project(self, slug: str) -> ProjectSnapshot:
        root = (self.projects_dir / slug).resolve()
        manifest = self._read_json(root / "project.json")
        snapshot = ProjectSnapshot(
            slug=slug,
            display_name=str(manifest.get("display_name") or slug),
            root=root,
        )

        db_path = root / "database" / "world.db"
        if db_path.exists():
            with sqlite3.connect(db_path) as connection:
                snapshot.chapter_count = self._count(connection, "compiled_chapters")
                snapshot.analysis_count = self._count_distinct(
                    connection,
                    "chapter_analysis_runs",
                    "chapter_id",
                    "status = 'SUCCEEDED'",
                )
                snapshot.entity_count = self._count(connection, "entities")
                snapshot.event_count = self._count(connection, "narrative_events")
                try:
                    rows = connection.execute(
                        "SELECT status, COUNT(*) FROM jobs GROUP BY status"
                    ).fetchall()
                    snapshot.job_counts = {str(status): int(count) for status, count in rows}
                except sqlite3.Error:
                    snapshot.job_counts = {}

        episodes = root / "production" / "episodes"
        episode_files = sorted(episodes.glob("episode_*.json"))
        snapshot.episode_count = len(episode_files)
        if episode_files:
            episode = self._read_json(episode_files[0])
            profiles = episode.get("character_profiles") or {}
            snapshot.cast_character_count = len(profiles) if isinstance(profiles, dict) else 0
        snapshot.cast_selected_count = len(self.load_cast_selections(slug))
        snapshot.image_count = sum(
            1 for path in root.rglob("*") if path.suffix.lower() in {".png", ".jpg", ".jpeg"}
        )
        snapshot.video_count = sum(
            1 for path in root.rglob("*") if path.suffix.lower() in {".mp4", ".mov", ".webm"}
        )
        return snapshot

    def load_episodes(self, slug: str) -> list[EpisodeSnapshot]:
        root = (self.projects_dir / slug).resolve()
        selections = self.load_cast_selections(slug)
        result: list[EpisodeSnapshot] = []
        for path in sorted((root / "production" / "episodes").glob("episode_*.json")):
            value = self._read_json(path)
            episode_number = int(value.get("episode_number") or len(result) + 1)
            dubbing = value.get("dubbing") or {}
            if not isinstance(dubbing, dict):
                dubbing = {}
            profiles = value.get("character_profiles") or {}
            character_styles = value.get("character_styles") or {}
            character_presets = value.get("character_generation_presets") or {}
            characters = [
                CharacterSnapshot(
                    name=str(name),
                    profile=str(profile),
                    style=str(character_styles.get(str(name)) or "真人电影"),
                    generation_preset=str(
                        character_presets.get(str(name)) or DEFAULT_CHARACTER_LAYOUT_ID
                    ),
                    images=self._find_character_images(root, str(name)),
                    selected_image=self._selection_path(
                        root,
                        selections.get(str(name), ""),
                    ),
                )
                for name, profile in profiles.items()
            ]
            allow_legacy_artifacts = (
                str(value.get("artifact_binding_policy") or "legacy_fallback")
                != "explicit_only"
            )
            shots = [
                self._shot_snapshot(
                    root,
                    episode_number,
                    item,
                    index,
                    allow_legacy_artifacts=allow_legacy_artifacts,
                )
                for index, item in enumerate(value.get("shots") or [], start=1)
                if isinstance(item, dict)
            ]
            result.append(
                EpisodeSnapshot(
                    number=episode_number,
                    title=str(value.get("episode_title") or path.stem),
                    path=path,
                    characters=characters,
                    shots=shots,
                    dubbed_video_path=(
                        self._safe_project_path(
                            root,
                            str(dubbing.get("output_file") or ""),
                        )
                        or (
                            self._find_latest_episode_dubbed(
                                root,
                                episode_number,
                            )
                            if allow_legacy_artifacts
                            else None
                        )
                    ),
                    dubbing_manifest_path=self._safe_project_path(
                        root,
                        str(dubbing.get("manifest_file") or ""),
                    ),
                )
            )
        return result

    def load_chapters(
        self,
        slug: str,
        *,
        limit: int = 200,
    ) -> list[ChapterSnapshot]:
        root = (self.projects_dir / slug).resolve()
        result: list[ChapterSnapshot] = []
        paths = sorted((root / "novel" / "chapters").glob("ch_*.json"))
        if limit > 0:
            paths = paths[:limit]
        for path in paths:
            value = self._read_json(path)
            content = str(value.get("content") or "")
            result.append(
                ChapterSnapshot(
                    order=int(value.get("order") or len(result) + 1),
                    title=str(value.get("title") or path.stem),
                    character_count=len(content),
                    preview=" ".join(content.split())[:120],
                )
            )
        return result

    def save_character_prompt(
        self,
        slug: str,
        episode_number: int,
        character: str,
        prompt: str,
        style: str,
        generation_preset: str = DEFAULT_CHARACTER_LAYOUT_ID,
    ) -> Path:
        path = self.episode_path(slug, episode_number)
        value = self._read_json(path)
        profiles = value.get("character_profiles") or {}
        if character not in profiles:
            raise KeyError(f"分镜中不存在角色: {character}")
        profiles[character] = prompt.strip()
        value["character_profiles"] = profiles
        styles = value.get("character_styles") or {}
        styles[character] = style
        value["character_styles"] = styles
        presets = value.get("character_generation_presets") or {}
        presets[character] = generation_preset
        value["character_generation_presets"] = presets
        atomic_write_json(path, value)
        return path

    def save_shot_prompt(
        self,
        slug: str,
        episode_number: int,
        shot_number: int,
        prompt: str,
        style: str,
    ) -> Path:
        path = self.episode_path(slug, episode_number)
        value = self._read_json(path)
        for index, shot in enumerate(value.get("shots") or [], start=1):
            if not isinstance(shot, dict):
                continue
            number = int(shot.get("shot_number") or index)
            if number == shot_number:
                shot["image_prompt"] = prompt.strip()
                shot["style_preset"] = style
                atomic_write_json(path, value)
                return path
        raise KeyError(f"分镜中不存在镜头: {shot_number}")

    def set_episode_artifact_binding_policy(
        self,
        slug: str,
        episode_number: int,
        policy: str,
    ) -> Path:
        if policy not in {"legacy_fallback", "explicit_only"}:
            raise ValueError(f"不支持的素材绑定策略：{policy}")
        path = self.episode_path(slug, episode_number)
        value = self._read_json(path)
        value["artifact_binding_policy"] = policy
        atomic_write_json(path, value)
        return path

    def prepare_shot_automation(
        self,
        slug: str,
        *,
        episode_numbers: set[int] | None = None,
        prefer_ai_video: bool = True,
        force_continuity: bool = False,
    ) -> dict[str, int]:
        """Backfill complete image/video instructions for new and legacy shots."""

        root = (self.projects_dir / slug).resolve()
        stats = {
            "episodes": 0,
            "shots": 0,
            "image_prompts_added": 0,
            "motion_framing_prompts_updated": 0,
            "motion_framing_prompts_removed": 0,
            "video_prompts_added": 0,
            "continuity_plans_updated": 0,
            "continuity_reference_links": 0,
            "source_images_ready": 0,
            "video_routes_updated": 0,
            "video_selections_cleared": 0,
            "end_frame_prompts_added": 0,
        }
        for path in sorted(
            (root / "production" / "episodes").glob("episode_*.json")
        ):
            value = self._read_json(path)
            episode_number = int(value.get("episode_number") or 0)
            if episode_numbers is not None and episode_number not in episode_numbers:
                continue
            changed = False
            stats["episodes"] += 1
            continuity_stats = plan_episode_continuity(
                value,
                force=force_continuity,
            )
            stats["continuity_plans_updated"] += continuity_stats[
                "plans_updated"
            ]
            stats["continuity_reference_links"] += continuity_stats[
                "reference_links"
            ]
            if continuity_stats["plans_updated"]:
                changed = True
            for fallback_number, shot in enumerate(
                value.get("shots") or [],
                start=1,
            ):
                if not isinstance(shot, dict):
                    continue
                stats["shots"] += 1
                shot_number = int(shot.get("shot_number") or fallback_number)
                scene = str(shot.get("scene_description") or "").strip()
                if not str(shot.get("image_prompt") or "").strip():
                    shot["image_prompt"] = (
                        "masterpiece, best quality, photorealistic live-action "
                        f"Chinese xianxia cinematic scene, {scene}, natural skin "
                        "texture, cinematic lighting, coherent anatomy, no text, "
                        "no logo, no watermark"
                    )[:600]
                    stats["image_prompts_added"] += 1
                    changed = True
                if not str(shot.get("style_preset") or "").strip():
                    shot["style_preset"] = "真人电影"
                    changed = True

                existing_video = shot.get("video_generation")
                video = dict(existing_video) if isinstance(existing_video, dict) else {}
                original_video = dict(video)
                subject_motion = str(video.get("subject_motion") or "").strip()
                if not subject_motion:
                    subject_motion = str(video.get("motion_prompt") or scene).strip()
                    video["subject_motion"] = subject_motion
                image_prompt = str(shot.get("image_prompt") or "").strip()
                motion_context = subject_motion.lower()
                scene_context = scene.lower()
                motion_ready = any(
                    term in motion_context for term in LOCOMOTION_PROMPT_TERMS
                ) and not any(term in scene_context for term in CLOSE_FRAMING_TERMS)
                framing_suffix = f"; {LOCOMOTION_FRAMING_PROMPT}"
                legacy_suffix = f"; {LEGACY_LOCOMOTION_FRAMING_PROMPT}"
                if motion_ready and LOCOMOTION_FRAMING_PROMPT not in image_prompt:
                    image_prompt = image_prompt.replace(legacy_suffix, "")
                    shot["image_prompt"] = (
                        f"{image_prompt.rstrip(', ')}{framing_suffix}"
                    )[:1200]
                    stats["motion_framing_prompts_updated"] += 1
                    changed = True
                elif not motion_ready and (
                    framing_suffix in image_prompt or legacy_suffix in image_prompt
                ):
                    shot["image_prompt"] = image_prompt.replace(
                        framing_suffix, ""
                    ).replace(legacy_suffix, "")
                    stats["motion_framing_prompts_removed"] += 1
                    changed = True
                environment = shot.get("environment")
                environment = environment if isinstance(environment, dict) else {}
                if not str(video.get("environment_motion") or "").strip():
                    video["environment_motion"] = str(
                        environment.get("atmosphere") or ""
                    ).strip()
                characters = shot.get("characters")
                names = "、".join(
                    str(item.get("name") or "").strip()
                    for item in (characters if isinstance(characters, list) else [])
                    if isinstance(item, dict) and item.get("name")
                )
                identity = f"保持{names}的" if names else "保持人物"
                if not str(video.get("continuity_constraints") or "").strip():
                    video["continuity_constraints"] = (
                        f"{identity}脸型、年龄、发型、服装和道具一致；"
                        "保持人物站位、屏幕方向、光线和背景布局稳定"
                    )
                route_engine, route_strength, route_reason = _automatic_video_route(
                    shot,
                    subject_motion,
                )
                route_version = int(video.get("routing_version") or 0)
                route_locked = bool(video.get("routing_locked"))
                if (
                    prefer_ai_video
                    and not route_locked
                    and route_version < ROUTING_VERSION
                ):
                    if video.get("engine_profile") != route_engine:
                        stats["video_routes_updated"] += 1
                        if video.get("selected_video"):
                            video["selected_video"] = ""
                            video["manifest_file"] = ""
                            stats["video_selections_cleared"] += 1
                    video["engine_profile"] = route_engine
                    video["motion_strength"] = route_strength
                    video["routing_reason"] = route_reason
                    video["routing_version"] = ROUTING_VERSION
                else:
                    video.setdefault("engine_profile", "minimax_h3_fl2va")
                    video.setdefault("routing_reason", route_reason)
                if not str(video.get("negative_prompt") or "").strip():
                    video["negative_prompt"] = DEFAULT_VIDEO_NEGATIVE_PROMPT
                if not str(video.get("motion_prompt") or "").strip():
                    video["motion_prompt"] = "；".join(
                        part
                        for part in (
                            subject_motion,
                            str(video.get("environment_motion") or "").strip(),
                        )
                        if part
                    )
                end_prompt_version = int(video.get("end_frame_prompt_version") or 0)
                if end_prompt_version < END_FRAME_PROMPT_VERSION:
                    video["end_frame_prompt"] = _end_frame_prompt(
                        shot,
                        subject_motion,
                        str(video.get("continuity_constraints") or ""),
                    )
                    video["end_frame_prompt_version"] = END_FRAME_PROMPT_VERSION
                    stats["end_frame_prompts_added"] += 1
                video.setdefault("native_audio_mode", "ambience_sfx_music")
                video.setdefault(
                    "dialogue_prompt",
                    str(shot.get("dialogue") or "").strip(),
                )
                video.setdefault(
                    "sound_effect_prompt",
                    str(shot.get("sound_effect") or "").strip()
                    or DEFAULT_H3_SOUND_EFFECT_PROMPT,
                )
                video.setdefault("music_prompt", DEFAULT_H3_MUSIC_PROMPT)
                source_camera = str(shot.get("camera_movement") or "static")
                video.setdefault(
                    "camera_movement",
                    CAMERA_MOVEMENT_MAP.get(source_camera, "slow_push"),
                )
                video.setdefault("motion_strength", route_strength)
                video.setdefault("screen_direction", "auto")
                source_transition = str(shot.get("transition") or "cut")
                video.setdefault(
                    "transition_out",
                    TRANSITION_MAP.get(source_transition, "cut"),
                )
                video.setdefault("transition_frames", 8)
                video.setdefault("handle_frames", 8)
                video.setdefault("candidate_count", 1)
                video.setdefault(
                    "duration_seconds",
                    max(
                        1.0,
                        min(float(shot.get("duration_seconds") or 3.0), 15.0),
                    ),
                )
                if video != original_video:
                    stats["video_prompts_added"] += 1
                    changed = True
                shot["video_generation"] = video
                explicit_only = (
                    str(
                        value.get("artifact_binding_policy")
                        or "legacy_fallback"
                    )
                    == "explicit_only"
                )
                source = (
                    self._existing_project_file(
                        root,
                        str(video.get("source_image") or ""),
                    )
                    if explicit_only
                    else self._find_shot_source(
                        root,
                        episode_number,
                        shot_number,
                        str(video.get("source_image") or ""),
                    )
                )
                if source:
                    stats["source_images_ready"] += 1
            if changed:
                atomic_write_json(path, value)
        return stats

    def save_video_settings(
        self,
        slug: str,
        episode_number: int,
        shot_number: int,
        *,
        engine_profile: str = "minimax_h3_fl2va",
        subject_motion: str = "",
        environment_motion: str = "",
        continuity_constraints: str = "",
        negative_prompt: str = "",
        end_frame_prompt: str = "",
        native_audio_mode: str = "ambience_sfx_music",
        dialogue_prompt: str = "",
        sound_effect_prompt: str = "",
        music_prompt: str = "",
        motion_prompt: str,
        camera_movement: str,
        motion_strength: str = "low",
        screen_direction: str = "auto",
        transition_out: str = "cut",
        transition_frames: int = 8,
        handle_frames: int = 8,
        candidate_count: int = 1,
        duration_seconds: float,
    ) -> Path:
        path = self.episode_path(slug, episode_number)
        value = self._read_json(path)
        shot = self._find_shot(value, shot_number)
        video = shot.get("video_generation") or {}
        video["engine_profile"] = engine_profile.strip() or "minimax_h3_fl2va"
        video["subject_motion"] = subject_motion.strip()
        video["environment_motion"] = environment_motion.strip()
        video["continuity_constraints"] = continuity_constraints.strip()
        video["negative_prompt"] = negative_prompt.strip()
        if end_frame_prompt.strip():
            video["end_frame_prompt"] = end_frame_prompt.strip()
        video["native_audio_mode"] = (
            native_audio_mode
            if native_audio_mode
            in {"off", "ambience_sfx_music", "native_full"}
            else "ambience_sfx_music"
        )
        video["dialogue_prompt"] = dialogue_prompt.strip()
        video["sound_effect_prompt"] = (
            sound_effect_prompt.strip() or DEFAULT_H3_SOUND_EFFECT_PROMPT
        )
        video["music_prompt"] = music_prompt.strip() or DEFAULT_H3_MUSIC_PROMPT
        video["motion_prompt"] = motion_prompt.strip()
        video["camera_movement"] = camera_movement.strip() or "auto"
        video["motion_strength"] = motion_strength.strip() or "low"
        video["screen_direction"] = screen_direction.strip() or "auto"
        video["transition_out"] = transition_out.strip() or "cut"
        video["transition_frames"] = max(0, min(int(transition_frames), 48))
        video["handle_frames"] = max(0, min(int(handle_frames), 48))
        video["candidate_count"] = max(1, min(int(candidate_count), 4))
        video["duration_seconds"] = max(1.0, min(float(duration_seconds), 15.0))
        shot["video_generation"] = video
        atomic_write_json(path, value)
        return path

    def save_audio_settings(
        self,
        slug: str,
        episode_number: int,
        shot_number: int,
        *,
        mode: str = "auto_narration",
        speaker: str = "旁白",
        text: str = "",
        engine: str = "edge_tts",
        voice_id: str = "zh-CN-YunyangNeural",
        reference_audio: str | Path = "",
        reference_text: str = "",
        instruct_text: str = "",
        fallback_to_edge: bool = True,
        rate: str = "+5%",
        volume: str = "+0%",
        pitch: str = "-5Hz",
        subtitle_enabled: bool = True,
        preserve_source_audio: bool = True,
        source_audio_gain_db: float = -6.0,
        ducking_gain_db: float = -12.0,
    ) -> Path:
        path = self.episode_path(slug, episode_number)
        value = self._read_json(path)
        shot = self._find_shot(value, shot_number)
        audio = shot.get("audio_generation")
        audio = dict(audio) if isinstance(audio, dict) else {}
        stored_reference = self._store_voice_reference(
            root=(self.projects_dir / slug).resolve(),
            speaker=speaker,
            source=reference_audio,
        )
        audio.update(
            {
                "enabled": mode != "mute",
                "mode": mode,
                "speaker": speaker.strip() or "旁白",
                "text": text.strip(),
                "engine": engine.strip() or "edge_tts",
                "voice_id": voice_id.strip() or "zh-CN-YunyangNeural",
                "reference_audio": (
                    stored_reference
                    or str(audio.get("reference_audio") or "")
                ),
                "reference_text": reference_text.strip(),
                "instruct_text": instruct_text.strip(),
                "fallback_to_edge": bool(fallback_to_edge),
                "rate": rate.strip() or "+5%",
                "volume": volume.strip() or "+0%",
                "pitch": pitch.strip() or "-5Hz",
                "subtitle_enabled": bool(subtitle_enabled),
                "preserve_source_audio": bool(preserve_source_audio),
                "source_audio_gain_db": max(
                    -30.0,
                    min(float(source_audio_gain_db), 6.0),
                ),
                "ducking_gain_db": max(
                    -30.0,
                    min(float(ducking_gain_db), 0.0),
                ),
            }
        )
        shot["audio_generation"] = audio
        if mode == "dialogue":
            shot["dialogue"] = text.strip()
        atomic_write_json(path, value)
        return path

    def optimize_audio_timeline(
        self,
        slug: str,
        episode_number: int,
    ) -> AudioTimingSummary:
        """Estimate speech first and persist video durations before rendering."""

        path = self.episode_path(slug, episode_number)
        value = self._read_json(path)
        summary = optimize_episode_audio_timing(value)
        atomic_write_json(path, value)
        return summary

    def save_lip_sync_settings(
        self,
        slug: str,
        episode_number: int,
        shot_number: int,
        *,
        enabled: bool = False,
        engine: str = "latentsync_1_6",
        target_character: str = "",
        mode: str = "speaker_tracking",
        inference_steps: int = 20,
        guidance_scale: float = 1.5,
    ) -> Path:
        path = self.episode_path(slug, episode_number)
        value = self._read_json(path)
        shot = self._find_shot(value, shot_number)
        lip_sync = shot.get("lip_sync")
        lip_sync = dict(lip_sync) if isinstance(lip_sync, dict) else {}
        previous_target = str(lip_sync.get("target_character") or "")
        previous_mode = str(lip_sync.get("mode") or "")
        lip_sync.update(
            {
                "enabled": bool(enabled),
                "engine": (
                    engine
                    if engine in {"latentsync_1_6", "latentsync_1_5"}
                    else "latentsync_1_6"
                ),
                "target_character": target_character.strip(),
                "mode": (
                    mode
                    if mode
                    in {
                        "auto_single_face",
                        "speaker_tracking",
                        "manual_anchor",
                    }
                    else "speaker_tracking"
                ),
                "inference_steps": max(10, min(int(inference_steps), 50)),
                "guidance_scale": max(
                    1.0,
                    min(float(guidance_scale), 3.0),
                ),
            }
        )
        if not enabled:
            lip_sync["status"] = "disabled"
        elif (
            lip_sync["mode"] == "manual_anchor"
            and not lip_sync.get("target_face_anchor")
        ):
            lip_sync["status"] = "needs_face_selection"
        elif (
            previous_target != lip_sync["target_character"]
            or previous_mode != lip_sync["mode"]
            or str(lip_sync.get("status") or "") == "disabled"
        ):
            lip_sync["status"] = "pending"
            lip_sync["output_file"] = ""
            lip_sync["sync_score"] = 0.0
            lip_sync["error"] = ""
        shot["lip_sync"] = lip_sync
        atomic_write_json(path, value)
        return path

    @staticmethod
    def _store_voice_reference(
        *,
        root: Path,
        speaker: str,
        source: str | Path,
    ) -> str:
        value = str(source or "").strip()
        if not value:
            return ""
        candidate = Path(value)
        if not candidate.is_absolute():
            candidate = root / candidate
        candidate = candidate.resolve()
        if not candidate.is_file():
            raise FileNotFoundError(f"参考音频不存在：{candidate}")
        try:
            return candidate.relative_to(root).as_posix()
        except ValueError:
            pass
        safe_speaker = re.sub(
            r"[^A-Za-z0-9_\-\u3400-\u9fff]+",
            "_",
            speaker.strip() or "speaker",
        ).strip("_")
        suffix = candidate.suffix.lower()
        if suffix not in {".wav", ".mp3", ".flac", ".m4a", ".ogg"}:
            raise ValueError("参考音频仅支持 WAV、MP3、FLAC、M4A 或 OGG")
        destination = (
            root
            / "production"
            / "audio"
            / "voice_refs"
            / f"{safe_speaker or 'speaker'}{suffix}"
        )
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(candidate, destination)
        return destination.relative_to(root).as_posix()

    def save_shot_audio_result(
        self,
        slug: str,
        episode_number: int,
        shot_number: int,
        audio_path: Path,
        subtitle_path: Path,
        manifest_path: Path,
        *,
        audio_duration_seconds: float = 0.0,
        timeline_duration_seconds: float = 0.0,
    ) -> Path:
        root = (self.projects_dir / slug).resolve()
        audio_file = self._project_relative_file(root, audio_path, "镜头配音")
        subtitle_file = self._project_relative_file(root, subtitle_path, "镜头字幕")
        manifest_file = self._project_relative_file(root, manifest_path, "配音清单")
        path = self.episode_path(slug, episode_number)
        value = self._read_json(path)
        shot = self._find_shot(value, shot_number)
        audio = shot.get("audio_generation")
        audio = dict(audio) if isinstance(audio, dict) else {}
        audio.update(
            {
                "audio_file": audio_file.relative_to(root).as_posix(),
                "subtitle_file": subtitle_file.relative_to(root).as_posix(),
                "manifest_file": manifest_file.relative_to(root).as_posix(),
                "estimated_duration_seconds": max(
                    0.0,
                    float(audio_duration_seconds),
                ),
                "planned_timeline_duration_seconds": max(
                    0.0,
                    float(timeline_duration_seconds),
                ),
            }
        )
        video = shot.get("video_generation")
        video = dict(video) if isinstance(video, dict) else {}
        current_duration = float(
            video.get("duration_seconds")
            or shot.get("duration_seconds")
            or 3.0
        )
        required_duration = max(
            current_duration,
            float(timeline_duration_seconds),
        )
        if required_duration > 15.0:
            audio["timing_status"] = "needs_split"
            audio["recommended_segments"] = max(
                2,
                math.ceil(required_duration / 5.0),
            )
        elif required_duration > current_duration + 0.1:
            audio["timing_status"] = "needs_regeneration"
        else:
            audio["timing_status"] = "ready"
        target_duration = min(15.0, required_duration)
        shot["duration_seconds"] = round(target_duration, 2)
        video["duration_seconds"] = round(target_duration, 2)
        shot["audio_generation"] = audio
        shot["video_generation"] = video
        atomic_write_json(path, value)
        return path

    def save_episode_dubbing_result(
        self,
        slug: str,
        episode_number: int,
        video_path: Path,
        subtitle_path: Path,
        manifest_path: Path,
    ) -> Path:
        root = (self.projects_dir / slug).resolve()
        video_file = self._project_relative_file(root, video_path, "带声成片")
        subtitle_file = self._project_relative_file(root, subtitle_path, "整集字幕")
        manifest_file = self._project_relative_file(root, manifest_path, "配音清单")
        path = self.episode_path(slug, episode_number)
        value = self._read_json(path)
        value["dubbing"] = {
            "output_file": video_file.relative_to(root).as_posix(),
            "subtitle_file": subtitle_file.relative_to(root).as_posix(),
            "manifest_file": manifest_file.relative_to(root).as_posix(),
        }
        atomic_write_json(path, value)
        return path

    def save_lip_sync_result(
        self,
        slug: str,
        episode_number: int,
        shot_number: int,
        video_path: Path,
        audio_path: Path,
        source_video: Path,
        manifest_path: Path,
        *,
        elapsed_seconds: float = 0.0,
        face_match_similarity: float = 0.0,
        select: bool = True,
    ) -> Path:
        """Persist one successful lip-sync artifact and optionally select it."""

        root = (self.projects_dir / slug).resolve()
        video_file = self._project_relative_file(root, video_path, "口型视频")
        audio_file = self._project_relative_file(root, audio_path, "口型配音")
        source_file = self._project_relative_file(root, source_video, "口型源视频")
        manifest_file = self._project_relative_file(root, manifest_path, "口型清单")
        path = self.episode_path(slug, episode_number)
        value = self._read_json(path)
        shot = self._find_shot(value, shot_number)
        lip_sync = shot.get("lip_sync")
        lip_sync = dict(lip_sync) if isinstance(lip_sync, dict) else {}
        lip_sync.update(
            {
                "enabled": True,
                "engine": "latentsync_1_6",
                "status": "succeeded",
                "source_video": source_file.relative_to(root).as_posix(),
                "audio_file": audio_file.relative_to(root).as_posix(),
                "output_file": video_file.relative_to(root).as_posix(),
                "manifest_file": manifest_file.relative_to(root).as_posix(),
                "generated_at": datetime.now().astimezone().isoformat(
                    timespec="seconds"
                ),
                "elapsed_seconds": max(0.0, float(elapsed_seconds)),
                "face_match_similarity": max(
                    0.0,
                    float(face_match_similarity),
                ),
                "sync_score": max(0.0, float(face_match_similarity)),
                "error": "",
            }
        )
        shot["lip_sync"] = lip_sync
        audio_generation = shot.get("audio_generation")
        audio_generation = (
            dict(audio_generation)
            if isinstance(audio_generation, dict)
            else {}
        )
        audio_generation["audio_file"] = audio_file.relative_to(root).as_posix()
        shot["audio_generation"] = audio_generation
        if select:
            video = shot.get("video_generation")
            video = dict(video) if isinstance(video, dict) else {}
            video["selected_video"] = video_file.relative_to(root).as_posix()
            video["manifest_file"] = manifest_file.relative_to(root).as_posix()
            shot["video_generation"] = video
        atomic_write_json(path, value)
        return path

    def save_lip_sync_failure(
        self,
        slug: str,
        episode_number: int,
        shot_number: int,
        detail: str,
    ) -> Path:
        path = self.episode_path(slug, episode_number)
        value = self._read_json(path)
        shot = self._find_shot(value, shot_number)
        lip_sync = shot.get("lip_sync")
        lip_sync = dict(lip_sync) if isinstance(lip_sync, dict) else {}
        lip_sync["status"] = "failed"
        lip_sync["error"] = detail[:1000]
        shot["lip_sync"] = lip_sync
        atomic_write_json(path, value)
        return path

    def set_shot_source_image(
        self,
        slug: str,
        episode_number: int,
        shot_number: int,
        source_path: Path,
    ) -> Path:
        return self._set_shot_video_image(
            slug,
            episode_number,
            shot_number,
            source_path,
            field_name="source_image",
            filename_suffix="",
            label="首帧",
        )

    def select_shot_image_candidate(
        self,
        slug: str,
        episode_number: int,
        shot_number: int,
        source_path: Path,
    ) -> Path:
        """Select a recorded candidate while preserving its generation lineage."""

        root = (self.projects_dir / slug).resolve()
        source_file = self._project_relative_file(root, source_path, "分镜候选图")
        selected_path = self.set_shot_source_image(
            slug,
            episode_number,
            shot_number,
            source_file,
        )
        episode_path = self.episode_path(slug, episode_number)
        value = self._read_json(episode_path)
        shot = self._find_shot(value, shot_number)
        generation = shot.get("image_generation")
        generation = dict(generation) if isinstance(generation, dict) else {}
        relative_source = source_file.relative_to(root).as_posix()
        generation["selected_image"] = selected_path.relative_to(root).as_posix()
        generation["selected_source"] = relative_source
        for candidate in generation.get("candidates") or []:
            if not isinstance(candidate, dict):
                continue
            if str(candidate.get("file") or "") != relative_source:
                continue
            generation["manifest"] = str(candidate.get("manifest") or "")
            break
        shot["image_generation"] = generation
        atomic_write_json(episode_path, value)
        return selected_path

    def archive_shot_source_candidate(
        self,
        slug: str,
        episode_number: int,
        shot_number: int,
        source_path: Path,
    ) -> Path:
        """Ensure the current keyframe has an immutable history copy."""

        root = (self.projects_dir / slug).resolve()
        source_file = self._project_relative_file(root, source_path, "当前分镜首帧")
        episode_path = self.episode_path(slug, episode_number)
        value = self._read_json(episode_path)
        shot = self._find_shot(value, shot_number)
        generation = shot.get("image_generation")
        generation = dict(generation) if isinstance(generation, dict) else {}

        selected_source = self._existing_project_file(
            root,
            str(generation.get("selected_source") or ""),
        )
        if selected_source:
            return selected_source

        relative_source = source_file.relative_to(root).as_posix()
        for candidate in generation.get("candidates") or []:
            if not isinstance(candidate, dict):
                continue
            if str(candidate.get("file") or "") == relative_source:
                return source_file

        timestamp = datetime.now().astimezone()
        archive_dir = (
            root
            / "production"
            / "shots"
            / f"episode_{episode_number:03d}"
            / "revisions"
            / "history"
        )
        archive_dir.mkdir(parents=True, exist_ok=True)
        stem = (
            f"shot_{shot_number:03d}_before_revision_"
            f"{timestamp:%Y%m%d_%H%M%S_%f}"
        )
        archived_image = archive_dir / f"{stem}{source_file.suffix.lower()}"
        archived_manifest = archive_dir / f"{stem}.json"
        shutil.copy2(source_file, archived_image)
        generated_at = timestamp.isoformat(timespec="seconds")
        metadata = {
            "model_id": "archived_source",
            "model_label": "修改前原图",
            "generated_at": generated_at,
            "prompt": str(shot.get("image_prompt") or ""),
        }
        atomic_write_json(
            archived_manifest,
            {
                "schema_version": "1.0",
                "kind": "shot_source_archive",
                "source_image": relative_source,
                "generated_at": generated_at,
                "images": [{"file": archived_image.name, **metadata}],
            },
        )
        self.save_shot_image_result(
            slug,
            episode_number,
            shot_number,
            archived_image,
            archived_manifest,
            metadata,
            select=False,
        )
        value = self._read_json(episode_path)
        shot = self._find_shot(value, shot_number)
        generation = shot.get("image_generation")
        generation = dict(generation) if isinstance(generation, dict) else {}
        generation["selected_image"] = relative_source
        generation["selected_source"] = archived_image.relative_to(root).as_posix()
        generation["manifest"] = archived_manifest.relative_to(root).as_posix()
        shot["image_generation"] = generation
        atomic_write_json(episode_path, value)
        return archived_image

    def set_shot_end_image(
        self,
        slug: str,
        episode_number: int,
        shot_number: int,
        source_path: Path,
    ) -> Path:
        return self._set_shot_video_image(
            slug,
            episode_number,
            shot_number,
            source_path,
            field_name="end_image",
            filename_suffix="_end",
            label="结束帧",
        )

    def save_shot_image_result(
        self,
        slug: str,
        episode_number: int,
        shot_number: int,
        image_path: Path,
        manifest_path: Path,
        metadata: dict[str, Any],
        *,
        select: bool = True,
    ) -> Path:
        """Register a generated keyframe and optionally select it as video input."""

        root = (self.projects_dir / slug).resolve()
        image_file = self._project_relative_file(root, image_path, "分镜首帧")
        manifest_file = self._project_relative_file(root, manifest_path, "生图清单")
        selected_path = (
            self.set_shot_source_image(
                slug,
                episode_number,
                shot_number,
                image_file,
            )
            if select
            else image_file
        )
        episode_path = self.episode_path(slug, episode_number)
        value = self._read_json(episode_path)
        shot = self._find_shot(value, shot_number)
        generation = shot.get("image_generation")
        generation = dict(generation) if isinstance(generation, dict) else {}
        relative_image = image_file.relative_to(root).as_posix()
        relative_manifest = manifest_file.relative_to(root).as_posix()
        candidates = [
            item
            for item in (generation.get("candidates") or [])
            if isinstance(item, dict) and item.get("file") != relative_image
        ]
        candidates.append(
            {
                "file": relative_image,
                "manifest": relative_manifest,
                "model_id": str(metadata.get("model_id") or ""),
                "model_label": str(metadata.get("model_label") or ""),
                "model_file": str(metadata.get("model_file") or ""),
                "generated_at": str(metadata.get("generated_at") or ""),
                "seed": metadata.get("seed"),
                "prompt": str(metadata.get("prompt") or ""),
                "continuity_group": str(
                    metadata.get("continuity_group") or ""
                ),
                "reference_mode": str(metadata.get("reference_mode") or ""),
                "reference_shot_number": int(
                    metadata.get("reference_shot_number") or 0
                ),
                "reference_image": str(metadata.get("reference_image") or ""),
                "reference_denoise": float(
                    metadata.get("reference_denoise") or 1.0
                ),
            }
        )
        generation["candidates"] = candidates
        if select:
            generation["selected_image"] = selected_path.relative_to(root).as_posix()
            generation["selected_source"] = relative_image
            generation["manifest"] = relative_manifest
        shot["image_generation"] = generation
        atomic_write_json(episode_path, value)
        return selected_path

    def set_shot_image_qc(
        self,
        slug: str,
        episode_number: int,
        shot_number: int,
        status: str,
        note: str = "",
    ) -> Path:
        """Approve or reject the exact keyframe currently selected for a shot."""

        normalized = status.strip().lower()
        if normalized not in {"approved", "rejected", "pending"}:
            raise ValueError(f"不支持的首帧质检状态：{status}")
        root = (self.projects_dir / slug).resolve()
        path = self.episode_path(slug, episode_number)
        value = self._read_json(path)
        shot = self._find_shot(value, shot_number)
        video = shot.get("video_generation")
        video = dict(video) if isinstance(video, dict) else {}
        source = self._existing_project_file(
            root,
            str(video.get("source_image") or ""),
        )
        if not source:
            raise ValueError("当前镜头没有可质检的首帧")
        generation = shot.get("image_generation")
        generation = dict(generation) if isinstance(generation, dict) else {}
        generation.update(
            {
                "qc_status": normalized,
                "qc_note": note.strip()[:500],
                "qc_checked_at": datetime.now().astimezone().isoformat(
                    timespec="seconds"
                ),
                "qc_selected_image": source.relative_to(root).as_posix(),
            }
        )
        shot["image_generation"] = generation
        atomic_write_json(path, value)
        return path

    def _set_shot_video_image(
        self,
        slug: str,
        episode_number: int,
        shot_number: int,
        source_path: Path,
        *,
        field_name: str,
        filename_suffix: str,
        label: str,
    ) -> Path:
        root = (self.projects_dir / slug).resolve()
        source = Path(source_path).resolve()
        if not source.is_file():
            raise FileNotFoundError(f"{label}图片不存在：{source}")
        if source.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"}:
            raise ValueError(f"{label}只支持 PNG、JPG、JPEG 或 WebP")
        destination = (
            root
            / "production"
            / "video_inputs"
            / f"episode_{episode_number:03d}"
            / f"shot_{shot_number:03d}{filename_suffix}{source.suffix.lower()}"
        )
        destination.parent.mkdir(parents=True, exist_ok=True)
        if source != destination.resolve():
            shutil.copy2(source, destination)
        path = self.episode_path(slug, episode_number)
        value = self._read_json(path)
        shot = self._find_shot(value, shot_number)
        video = shot.get("video_generation") or {}
        video[field_name] = destination.relative_to(root).as_posix()
        shot["video_generation"] = video
        if field_name == "source_image":
            generation = shot.get("image_generation")
            generation = (
                dict(generation) if isinstance(generation, dict) else {}
            )
            generation.update(
                {
                    "qc_status": "pending",
                    "qc_note": "",
                    "qc_checked_at": "",
                    "qc_selected_image": destination.relative_to(root).as_posix(),
                    "selected_image": destination.relative_to(root).as_posix(),
                    "selected_source": "",
                    "manifest": "",
                }
            )
            shot["image_generation"] = generation
        atomic_write_json(path, value)
        return destination

    def save_shot_video_result(
        self,
        slug: str,
        episode_number: int,
        shot_number: int,
        video_path: Path,
        manifest_path: Path,
        *,
        select: bool = True,
    ) -> Path:
        root = (self.projects_dir / slug).resolve()
        video_file = self._project_relative_file(root, video_path, "镜头视频")
        manifest_file = self._project_relative_file(root, manifest_path, "视频清单")
        path = self.episode_path(slug, episode_number)
        value = self._read_json(path)
        shot = self._find_shot(value, shot_number)
        video = shot.get("video_generation") or {}
        relative_video = video_file.relative_to(root).as_posix()
        relative_manifest = manifest_file.relative_to(root).as_posix()
        candidates = [
            item
            for item in (video.get("candidates") or [])
            if isinstance(item, dict) and item.get("file") != relative_video
        ]
        candidates.append(
            {
                "file": relative_video,
                "manifest": relative_manifest,
            }
        )
        video["candidates"] = candidates
        if select:
            video["selected_video"] = relative_video
            video["manifest"] = relative_manifest
        shot["video_generation"] = video
        atomic_write_json(path, value)
        return path

    def process_novel(
        self,
        slug: str,
        source_path: Path,
        *,
        analysis_limit: int = 3,
        progress_callback: Callable[[int, str], None] | None = None,
    ) -> dict[str, Any]:
        root = (self.projects_dir / slug).resolve()

        def report(percent: int, message: str) -> None:
            if progress_callback:
                progress_callback(max(0, min(percent, 100)), message)

        report(3, "正在导入并切分小说")
        init_db(root / "database" / "world.db")
        imported = import_novel(source_path, root)
        import_stats = persist_import(imported)
        report(15, f"已切分 {len(imported.chapters)} 章")

        def compile_progress(done: int, total: int, message: str) -> None:
            ratio = done / total if total else 0
            report(15 + int(ratio * 55), message)

        compile_stats = run_compile_novel(
            limit=analysis_limit,
            project_root=root,
            progress_callback=compile_progress,
        )
        report(72, "人物与事件提取完成，正在生成分镜")

        def storyboard_progress(done: int, total: int, message: str) -> None:
            ratio = done / total if total else 0
            report(72 + int(ratio * 27), message)

        episodes = generate_storyboard(
            root,
            limit=analysis_limit,
            progress_callback=storyboard_progress,
        )
        automation = self.prepare_shot_automation(slug)
        report(100, f"自动处理完成：{len(episodes)} 集分镜")
        return {
            "source": imported.source.original_name,
            "chapters": len(imported.chapters),
            "import": import_stats,
            "compile": compile_stats,
            "episodes": len(episodes),
            "shots": sum(len(episode.shots) for episode in episodes),
            "automation": automation,
        }

    def reprocess_novel(
        self,
        slug: str,
        *,
        analysis_limit: int = 3,
        progress_callback: Callable[[int, str], None] | None = None,
    ) -> dict[str, Any]:
        """Force fresh analysis and storyboard generation for imported chapters."""
        root = (self.projects_dir / slug).resolve()

        def report(percent: int, message: str) -> None:
            if progress_callback:
                progress_callback(max(0, min(percent, 100)), message)

        chapter_files = sorted((root / "novel" / "chapters").glob("ch_*.json"))
        if not chapter_files:
            raise ValueError("当前项目没有已导入章节，请先导入小说")

        report(3, "正在备份现有分镜")
        episode_dir = root / "production" / "episodes"
        episode_files = sorted(episode_dir.glob("episode_*.json"))
        backup_dir: Path | None = None
        if episode_files:
            backup_dir = (
                root
                / "production"
                / "backups"
                / f"reprocess_{time.strftime('%Y%m%d_%H%M%S')}"
                / "episodes"
            )
            backup_dir.mkdir(parents=True, exist_ok=True)
            for path in episode_files:
                shutil.copy2(path, backup_dir / path.name)

        init_db(root / "database" / "world.db")
        target_count = (
            min(analysis_limit, len(chapter_files))
            if analysis_limit > 0
            else len(chapter_files)
        )
        report(8, f"将强制重新分析 {target_count} 章")

        def compile_progress(done: int, total: int, message: str) -> None:
            ratio = done / total if total else 0
            report(8 + int(ratio * 62), message)

        compile_stats = run_compile_novel(
            limit=analysis_limit,
            force=True,
            project_root=root,
            progress_callback=compile_progress,
        )
        report(72, "最新分析已完成，正在重新生成分镜")

        def storyboard_progress(done: int, total: int, message: str) -> None:
            ratio = done / total if total else 0
            report(72 + int(ratio * 27), message)

        episodes = generate_storyboard(
            root,
            limit=analysis_limit,
            progress_callback=storyboard_progress,
        )
        automation = self.prepare_shot_automation(slug)
        report(100, f"重新处理完成：{len(episodes)} 集分镜")
        return {
            "chapters": target_count,
            "compile": compile_stats,
            "episodes": len(episodes),
            "shots": sum(len(episode.shots) for episode in episodes),
            "backup_dir": backup_dir,
            "automation": automation,
        }

    def load_cast_selections(self, slug: str) -> dict[str, str]:
        root = (self.projects_dir / slug).resolve()
        value = self._read_json(root / "production" / "cast_selection.json")
        selections = value.get("selections") or {}
        return (
            {str(name): str(path) for name, path in selections.items()}
            if isinstance(selections, dict)
            else {}
        )

    def select_character_image(
        self,
        slug: str,
        character: str,
        image_path: Path,
    ) -> Path:
        root = (self.projects_dir / slug).resolve()
        candidate = image_path.resolve()
        try:
            relative = candidate.relative_to(root)
        except ValueError as exc:
            raise ValueError("候选图片不在当前项目目录内") from exc
        if not candidate.is_file():
            raise FileNotFoundError(f"候选图片不存在: {candidate}")
        selections = self.load_cast_selections(slug)
        selections[character] = relative.as_posix()
        destination = root / "production" / "cast_selection.json"
        atomic_write_json(
            destination,
            {
                "schema_version": "1.0",
                "selections": selections,
            },
        )
        return destination

    def clear_character_selection(self, slug: str, character: str) -> Path:
        """Unlock a cast selection without deleting any generated image."""
        root = (self.projects_dir / slug).resolve()
        selections = self.load_cast_selections(slug)
        selections.pop(character, None)
        destination = root / "production" / "cast_selection.json"
        atomic_write_json(
            destination,
            {
                "schema_version": "1.0",
                "selections": selections,
            },
        )
        return destination

    def load_jobs(self, slug: str, limit: int = 100) -> list[JobSnapshot]:
        db_path = self.projects_dir / slug / "database" / "world.db"
        if not db_path.exists():
            return []
        with sqlite3.connect(db_path) as connection:
            try:
                rows = connection.execute(
                    """
                    SELECT id, job_type, status, progress, error_message, updated_at
                    FROM jobs
                    ORDER BY updated_at DESC
                    LIMIT ?
                    """,
                    (limit,),
                ).fetchall()
            except sqlite3.Error:
                return []
        return [
            JobSnapshot(
                job_id=str(row[0]),
                job_type=str(row[1]),
                status=str(row[2]),
                progress=float(row[3] or 0),
                error=str(row[4] or ""),
                updated_at=str(row[5] or ""),
            )
            for row in rows
        ]

    def episode_path(self, slug: str, episode_number: int = 1) -> Path:
        return (
            self.projects_dir
            / slug
            / "production"
            / "episodes"
            / f"episode_{episode_number:03d}.json"
        )

    def local_krea_output_dir(self, slug: str, run_name: str) -> Path:
        return self.projects_dir / slug / "outputs" / "server_test" / "krea_app" / run_name

    def local_image_output_dir(self, slug: str, run_name: str) -> Path:
        return self.projects_dir / slug / "outputs" / "server_test" / "image_app" / run_name

    def episode_video_paths(self, slug: str, episode_number: int) -> list[Path]:
        episodes = {
            episode.number: episode for episode in self.load_episodes(slug)
        }
        episode = episodes.get(episode_number)
        if not episode:
            return []
        return [
            shot.video_path
            for shot in episode.shots
            if shot.video_path and shot.video_path.is_file()
        ]

    def latest_episode_preview(
        self,
        slug: str,
        episode_number: int,
    ) -> Path | None:
        root = (self.projects_dir / slug).resolve()
        directory = (
            root / "production" / "videos" / f"episode_{episode_number:03d}"
        )
        candidates = list(directory.glob(f"episode_{episode_number:03d}_preview_*.mp4"))
        return max(candidates, key=lambda path: path.stat().st_mtime) if candidates else None

    @staticmethod
    def _count(
        connection: sqlite3.Connection,
        table: str,
        where: str = "",
    ) -> int:
        suffix = f" WHERE {where}" if where else ""
        try:
            value = connection.execute(f"SELECT COUNT(*) FROM {table}{suffix}").fetchone()
        except sqlite3.Error:
            return 0
        return int(value[0]) if value else 0

    @staticmethod
    def _count_distinct(
        connection: sqlite3.Connection,
        table: str,
        column: str,
        where: str = "",
    ) -> int:
        suffix = f" WHERE {where}" if where else ""
        try:
            value = connection.execute(
                f"SELECT COUNT(DISTINCT {column}) FROM {table}{suffix}"
            ).fetchone()
        except sqlite3.Error:
            return 0
        return int(value[0]) if value else 0

    @staticmethod
    def _read_json(path: Path) -> dict[str, Any]:
        if not path.exists():
            return {}
        value = json.loads(path.read_text(encoding="utf-8-sig"))
        return value if isinstance(value, dict) else {}

    def _find_character_images(self, root: Path, character: str) -> list[ImageSnapshot]:
        found: list[ImageSnapshot] = []
        outputs = root / "outputs"
        for manifest_path in outputs.rglob("manifest.json"):
            manifest = self._read_json(manifest_path)
            manifest_model = str(manifest.get("model", ""))
            models = {
                str(item.get("id")): item
                for item in (manifest.get("models") or [])
                if isinstance(item, dict) and item.get("id")
            }
            for item in manifest.get("images") or []:
                if not isinstance(item, dict) or item.get("character") != character:
                    continue
                candidate = manifest_path.parent / str(item.get("file", ""))
                if candidate.is_file():
                    model_id = str(item.get("model_id") or "")
                    model_meta = models.get(model_id) or {}
                    model_file = str(
                        item.get("model_file")
                        or model_meta.get("file")
                        or manifest_model
                    )
                    model_name = str(
                        item.get("model_label")
                        or model_meta.get("label")
                        or image_model_label(model_id or model_file)
                    )
                    supported = any(
                        marker in f"{model_id} {model_file} {model_name}".lower()
                        for marker in ("krea", "juggernaut", "kontext")
                    )
                    if not supported:
                        continue
                    generated_at = self._display_timestamp(
                        str(item.get("generated_at") or manifest.get("generated_at") or ""),
                        candidate,
                    )
                    found.append(
                        ImageSnapshot(
                            path=candidate,
                            model_id=model_id or model_file,
                            model_label=image_model_label(model_name),
                            generated_at=generated_at,
                            layout_label=str(
                                item.get("layout_label")
                                or manifest.get("layout_label")
                                or "单人定妆照"
                            ),
                        )
                    )
        unique: dict[Path, ImageSnapshot] = {}
        for image in found:
            unique[image.path.resolve()] = image
        return sorted(
            unique.values(),
            key=lambda image: image.path.stat().st_mtime,
            reverse=True,
        )

    def _shot_snapshot(
        self,
        root: Path,
        episode_number: int,
        item: dict[str, Any],
        fallback_number: int,
        *,
        allow_legacy_artifacts: bool = True,
    ) -> ShotSnapshot:
        number = int(item.get("shot_number") or fallback_number)
        video = item.get("video_generation") or {}
        if not isinstance(video, dict):
            video = {}
        audio = item.get("audio_generation") or {}
        if not isinstance(audio, dict):
            audio = {}
        continuity = item.get("continuity_plan") or {}
        if not isinstance(continuity, dict):
            continuity = {}
        lip_sync = item.get("lip_sync") or {}
        if not isinstance(lip_sync, dict):
            lip_sync = {}
        generation = item.get("image_generation") or {}
        if not isinstance(generation, dict):
            generation = {}
        source_image = (
            self._find_shot_source(
                root,
                episode_number,
                number,
                str(video.get("source_image") or ""),
            )
            if allow_legacy_artifacts
            else self._existing_project_file(
                root,
                str(video.get("source_image") or ""),
            )
        )
        image_candidates: list[ImageSnapshot] = []
        for candidate_meta in generation.get("candidates") or []:
            if not isinstance(candidate_meta, dict):
                continue
            candidate_path = self._existing_project_file(
                root,
                str(candidate_meta.get("file") or ""),
            )
            if not candidate_path:
                continue
            model_id = str(candidate_meta.get("model_id") or "")
            model_label = image_model_label(
                str(
                    candidate_meta.get("model_label")
                    or candidate_meta.get("model_file")
                    or model_id
                )
            )
            image_candidates.append(
                ImageSnapshot(
                    path=candidate_path,
                    model_id=model_id,
                    model_label=model_label,
                    generated_at=self._display_timestamp(
                        str(candidate_meta.get("generated_at") or ""),
                        candidate_path,
                    ),
                    layout_label="分镜首帧版本",
                )
            )
        if source_image and all(
            item.path.resolve() != source_image.resolve()
            for item in image_candidates
        ):
            image_candidates.insert(
                0,
                ImageSnapshot(
                    path=source_image,
                    model_id="selected",
                    model_label="当前首帧",
                    generated_at=self._display_timestamp("", source_image),
                    layout_label="当前使用版本",
                ),
            )
        image_qc_status = str(generation.get("qc_status") or "").lower()
        if not source_image:
            image_qc_status = "missing"
        elif image_qc_status not in {"approved", "rejected", "pending"}:
            image_qc_status = "pending"
        approved_image = str(generation.get("qc_selected_image") or "")
        current_image = (
            source_image.relative_to(root).as_posix() if source_image else ""
        )
        if image_qc_status == "approved" and approved_image != current_image:
            image_qc_status = "pending"
        legacy_motion = str(
            video.get("motion_prompt")
            or item.get("scene_description")
            or ""
        )
        return ShotSnapshot(
            number=number,
            description=str(item.get("scene_description", "")),
            prompt=str(item.get("image_prompt", "")),
            style=str(item.get("style_preset") or "真人电影"),
            duration_seconds=float(
                video.get("duration_seconds")
                or item.get("duration_seconds")
                or 3.0
            ),
            camera_movement=str(
                video.get("camera_movement")
                or item.get("camera_movement")
                or "auto"
            ),
            motion_prompt=legacy_motion,
            end_frame_prompt=str(video.get("end_frame_prompt") or ""),
            routing_reason=str(video.get("routing_reason") or ""),
            routing_locked=bool(video.get("routing_locked")),
            subject_motion=str(video.get("subject_motion") or legacy_motion),
            environment_motion=str(video.get("environment_motion") or ""),
            continuity_constraints=str(
                video.get("continuity_constraints") or ""
            ),
            negative_prompt=str(video.get("negative_prompt") or ""),
            native_audio_mode=str(
                video.get("native_audio_mode") or "ambience_sfx_music"
            ),
            dialogue_prompt=str(
                video.get("dialogue_prompt")
                or audio.get("text")
                or item.get("dialogue")
                or ""
            ),
            sound_effect_prompt=str(
                video.get("sound_effect_prompt")
                or item.get("sound_effect")
                or DEFAULT_H3_SOUND_EFFECT_PROMPT
            ),
            music_prompt=str(
                video.get("music_prompt") or DEFAULT_H3_MUSIC_PROMPT
            ),
            engine_profile=str(video.get("engine_profile") or "minimax_h3_fl2va"),
            motion_strength=str(video.get("motion_strength") or "low"),
            screen_direction=str(video.get("screen_direction") or "auto"),
            transition_out=str(video.get("transition_out") or "cut"),
            transition_frames=int(video.get("transition_frames") or 8),
            handle_frames=int(video.get("handle_frames") or 8),
            candidate_count=int(video.get("candidate_count") or 1),
            continuity_group=str(
                continuity.get("group_id") or "scene_01"
            ),
            beat_type=str(continuity.get("beat_type") or "dialogue"),
            action_phase=str(
                continuity.get("action_phase") or "anticipation"
            ),
            entry_state=str(continuity.get("entry_state") or ""),
            exit_state=str(continuity.get("exit_state") or ""),
            match_anchor=str(continuity.get("match_anchor") or ""),
            reference_shot_number=int(
                continuity.get("reference_shot_number") or 0
            ),
            reference_denoise=float(
                continuity.get("reference_denoise") or 0.76
            ),
            source_image=source_image,
            image_candidates=image_candidates,
            image_qc_status=image_qc_status,
            image_qc_note=str(generation.get("qc_note") or ""),
            image_qc_checked_at=str(generation.get("qc_checked_at") or ""),
            end_image=(
                self._find_shot_end_image(
                    root,
                    episode_number,
                    number,
                    str(video.get("end_image") or ""),
                )
                if allow_legacy_artifacts
                else self._existing_project_file(
                    root,
                    str(video.get("end_image") or ""),
                )
            ),
            video_path=(
                self._find_shot_video(
                    root,
                    episode_number,
                    number,
                    str(video.get("selected_video") or ""),
                )
                if allow_legacy_artifacts
                else self._existing_project_file(
                    root,
                    str(video.get("selected_video") or ""),
                )
            ),
            dialogue=str(audio.get("text") or item.get("dialogue") or ""),
            audio_mode=str(audio.get("mode") or "auto_narration"),
            speaker=str(audio.get("speaker") or "旁白"),
            tts_engine=str(audio.get("engine") or "edge_tts"),
            voice_id=str(
                audio.get("voice_id") or "zh-CN-YunyangNeural"
            ),
            voice_reference_path=self._safe_project_path(
                root,
                str(audio.get("reference_audio") or ""),
            ),
            voice_reference_text=str(audio.get("reference_text") or ""),
            voice_instruct_text=str(audio.get("instruct_text") or ""),
            fallback_to_edge=bool(audio.get("fallback_to_edge", True)),
            speech_rate=str(audio.get("rate") or "+5%"),
            speech_volume=str(audio.get("volume") or "+0%"),
            speech_pitch=str(audio.get("pitch") or "-5Hz"),
            subtitle_enabled=bool(audio.get("subtitle_enabled", True)),
            audio_path=self._safe_project_path(
                root,
                str(audio.get("audio_file") or ""),
            ),
            subtitle_path=self._safe_project_path(
                root,
                str(audio.get("subtitle_file") or ""),
            ),
            estimated_audio_duration_seconds=float(
                audio.get("estimated_duration_seconds") or 0.0
            ),
            planned_timeline_duration_seconds=float(
                audio.get("planned_timeline_duration_seconds") or 0.0
            ),
            timing_status=str(audio.get("timing_status") or "unplanned"),
            recommended_segments=max(
                1,
                int(audio.get("recommended_segments") or 1),
            ),
            lip_sync_enabled=bool(lip_sync.get("enabled", False)),
            lip_sync_engine=str(
                lip_sync.get("engine") or "latentsync_1_6"
            ),
            lip_sync_target_character=str(
                lip_sync.get("target_character") or ""
            ),
            lip_sync_mode=str(
                lip_sync.get("mode") or "speaker_tracking"
            ),
            lip_sync_status=str(lip_sync.get("status") or "disabled"),
            lip_sync_score=float(lip_sync.get("sync_score") or 0.0),
            lip_sync_output_path=self._safe_project_path(
                root,
                str(lip_sync.get("output_file") or ""),
            ),
        )

    @staticmethod
    def _find_shot(value: dict[str, Any], shot_number: int) -> dict[str, Any]:
        for index, shot in enumerate(value.get("shots") or [], start=1):
            if not isinstance(shot, dict):
                continue
            if int(shot.get("shot_number") or index) == shot_number:
                return shot
        raise KeyError(f"分镜中不存在镜头: {shot_number}")

    @classmethod
    def _find_shot_source(
        cls,
        root: Path,
        episode_number: int,
        shot_number: int,
        configured: str,
    ) -> Path | None:
        direct = cls._safe_project_path(root, configured)
        if direct and direct.is_file():
            return direct
        input_dir = (
            root / "production" / "video_inputs" / f"episode_{episode_number:03d}"
        )
        for suffix in (".png", ".jpg", ".jpeg", ".webp"):
            candidate = input_dir / f"shot_{shot_number:03d}{suffix}"
            if candidate.is_file():
                return candidate
        legacy = (
            root
            / "outputs"
            / "server_test"
            / "ch0001"
            / "original_images"
            / f"s{shot_number:02d}_shot01.png"
        )
        if episode_number == 1 and legacy.is_file():
            return legacy
        shot_root = (
            root
            / "production"
            / "shots"
            / f"episode_{episode_number:03d}"
            / f"shot_{shot_number:03d}"
            / "images"
        )
        candidates = [
            path
            for suffix in ("*.png", "*.jpg", "*.jpeg", "*.webp")
            for path in shot_root.glob(suffix)
        ]
        return max(candidates, key=lambda path: path.stat().st_mtime) if candidates else None

    @classmethod
    def _find_shot_end_image(
        cls,
        root: Path,
        episode_number: int,
        shot_number: int,
        configured: str,
    ) -> Path | None:
        direct = cls._safe_project_path(root, configured)
        if direct and direct.is_file():
            return direct
        input_dir = (
            root / "production" / "video_inputs" / f"episode_{episode_number:03d}"
        )
        for suffix in (".png", ".jpg", ".jpeg", ".webp"):
            candidate = input_dir / f"shot_{shot_number:03d}_end{suffix}"
            if candidate.is_file():
                return candidate
        return None

    @classmethod
    def _find_shot_video(
        cls,
        root: Path,
        episode_number: int,
        shot_number: int,
        configured: str,
    ) -> Path | None:
        direct = cls._safe_project_path(root, configured)
        if direct and direct.is_file():
            return direct
        directory = (
            root
            / "production"
            / "videos"
            / f"episode_{episode_number:03d}"
            / f"shot_{shot_number:03d}"
        )
        candidates = list(directory.glob("shot_*.mp4"))
        if candidates:
            return max(candidates, key=lambda path: path.stat().st_mtime)
        legacy = (
            root
            / "outputs"
            / "server_test"
            / "ch0001"
            / f"s{shot_number:02d}_shot01.mp4"
        )
        return legacy if episode_number == 1 and legacy.is_file() else None

    @staticmethod
    def _find_latest_episode_dubbed(
        root: Path,
        episode_number: int,
    ) -> Path | None:
        directory = (
            root
            / "production"
            / "videos"
            / f"episode_{episode_number:03d}"
        )
        candidates = list(
            directory.glob(f"episode_{episode_number:03d}_dubbed_*.mp4")
        )
        return (
            max(candidates, key=lambda path: path.stat().st_mtime)
            if candidates
            else None
        )

    @staticmethod
    def _safe_project_path(root: Path, relative: str) -> Path | None:
        if not relative:
            return None
        candidate = (root / relative).resolve()
        try:
            candidate.relative_to(root)
        except ValueError:
            return None
        return candidate

    @classmethod
    def _existing_project_file(
        cls,
        root: Path,
        relative: str,
    ) -> Path | None:
        candidate = cls._safe_project_path(root, relative)
        return candidate if candidate and candidate.is_file() else None

    @staticmethod
    def _project_relative_file(root: Path, path: Path, label: str) -> Path:
        candidate = Path(path).resolve()
        try:
            candidate.relative_to(root)
        except ValueError as exc:
            raise ValueError(f"{label}不在当前项目目录内") from exc
        if not candidate.is_file():
            raise FileNotFoundError(f"{label}不存在：{candidate}")
        return candidate

    @staticmethod
    def _display_timestamp(value: str, path: Path) -> str:
        if value:
            return value.replace("T", " ")[:19]
        return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(path.stat().st_mtime))

    @staticmethod
    def _selection_path(root: Path, relative: str) -> Path | None:
        if not relative:
            return None
        candidate = (root / relative).resolve()
        try:
            candidate.relative_to(root)
        except ValueError:
            return None
        return candidate if candidate.is_file() else None
