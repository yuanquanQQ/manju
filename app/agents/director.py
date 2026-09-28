"""导演 Agent：将章节结构化分析转换为视觉级分镜脚本。

调用 LLM，输入章节的事件、对话、实体描述，
输出每镜包含可落地的场景描写、人物刻画、环境细节和生图 Prompt。
"""
from __future__ import annotations

import re
from typing import Any

from app.adapters.llm import OpenAICompatibleLLM, StructuredLLM
from app.agents.story_planner import format_episode_plan, planned_dialogue_pairs
from app.core.logger import logger
from app.core.prompts import load_prompt
from app.domain.narrative import EpisodePlan
from app.domain.novel import ChapterAnalysis
from app.domain.storyboard import (
    CharacterAppearance,
    EnvironmentDetail,
    Episode,
    Shot,
    ShotAudioGeneration,
    ShotContinuityPlan,
    ShotLipSyncGeneration,
    ShotNarrativeBinding,
    ShotVideoGeneration,
)
from app.pipeline.audio_timing import optimize_episode_audio_timing
from app.pipeline.character_identity import derive_visual_fingerprints
from app.pipeline.continuity import plan_episode_continuity
from app.pipeline.pacing import normalize_episode_duration, pacing_target
from app.pipeline.video_prompt import build_storyboard_motion_prompt

_DIALOGUE_PARTS = re.compile(r"[／\n]+")
_DIALOGUE_PREFIX = re.compile(r"^([^：:]{1,40})[：:]\s*(.+)$")
_PROFILE_PLACEHOLDERS = ("已锁定", "按设定", "待定", "未提供")


def _is_usable_character_profile(value: object) -> bool:
    profile = str(value or "").strip()
    return len(profile) >= 80 and not any(
        term in profile for term in _PROFILE_PLACEHOLDERS
    )


def _generate_character_profiles(
    analysis: ChapterAnalysis,
    source_text: str,
    client: StructuredLLM,
) -> dict[str, str]:
    """Generate the immutable cast bible before any fresh storyboard shots."""

    required = [
        mention.surface_text.strip()
        for mention in analysis.mentions
        if str(mention.entity_type.value) == "character"
        and mention.surface_text.strip()
    ]
    required = list(dict.fromkeys(required))
    if not required:
        return {}
    result = client.complete(
        system_prompt=load_prompt("character_bible"),
        user_prompt=(
            f"为这些已识别人物建立真人影视定妆圣经：{'、'.join(required)}；"
            "同时检查原文并补充所有会在画面中出现的具名人物。"
            "每条 profile 必须用中文明确给出年龄感、性别呈现、脸型、眼型、鼻形、"
            "下颌、肤色、身形、发型轮廓、服装款式、主色、材质层次、鞋履和唯一标志配件；"
            "不得使用‘按设定’‘已锁定’等占位表达。不同人物至少三项视觉差异。\n\n"
            f"结构化分析：\n{_build_analysis_summary(analysis)}\n\n"
            f"原文：\n{source_text[:12000]}"
        ),
        json_schema={
            "type": "object",
            "properties": {
                "character_profiles": {
                    "type": "object",
                    "additionalProperties": {"type": "string", "minLength": 80},
                }
            },
            "required": ["character_profiles"],
        },
    )
    raw = result.get("character_profiles")
    raw = raw if isinstance(raw, dict) else {}
    profiles = {
        str(name).strip(): str(profile).strip()[:900]
        for name, profile in raw.items()
        if str(name).strip() and _is_usable_character_profile(profile)
    }
    invalid = [
        name
        for name in required
        if not _is_usable_character_profile(profiles.get(name))
    ]
    if invalid:
        raise RuntimeError(f"角色定妆圣经缺失或仍为占位内容：{'、'.join(invalid)}")
    return profiles


def _dialogue_payload(dialogue: str) -> tuple[ShotAudioGeneration, ShotLipSyncGeneration]:
    """Bind one exact storyboard line to its speaker, audio, and lip-sync state."""

    raw = dialogue.strip()
    if not raw:
        return (
            ShotAudioGeneration(enabled=False, mode="mute", speaker="旁白", text=""),
            ShotLipSyncGeneration(),
        )
    parts = [part.strip() for part in _DIALOGUE_PARTS.split(raw) if part.strip()]
    if len(parts) != 1:
        raise ValueError("每个镜头只能包含一个说话人的一条连续台词")
    match = _DIALOGUE_PREFIX.match(parts[0])
    if not match:
        raise ValueError(f"对白必须使用“角色名：原文台词”格式：{raw}")
    speaker, text = match.groups()
    speaker = speaker.strip()
    text = text.strip()
    narration = speaker == "旁白"
    audio = ShotAudioGeneration(
        enabled=True,
        mode="auto_narration" if narration else "dialogue",
        speaker=speaker,
        text=text,
        voice_assignment_mode="auto",
        timing_status="unplanned",
    )
    lip_sync = ShotLipSyncGeneration(
        enabled=not narration,
        target_character="" if narration else speaker,
        status="disabled" if narration else "pending",
    )
    return audio, lip_sync


def _validate_dialogues_against_source(
    items: list[dict[str, Any]],
    source_text: str,
    allowed_dialogues: set[tuple[str, str]] | None = None,
    allowed_adaptations: set[tuple[str, str]] | None = None,
) -> list[str]:
    errors: list[str] = []
    for index, item in enumerate(items, start=1):
        raw = str(item.get("dialogue") or "").strip()
        if not raw:
            continue
        parts = [part.strip() for part in _DIALOGUE_PARTS.split(raw) if part.strip()]
        if len(parts) != 1:
            errors.append(f"镜头{index}包含多个说话者")
            continue
        match = _DIALOGUE_PREFIX.match(parts[0])
        if not match:
            errors.append(f"镜头{index}对白格式错误")
            continue
        speaker, text = (value.strip() for value in match.groups())
        adapted = (speaker, text) in (allowed_adaptations or set())
        if text not in source_text and not adapted:
            errors.append(f"镜头{index}的{speaker}台词并非原文逐字内容")
        known_speakers = {
            known_speaker
            for known_speaker, known_text in (allowed_dialogues or set())
            if known_text == text
        }
        if speaker != "旁白" and known_speakers and speaker not in known_speakers:
            errors.append(f"镜头{index}台词与说话人{speaker}的原文记录不匹配")
        visible_names = {
            str(name).strip()
            for name in (item.get("characters") or [])
            if str(name).strip()
        }
        scene = " ".join(
            str(item.get(key) or "")
            for key in ("scene_description", "visible_action")
        )
        if speaker != "旁白" and speaker not in visible_names and "画外" not in scene:
            errors.append(f"镜头{index}说话人{speaker}不在画面且未标明画外")
    return errors


def _validate_compact_items(
    items: list[dict[str, Any]],
    profiles: dict[str, str] | None = None,
    episode_plan: EpisodePlan | None = None,
) -> list[str]:
    errors: list[str] = []
    camera_angles = {
        "close-up", "medium shot", "wide shot", "panoramic", "low angle",
        "high angle", "POV", "over-shoulder", "dutch angle",
    }
    camera_movements = {
        "static", "pan", "tilt", "zoom", "dolly", "handheld", "crane", "tracking",
        "slow_push", "slow_pull", "pan_left", "pan_right", "tilt_up", "tilt_down", "still",
    }
    for index, item in enumerate(items, start=1):
        scene = str(item.get("scene_description") or "").strip()
        visual = str(item.get("visual_prompt") or "").strip()
        if len(scene) < 10:
            errors.append(f"镜头{index}画面描述少于10字")
        if not visual or not visual.isascii():
            errors.append(f"镜头{index}visual_prompt必须为英文ASCII")
        if str(item.get("camera_angle") or "") not in camera_angles:
            errors.append(f"镜头{index}景别非法")
        movement = str(item.get("camera_movement") or "static")
        if movement not in camera_movements:
            errors.append(f"镜头{index}运镜非法")
        if profiles is not None:
            missing = [
                str(name).strip()
                for name in (item.get("characters") or [])
                if str(name).strip() not in profiles
            ]
            if missing:
                errors.append(
                    f"镜头{index}人物缺少定妆：{'、'.join(dict.fromkeys(missing))}"
                )
        if episode_plan is not None:
            scene_number = int(item.get("planned_scene_number") or 0)
            if scene_number < 1 or scene_number > len(episode_plan.scenes):
                errors.append(f"镜头{index}未绑定有效编剧场次")
    return errors

DIRECTOR_SYSTEM_PROMPT = load_prompt("director")




_CAMERA_MOVEMENT_MAP = {
    "static": "still",
    "pan": "pan_right",
    "tilt": "tilt_up",
    "zoom": "slow_push",
    "dolly": "slow_push",
    "handheld": "still",
    "crane": "tilt_up",
    "tracking": "pan_right",
}

_TRANSITION_MAP = {
    "cut": "cut",
    "fade": "fade_black",
    "dissolve": "dissolve",
    "wipe": "match_cut",
}

_DEFAULT_VIDEO_NEGATIVE = (
    "face morphing, identity change, age change, costume change, extra limbs, "
    "extra fingers, deformed hands, body distortion, duplicate person, flicker, "
    "frame jitter, camera shake, warped background, missing face, faceless, "
    "face blur, facial feature loss, asymmetric eyes, crossed eyes, missing eyes, "
    "mouth distortion, melted face, occluded face, cropped face, fast motion, "
    "sudden motion, rapid head turn, exaggerated expression, talking, lip sync, "
    "open mouth, crowd motion, moving background people, text, logo, watermark"
)


def _video_generation_from_item(
    item: dict[str, Any],
    *,
    scene_description: str,
    environment: EnvironmentDetail,
    characters: list[CharacterAppearance],
    camera_movement: str,
    transition: str,
    duration_seconds: float,
) -> ShotVideoGeneration:
    raw = item.get("video_generation")
    raw = raw if isinstance(raw, dict) else {}
    visible_character_motion = "；".join(
        part
        for character in characters
        for part in (
            f"{character.name}{character.pose.strip()}"
            if character.pose.strip()
            else "",
            f"{character.name}{character.expression.strip()}"
            if character.expression.strip()
            else "",
        )
        if part
    )
    subject_motion = str(
        raw.get("subject_motion")
        or visible_character_motion
        or scene_description
    ).strip()
    environment_motion = str(
        raw.get("environment_motion")
        or environment.atmosphere
        or ""
    ).strip()
    motion_prompt = str(raw.get("motion_prompt") or "").strip()
    continuity_plan = item.get("continuity_plan")
    continuity_plan = (
        continuity_plan if isinstance(continuity_plan, dict) else {}
    )
    motion_prompt = build_storyboard_motion_prompt(
        shot_number=int(item.get("shot_number") or 1),
        duration_seconds=duration_seconds,
        scene_description=scene_description,
        subject_motion=subject_motion,
        environment_motion=environment_motion,
        entry_state=str(continuity_plan.get("entry_state") or ""),
        exit_state=str(continuity_plan.get("exit_state") or subject_motion),
        dialogue=str(item.get("dialogue") or ""),
        sound_effect=str(item.get("sound_effect") or ""),
        dramatic_point=str(item.get("emotion") or ""),
    )
    continuity = str(raw.get("continuity_constraints") or "").strip()
    if not continuity:
        names = "、".join(
            character.name for character in characters if character.name
        )
        identity = f"保持{names}的" if names else "保持人物"
        continuity = (
            f"{identity}脸型、年龄、发型、服装和道具一致；"
            "保持人物站位、屏幕方向、光线和背景布局稳定"
        )
    motion_strength = str(raw.get("motion_strength") or "low")
    if motion_strength not in {"low", "medium", "high"}:
        motion_strength = "low"
    screen_direction = str(raw.get("screen_direction") or "auto")
    if screen_direction not in {
        "auto",
        "left_to_right",
        "right_to_left",
        "static",
    }:
        screen_direction = "auto"
    transition_out = str(
        raw.get("transition_out")
        or _TRANSITION_MAP.get(transition, "cut")
    )
    if transition_out not in {"cut", "match_cut", "dissolve", "fade_black"}:
        transition_out = "cut"
    return ShotVideoGeneration(
        engine_profile="minimax_h3_fl2va",
        subject_motion=subject_motion[:1600],
        environment_motion=environment_motion[:1200],
        continuity_constraints=continuity[:1600],
        negative_prompt=str(
            raw.get("negative_prompt") or _DEFAULT_VIDEO_NEGATIVE
        )[:1600],
        motion_prompt=motion_prompt[:4000],
        camera_movement=str(
            raw.get("camera_movement")
            or _CAMERA_MOVEMENT_MAP.get(camera_movement, "slow_push")
        ),
        motion_strength=motion_strength,
        screen_direction=screen_direction,
        transition_out=transition_out,
        transition_frames=int(
            raw["transition_frames"]
            if raw.get("transition_frames") is not None
            else 8
        ),
        handle_frames=int(
            raw["handle_frames"]
            if raw.get("handle_frames") is not None
            else 8
        ),
        candidate_count=int(raw.get("candidate_count") or 1),
        duration_seconds=max(1.0, min(duration_seconds, 15.0)),
    )


def _build_analysis_summary(analysis: ChapterAnalysis) -> str:
    """将 ChapterAnalysis 转为 LLM 可读的摘要文本。"""
    parts: list[str] = []

    parts.append(f"章节 ID: {analysis.chapter_id}")
    parts.append(f"章节摘要: {analysis.summary}")

    if analysis.mentions:
        parts.append("\n本章提及的实体及其描述:")
        for m in analysis.mentions:
            desc = f": {m.description}" if m.description else ""
            parts.append(
                f"  - [{m.entity_type.value}] {m.surface_text}{desc}"
            )

    if analysis.events:
        parts.append(f"\n事件序列 ({len(analysis.events)} 个):")
        for e in analysis.events:
            participants = "、".join(e.participants) if e.participants else "无"
            loc = f" @{e.location}" if e.location else ""
            result = f" | 结果: {e.result}" if e.result else ""
            parts.append(
                f"  [重要度 {e.importance}/5] {e.summary}"
                f" | 参与: {participants}{loc}{result}"
            )

    if analysis.dialogues:
        parts.append(f"\n对白 ({len(analysis.dialogues)} 条):")
        for d in analysis.dialogues:
            to = f" → {d.addressee}" if d.addressee else ""
            em = f" [{d.emotion}]" if d.emotion else ""
            parts.append(f"  {d.speaker}{to}{em}: {d.text}")

    if analysis.state_changes:
        parts.append(f"\n状态变化 ({len(analysis.state_changes)} 个):")
        for sc in analysis.state_changes:
            before = f" ({sc.before} →)" if sc.before else ""
            parts.append(f"  {sc.entity}.{sc.attribute}{before} {sc.after}")

    if analysis.adaptation_notes:
        parts.append("\n改编建议:")
        for note in analysis.adaptation_notes:
            parts.append(f"  - {note}")

    return "\n".join(parts)


def _source_segments(
    source_text: str,
    *,
    target_shots: int,
    max_chars: int = 1900,
) -> list[tuple[str, int]]:
    """Split a chapter at paragraph boundaries and distribute shot targets."""

    text = source_text.strip()
    if not text:
        return [("", target_shots)]
    paragraphs = [part.strip() for part in text.splitlines() if part.strip()]
    segments: list[str] = []
    current: list[str] = []
    current_size = 0
    for paragraph in paragraphs:
        if current and current_size + len(paragraph) > max_chars:
            segments.append("\n".join(current))
            current = []
            current_size = 0
        current.append(paragraph)
        current_size += len(paragraph)
    if current:
        segments.append("\n".join(current))
    count = max(1, len(segments))
    base, extra = divmod(target_shots, count)
    return [
        (segment, base + (1 if index < extra else 0))
        for index, segment in enumerate(segments)
    ]


def _identity_bible(profiles: dict[str, str]) -> str:
    if not profiles:
        return "本章暂无已锁定定妆；请为每个角色建立彼此不可互换的视觉指纹。"
    lines = ["已锁定人物视觉设定（每个相关镜头必须逐字遵守其关键差异）："]
    for name, profile in profiles.items():
        lines.append(f"- {name}: {profile[:900]}")
    lines.append(
        "禁止混用上述人物的脸型、眼型、发冠、服装主色、身份配件或身体轮廓。"
    )
    return "\n".join(lines)


def _expand_compact_beat(
    item: dict[str, Any],
    *,
    profiles: dict[str, str],
    episode_plan: EpisodePlan | None = None,
) -> dict[str, Any]:
    """Expand a compact LLM beat into the stable full shot contract."""

    scene = str(item.get("scene_description") or "").strip()
    visible_action = str(item.get("visible_action") or scene).strip()
    location = str(item.get("location") or "本段既定场景").strip()
    atmosphere = str(item.get("atmosphere") or "轻微空气流动").strip()
    lighting = str(
        item.get("lighting") or "延续场景既定主光方向与自然电影光线"
    ).strip()
    framing = str(item.get("camera_angle") or "medium shot").strip()
    camera_movement = str(
        item.get("camera_movement") or "static"
    ).strip()
    beat_type = str(item.get("beat_type") or "action").strip()
    if beat_type not in {
        "establish",
        "action",
        "reaction",
        "dialogue",
        "flashback",
    }:
        beat_type = "action"
    names = [
        str(name).strip()
        for name in (item.get("characters") or [])
        if str(name).strip()
    ]
    expression = str(item.get("expression") or "").strip()
    characters = [
        {
            "name": name,
            "appearance": str(profiles[name])[:300],
            "clothing": str(profiles[name])[:300],
            "pose": visible_action[:200],
            "expression": expression[:150],
        }
        for name in names
    ]
    visual_prompt = str(item.get("visual_prompt") or scene).strip()
    identity_prompt = "; ".join(
        str(profiles.get(name) or "") for name in names if profiles.get(name)
    )
    duration = max(
        2.5,
        min(float(item.get("duration_seconds") or 3.2), 5.0),
    )
    transition_hint = str(item.get("transition_hint") or "cut").strip()
    if transition_hint not in {"cut", "match_cut", "dissolve", "fade_black"}:
        transition_hint = "cut"
    source_transition = (
        "fade"
        if transition_hint == "fade_black"
        else "wipe"
        if transition_hint == "match_cut"
        else transition_hint
    )
    planned_scene_number = max(0, int(item.get("planned_scene_number") or 0))
    planned_scene = (
        episode_plan.scenes[planned_scene_number - 1]
        if episode_plan
        and 1 <= planned_scene_number <= len(episode_plan.scenes)
        else None
    )
    source_event_ids = [
        str(event_id).strip()
        for event_id in (item.get("source_event_ids") or [])
        if str(event_id).strip()
    ]
    if planned_scene and not source_event_ids:
        source_event_ids = list(planned_scene.source_event_ids)
    dramatic_purpose = str(item.get("dramatic_purpose") or "").strip()
    value_before = str(item.get("value_before") or "").strip()
    value_after = str(item.get("value_after") or "").strip()
    if planned_scene:
        dramatic_purpose = dramatic_purpose or planned_scene.purpose
        value_before = value_before or planned_scene.value_before
        value_after = value_after or planned_scene.value_after
    return {
        "scene_description": scene,
        "environment": {
            "layout": f"{location}；{scene}"[:400],
            "lighting": lighting[:300],
            "color_palette": "延续同一场景与人物服装的固定综合色板",
            "atmosphere": atmosphere[:200],
        },
        "characters": characters,
        "camera_angle": framing,
        "camera_movement": camera_movement,
        "emotion": str(
            item.get("emotion")
            or (
                f"{planned_scene.emotion_start}转为{planned_scene.emotion_end}"
                if planned_scene
                else "克制的戏剧张力"
            )
        )[:100],
        "dialogue": str(item.get("dialogue") or "")[:500],
        "sound_effect": str(item.get("sound_effect") or "")[:200],
        "duration_seconds": duration,
        "transition": source_transition,
        "image_prompt": (
            "masterpiece, best quality, photorealistic live-action Chinese "
            f"xianxia cinematic scene, {visual_prompt}, {identity_prompt}, {framing}, real human "
            "actors, natural skin texture, coherent anatomy, cinematic lighting, "
            "video-safe first frame, no anime, no illustration, no CGI, no text, "
            "no logo, no watermark"
        )[:600],
        "style_preset": "真人电影",
        "narrative_binding": {
            "scene_number": planned_scene_number,
            "dramatic_purpose": dramatic_purpose[:300],
            "source_event_ids": source_event_ids,
            "value_before": value_before[:120],
            "value_after": value_after[:120],
        },
        "continuity_plan": {
            "group_id": (
                f"scene_{planned_scene_number:02d}"
                if planned_scene_number
                else "scene_01"
            ),
            "beat_type": beat_type,
            "action_phase": "anticipation",
            "entry_state": "",
            "exit_state": visible_action[:600],
            "match_anchor": "",
        },
        "video_generation": {
            "subject_motion": visible_action[:1600],
            "environment_motion": atmosphere[:1200],
            "motion_prompt": "；".join(
                part for part in (visible_action, atmosphere) if part
            )[:1600],
            "continuity_constraints": "",
            "negative_prompt": _DEFAULT_VIDEO_NEGATIVE,
            "camera_movement": _CAMERA_MOVEMENT_MAP.get(
                camera_movement,
                camera_movement
                if camera_movement
                in {
                    "slow_push",
                    "slow_pull",
                    "pan_left",
                    "pan_right",
                    "tilt_up",
                    "tilt_down",
                    "still",
                }
                else "still",
            ),
            "motion_strength": str(item.get("motion_strength") or "low"),
            "screen_direction": str(item.get("screen_direction") or "auto"),
            "transition_out": transition_hint,
            "transition_frames": 4 if transition_hint == "match_cut" else 8,
            "handle_frames": 8,
            "candidate_count": 1,
            "duration_seconds": duration,
        },
    }


def _parse_shots(raw_data: dict[str, Any]) -> list[Shot]:
    """从 LLM 返回的原始数据中提取 Shot 列表（兼容新旧格式）。"""
    shots_data = raw_data.get("shots", raw_data.get("storyboard", []))

    if isinstance(shots_data, dict):
        shots_data = list(shots_data.values())

    if not isinstance(shots_data, list):
        raise ValueError(f"shots 必须是数组，实际: {type(shots_data).__name__}")

    plan_episode_continuity({"shots": shots_data}, force=True)
    shots: list[Shot] = []
    parse_errors: list[str] = []
    for idx, item in enumerate(shots_data, start=1):
        if not isinstance(item, dict):
            continue
        try:
            # 解析 characters
            chars_raw = item.get("characters", [])
            characters: list[CharacterAppearance] = []
            if isinstance(chars_raw, list):
                for c in chars_raw:
                    if isinstance(c, dict):
                        characters.append(
                            CharacterAppearance(
                                name=c.get("name", ""),
                                appearance=c.get("appearance", ""),
                                clothing=c.get("clothing", ""),
                                pose=c.get("pose", ""),
                                expression=c.get("expression", ""),
                            )
                        )
                    elif isinstance(c, str):
                        # 兼容旧格式
                        characters.append(
                            CharacterAppearance(name=c, appearance=c)
                        )

            # 解析 environment
            env_raw = item.get("environment", {})
            if isinstance(env_raw, dict):
                environment = EnvironmentDetail(
                    layout=env_raw.get("layout", ""),
                    lighting=env_raw.get("lighting", ""),
                    color_palette=env_raw.get("color_palette", ""),
                    atmosphere=env_raw.get("atmosphere", ""),
                )
            else:
                environment = EnvironmentDetail()

            scene_description = str(item.get("scene_description", "")).strip()
            camera_movement = str(item.get("camera_movement", "static"))
            transition = str(item.get("transition", "cut"))
            duration_seconds = float(item.get("duration_seconds", 3.0))
            image_prompt = str(item.get("image_prompt", "")).strip()
            if not image_prompt:
                image_prompt = (
                    "masterpiece, best quality, photorealistic live-action Chinese "
                    f"xianxia cinematic scene, {scene_description}, "
                    f"{item.get('camera_angle', 'medium shot')}, natural skin texture, "
                    "cinematic lighting, coherent anatomy, no text, no watermark"
                )[:600]
            continuity_raw = item.get("continuity_plan")
            continuity_raw = (
                continuity_raw if isinstance(continuity_raw, dict) else {}
            )
            audio_generation, lip_sync = _dialogue_payload(
                str(item.get("dialogue") or "")
            )
            shots.append(
                Shot(
                    shot_number=item.get("shot_number", idx),
                    scene_description=scene_description,
                    environment=environment,
                    characters=characters,
                    camera_angle=item.get("camera_angle", "medium shot"),
                    camera_movement=camera_movement,
                    emotion=item.get("emotion", "neutral"),
                    dialogue=item.get("dialogue", ""),
                    sound_effect=item.get("sound_effect", ""),
                    duration_seconds=duration_seconds,
                    transition=transition,
                    image_prompt=image_prompt,
                    style_preset=str(item.get("style_preset") or "真人电影"),
                    video_generation=_video_generation_from_item(
                        item,
                        scene_description=scene_description,
                        environment=environment,
                        characters=characters,
                        camera_movement=camera_movement,
                        transition=transition,
                        duration_seconds=duration_seconds,
                    ),
                    continuity_plan=ShotContinuityPlan.model_validate(
                        continuity_raw
                    ),
                    audio_generation=audio_generation,
                    lip_sync=lip_sync,
                    narrative_binding=ShotNarrativeBinding.model_validate(
                        item.get("narrative_binding") or {}
                    ),
                )
            )
        except Exception as exc:
            logger.warning(f"Shot {idx} 解析失败: {exc}")
            parse_errors.append(f"镜头{idx}: {exc}")
    if parse_errors:
        raise ValueError("分镜解析失败：" + "；".join(parse_errors))
    return shots


def direct_chapter(
    analysis: ChapterAnalysis,
    *,
    llm: StructuredLLM | None = None,
    episode_number: int = 1,
    episode_title: str = "",
    source_text: str = "",
    character_profiles: dict[str, str] | None = None,
    character_visual_fingerprints: dict[str, str] | None = None,
    character_styles: dict[str, str] | None = None,
    character_generation_presets: dict[str, str] | None = None,
    episode_plan: EpisodePlan | None = None,
) -> Episode:
    """将单章分析结果转为视觉级分镜。"""
    client = llm or OpenAICompatibleLLM()
    summary_text = _build_analysis_summary(analysis)
    profiles = {
        str(name).strip(): str(profile).strip()
        for name, profile in (character_profiles or {}).items()
        if str(name).strip() and _is_usable_character_profile(profile)
    }
    analyzed_names = {
        mention.surface_text.strip()
        for mention in analysis.mentions
        if str(mention.entity_type.value) == "character"
    }
    allowed_dialogues = {
        (dialogue.speaker.strip(), dialogue.text.strip())
        for dialogue in analysis.dialogues
        if dialogue.speaker.strip() and dialogue.text.strip()
    }
    allowed_adaptations = (
        planned_dialogue_pairs(episode_plan) if episode_plan else set()
    )
    if not analyzed_names.issubset(profiles):
        generated_profiles = _generate_character_profiles(
            analysis,
            source_text,
            client,
        )
        profiles = {**generated_profiles, **profiles}
    target = pacing_target(
        len(source_text),
        event_count=len(analysis.events),
        dialogue_count=len(analysis.dialogues),
        planned_shots=episode_plan.target_shot_count if episode_plan else 0,
        planned_duration_seconds=(
            episode_plan.target_duration_seconds if episode_plan else 0.0
        ),
    )
    segments = _source_segments(source_text, target_shots=target.target_shots)
    raw_shots: list[dict[str, Any]] = []
    format_prompt = (
        "【输出格式 — 最高优先级】你的回复必须且只能是以 "
        '{"shots": [ 开头、以 ]} 结尾的纯 JSON 对象。'
        "禁止输出思考过程、解释、代码块标记或 Markdown。"
        "只输出紧凑镜头节拍，不要输出 environment、完整人物外貌、"
        "continuity_plan 或 video_generation；程序会根据定妆和连续性规则自动补齐。"
        "每项只保留 scene_description、characters（姓名字符串数组）、location、"
        "camera_angle、camera_movement、beat_type、visible_action、expression、"
        "dialogue、duration_seconds、visual_prompt、lighting、atmosphere、"
        "screen_direction、transition_hint，以及 planned_scene_number、"
        "dramatic_purpose、source_event_ids、value_before、value_after。"
    )
    narrative_context = (
        format_episode_plan(episode_plan)
        if episode_plan
        else "未提供独立编剧计划，按结构化分析建立基础场次。"
    )
    for segment_index, (segment_text, segment_target) in enumerate(
        segments,
        start=1,
    ):
        segment_min = max(2, segment_target - 1)
        prompt = (
            f"请把本章第 {segment_index}/{len(segments)} 个连续段落转换为"
            f" {segment_target}-{segment_target + 2} 个镜头，至少 {segment_min} 个。"
            "本段中的铺垫、物件特写、动作过程、对话双方反应和段尾状态都必须呈现；"
            "不要跨越或概括本段事件。每镜只表现一个可见动作或一个明确反应。\n\n"
            f"全章节奏目标：{target.target_shots} 个左右、"
            f"{target.target_duration_seconds:.0f} 秒，最低 "
            f"{target.min_shots} 镜头且不少于 "
            f"{target.min_duration_seconds:.0f} 秒。\n\n"
            f"职业编剧计划：\n{narrative_context}\n\n"
            f"{_identity_bible(profiles)}\n\n"
            f"{format_prompt}\n\n"
            f"全章结构化分析：\n{summary_text}\n\n"
            f"本次必须改编的原文段落：\n{segment_text or summary_text}"
        )
        segment_items: list[dict[str, Any]] = []
        for attempt in range(1, 3):
            try:
                value = client.complete(
                    system_prompt=DIRECTOR_SYSTEM_PROMPT,
                    user_prompt=(
                        prompt
                        if attempt == 1
                        else prompt
                        + f"\n\n上次镜头数不足；这次必须输出至少 {segment_min} 个"
                        "彼此不同、按时间顺序排列的镜头。"
                    ),
                    json_schema=_COMPACT_BEAT_OUTPUT_SCHEMA(),
                )
            except Exception as exc:
                logger.error(f"导演 Agent 调用失败: {exc}")
                if attempt >= 2:
                    raise
                continue
            candidate = value.get("shots", value.get("storyboard", []))
            if isinstance(candidate, dict):
                candidate = list(candidate.values())
            segment_items = [
                item for item in candidate if isinstance(item, dict)
            ] if isinstance(candidate, list) else []
            dialogue_errors = _validate_dialogues_against_source(
                segment_items,
                source_text,
                allowed_dialogues,
                allowed_adaptations,
            )
            content_errors = _validate_compact_items(
                segment_items,
                profiles,
                episode_plan,
            )
            if (
                len(segment_items) >= segment_min
                and not dialogue_errors
                and not content_errors
            ):
                break
            logger.warning(
                f"章节 {analysis.chapter_id} 第 {segment_index} 段仅生成 "
                f"{len(segment_items)} 个镜头，要求至少 {segment_min}；"
                f"对白问题：{'；'.join(dialogue_errors) or '无'}，正在重试"
                f"；内容问题：{'；'.join(content_errors) or '无'}"
            )
        dialogue_errors = _validate_dialogues_against_source(
            segment_items,
            source_text,
            allowed_dialogues,
            allowed_adaptations,
        )
        content_errors = _validate_compact_items(
            segment_items,
            profiles,
            episode_plan,
        )
        if len(segment_items) < segment_min or dialogue_errors or content_errors:
            raise RuntimeError(
                f"章节 {analysis.chapter_id} 第 {segment_index} 段镜头密度不足："
                f"{len(segment_items)}/{segment_min}；"
                f"对白校验：{'；'.join(dialogue_errors) or '通过'}"
                f"；内容校验：{'；'.join(content_errors) or '通过'}"
            )
        raw_shots.extend(
            _expand_compact_beat(
                item,
                profiles=profiles,
                episode_plan=episode_plan,
            )
            for item in segment_items
        )

    for index, item in enumerate(raw_shots, start=1):
        item["shot_number"] = index
    shots = _parse_shots({"shots": raw_shots})
    if not shots:
        raise RuntimeError(f"章节 {analysis.chapter_id} 未生成有效分镜")
    if len(shots) < target.min_shots:
        raise RuntimeError(
            f"章节 {analysis.chapter_id} 镜头数不足："
            f"{len(shots)}/{target.min_shots}"
        )
    total_duration = normalize_episode_duration(
        shots,
        minimum_seconds=target.min_duration_seconds,
    )
    if total_duration < target.min_duration_seconds - 0.1:
        raise RuntimeError(
            f"章节 {analysis.chapter_id} 总时长不足：{total_duration:.1f}/"
            f"{target.min_duration_seconds:.1f} 秒"
        )

    # Duration normalization happens after parsing, so rebuild every time-coded
    # prompt against the final duration rather than retaining stale boundaries.
    for shot in shots:
        shot.video_generation.motion_prompt = build_storyboard_motion_prompt(
            shot_number=shot.shot_number,
            duration_seconds=shot.duration_seconds,
            scene_description=shot.scene_description,
            subject_motion=shot.video_generation.subject_motion,
            environment_motion=shot.video_generation.environment_motion,
            entry_state=shot.continuity_plan.entry_state,
            exit_state=shot.continuity_plan.exit_state,
            dialogue=shot.dialogue,
            sound_effect=shot.sound_effect,
            dramatic_point=shot.emotion,
        )

    # 重新编号确保连续
    for i, shot in enumerate(shots, start=1):
        shot.shot_number = i

    fingerprints = derive_visual_fingerprints(
        profiles,
        shots,
        existing=character_visual_fingerprints,
    )
    episode = Episode(
        episode_number=episode_number,
        episode_title=episode_title or f"第 {episode_number} 集",
        chapter_ids=(
            list(episode_plan.source_chapter_ids)
            if episode_plan
            else [analysis.chapter_id]
        ),
        artifact_binding_policy="explicit_only",
        character_profiles=profiles,
        character_visual_fingerprints=fingerprints,
        character_styles=dict(character_styles or {}),
        character_generation_presets=dict(
            character_generation_presets or {}
        ),
        narrative_plan=episode_plan,
        shots=shots,
        summary=analysis.summary[:500],
    )
    payload = episode.model_dump(mode="json")
    optimize_episode_audio_timing(
        payload,
        minimum_episode_seconds=target.min_duration_seconds,
    )
    return Episode.model_validate(payload)


def _COMPACT_BEAT_OUTPUT_SCHEMA() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "shots": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "scene_description": {"type": "string", "minLength": 10},
                        "characters": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                        "location": {"type": "string"},
                        "camera_angle": {
                            "type": "string",
                            "enum": ["close-up", "medium shot", "wide shot", "panoramic", "low angle", "high angle", "POV", "over-shoulder", "dutch angle"],
                        },
                        "camera_movement": {
                            "type": "string",
                            "enum": ["static", "pan", "tilt", "zoom", "dolly", "handheld", "crane", "tracking", "slow_push", "slow_pull", "pan_left", "pan_right", "tilt_up", "tilt_down", "still"],
                        },
                        "beat_type": {
                            "type": "string",
                            "enum": ["establish", "action", "reaction", "dialogue", "flashback"],
                        },
                        "visible_action": {"type": "string"},
                        "expression": {"type": "string"},
                        "dialogue": {"type": "string"},
                        "sound_effect": {"type": "string"},
                        "duration_seconds": {"type": "number"},
                        "visual_prompt": {"type": "string", "minLength": 20},
                        "lighting": {"type": "string"},
                        "atmosphere": {"type": "string"},
                        "emotion": {"type": "string"},
                        "motion_strength": {"type": "string"},
                        "screen_direction": {"type": "string"},
                        "transition_hint": {"type": "string"},
                        "planned_scene_number": {"type": "integer", "minimum": 0},
                        "dramatic_purpose": {"type": "string"},
                        "source_event_ids": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                        "value_before": {"type": "string"},
                        "value_after": {"type": "string"},
                    },
                    "required": [
                        "scene_description",
                        "characters",
                        "camera_angle",
                        "beat_type",
                        "visible_action",
                        "duration_seconds",
                        "visual_prompt",
                    ],
                },
            }
        },
        "required": ["shots"],
    }


def _SHOT_OUTPUT_SCHEMA() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "shots": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "shot_number": {"type": "integer"},
                        "scene_description": {"type": "string"},
                        "environment": {
                            "type": "object",
                            "properties": {
                                "layout": {"type": "string"},
                                "lighting": {"type": "string"},
                                "color_palette": {"type": "string"},
                                "atmosphere": {"type": "string"},
                            },
                            "required": ["layout", "lighting"],
                        },
                        "characters": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "name": {"type": "string"},
                                    "appearance": {"type": "string"},
                                    "clothing": {"type": "string"},
                                    "pose": {"type": "string"},
                                    "expression": {"type": "string"},
                                },
                                "required": ["name", "appearance"],
                            },
                        },
                        "camera_angle": {"type": "string"},
                        "camera_movement": {"type": "string"},
                        "emotion": {"type": "string"},
                        "dialogue": {"type": "string"},
                        "sound_effect": {"type": "string"},
                        "duration_seconds": {"type": "number"},
                        "transition": {"type": "string"},
                        "image_prompt": {"type": "string"},
                        "style_preset": {"type": "string"},
                        "continuity_plan": {
                            "type": "object",
                            "properties": {
                                "group_id": {"type": "string"},
                                "beat_type": {"type": "string"},
                                "action_phase": {"type": "string"},
                                "entry_state": {"type": "string"},
                                "exit_state": {"type": "string"},
                                "match_anchor": {"type": "string"},
                                "transition_strategy": {"type": "string"},
                                "match_action": {"type": "string"},
                                "eyeline": {"type": "string"},
                                "screen_axis": {"type": "string"},
                                "bridge_prompt": {"type": "string"},
                            },
                            "required": [
                                "group_id",
                                "beat_type",
                                "action_phase",
                                "entry_state",
                                "exit_state",
                                "match_anchor",
                                "transition_strategy",
                                "match_action",
                                "eyeline",
                                "screen_axis",
                                "bridge_prompt",
                            ],
                        },
                        "video_generation": {
                            "type": "object",
                            "properties": {
                                "subject_motion": {"type": "string"},
                                "environment_motion": {"type": "string"},
                                "motion_prompt": {"type": "string"},
                                "continuity_constraints": {"type": "string"},
                                "negative_prompt": {"type": "string"},
                                "camera_movement": {"type": "string"},
                                "motion_strength": {"type": "string"},
                                "screen_direction": {"type": "string"},
                                "transition_out": {"type": "string"},
                            },
                            "required": [
                                "subject_motion",
                                "environment_motion",
                                "motion_prompt",
                                "continuity_constraints",
                                "negative_prompt",
                                "camera_movement",
                                "motion_strength",
                                "screen_direction",
                                "transition_out",
                            ],
                        },
                    },
                    "required": [
                        "shot_number",
                        "scene_description",
                        "environment",
                        "characters",
                        "image_prompt",
                        "continuity_plan",
                        "video_generation",
                    ],
                },
            }
        },
        "required": ["shots"],
    }
