"""Plan visual continuity between storyboard shots.

The plan is deliberately stored in episode JSON so the desktop UI, image
workflow and video workflow all share the same shot-to-shot state.
"""

from __future__ import annotations

import re
from typing import Any

_FLASHBACK_MARKERS = (
    "回忆",
    "闪回",
    "前世",
    "梦境",
    "记忆",
    "幻象",
)
_PRESENT_RETURN_MARKERS = (
    "回到药圃",
    "回到现实",
    "现实中",
    "眼前的药圃",
)
_SCENE_BREAK_MARKERS = (
    "与此同时",
    "另一边",
    "翌日",
    "次日",
    "数日后",
    "多年后",
    "夜幕降临",
    "画面转到",
    "转场至",
)
_ESTABLISH_MARKERS = ("全景", "远景", "俯瞰", "清晨", "夜晚", "场景建立")
_REACTION_MARKERS = (
    "发现",
    "意识到",
    "目光",
    "震惊",
    "愤怒",
    "惊讶",
    "凝重",
    "反应",
)
_ACTION_MARKERS = (
    "拔剑",
    "挥剑",
    "冲",
    "跑",
    "跃",
    "刺",
    "打",
    "抓",
    "推",
    "转身",
    "俯身",
    "蹲下",
    "抬手",
    "签下",
)

_ACTION_READY_BASE = (
    "cinematic action-ready keyframe, captured at the anticipatory instant "
    "immediately before the main action completes, natural asymmetrical body "
    "weight, a clear line of action through the torso and limbs, purposeful "
    "hands kept away from the face, layered foreground midground and background, "
    "open visual space in the direction of movement, candid dramatic moment, "
    "not a posed portrait, not a static character lineup, not a rigid symmetrical "
    "standing pose"
)

_REFERENCE_RESET_PATTERN = re.compile(
    r"\b(?:macro|insert|pov|top[- ]down|detail shot|extreme close-up)\b",
    flags=re.IGNORECASE,
)


def _text(shot: dict[str, Any]) -> str:
    return " ".join(
        str(shot.get(key) or "").strip()
        for key in ("scene_description", "dialogue", "image_prompt")
    )


def _beat_type(text: str, index: int) -> str:
    if _is_flashback(text):
        return "flashback"
    if index == 1 or any(marker in text for marker in _ESTABLISH_MARKERS):
        return "establish"
    if any(marker in text for marker in _ACTION_MARKERS):
        return "action"
    if any(marker in text for marker in _REACTION_MARKERS):
        return "reaction"
    return "dialogue"


def _is_flashback(text: str) -> bool:
    return (
        any(marker in text for marker in _FLASHBACK_MARKERS)
        and not any(marker in text for marker in _PRESENT_RETURN_MARKERS)
    )


def _action_phase(beat_type: str) -> str:
    return {
        "establish": "setup",
        "action": "anticipation",
        "reaction": "reaction",
        "flashback": "impact",
        "dialogue": "interaction",
    }.get(beat_type, "anticipation")


def _character_names(
    shot: dict[str, Any],
    known_names: tuple[str, ...],
) -> list[str]:
    characters = shot.get("characters")
    names = [
        str(item.get("name") or "").strip()
        for item in (characters if isinstance(characters, list) else [])
        if isinstance(item, dict) and str(item.get("name") or "").strip()
    ]
    if names:
        return names
    combined = _text(shot)
    known_matches = [name for name in known_names if name in combined]
    if known_matches:
        return known_matches
    return []


def _reference_composition_compatible(shot: dict[str, Any]) -> bool:
    """Do not carry a full-character frame into an insert or overhead composition."""

    framing_text = " ".join(
        str(shot.get(key) or "")
        for key in ("camera_angle", "scene_description", "image_prompt")
    )
    return not bool(_REFERENCE_RESET_PATTERN.search(framing_text))


def _match_anchor(shot: dict[str, Any], names: list[str]) -> str:
    video = shot.get("video_generation")
    video = video if isinstance(video, dict) else {}
    existing = str(video.get("continuity_constraints") or "").strip()
    if existing:
        return existing[:600]
    subject = "、".join(names) if names else "主要人物"
    return (
        f"{subject}的脸型、年龄、发型、服装、道具保持一致；"
        "主光方向、背景地标、人物左右站位和视线轴线保持一致"
    )


def _exit_state(shot: dict[str, Any], scene: str) -> str:
    video = shot.get("video_generation")
    video = video if isinstance(video, dict) else {}
    motion = str(
        video.get("subject_motion")
        or video.get("motion_prompt")
        or scene
    ).strip()
    return motion[:500]


def _keyframe_prompt(
    *,
    beat_type: str,
    entry_state: str,
    exit_state: str,
    match_anchor: str,
    screen_direction: str,
    bridge_prompt: str,
) -> str:
    beat_directive = {
        "establish": (
            "Use a motivated environmental establishing composition with the "
            "character already engaged with the location: catch them mid-step along "
            "the path or beginning to reach toward the key story object, one foot "
            "ahead, torso angled and gaze directed into the scene. Ignore any earlier "
            "static-standing language; never use an upright centered display pose."
        ),
        "action": (
            "Freeze the readable wind-up before the decisive movement; preserve "
            "room for the body and prop to travel through the next frames."
        ),
        "reaction": (
            "Show the physical reaction beginning in the eyes, shoulders and "
            "weight shift instead of a front-facing neutral pose."
        ),
        "dialogue": (
            "Use conversational blocking, opposing eyelines and a subtle active "
            "gesture instead of two people standing parallel to the camera."
        ),
        "flashback": (
            "Use a distinct memory treatment while keeping the action readable "
            "and the bodies anatomically separated."
        ),
    }.get(beat_type, "")
    direction = {
        "left_to_right": "Preserve left-to-right screen direction and leave lead room on the right.",
        "right_to_left": "Preserve right-to-left screen direction and leave lead room on the left.",
        "static": "Preserve the established screen axis and eyelines.",
    }.get(screen_direction, "Preserve the established screen axis and eyelines.")
    return (
        f"{_ACTION_READY_BASE}. {beat_directive} {direction} "
        f"Entry continuity: {entry_state}. Intended exit action: {exit_state}. "
        f"Match anchors: {match_anchor}. Transition bridge: {bridge_prompt}."
    )[:1600]


def plan_episode_continuity(
    value: dict[str, Any],
    *,
    force: bool = False,
) -> dict[str, int]:
    """Backfill or rebuild shot continuity plans in an episode dictionary."""

    shots = [
        shot
        for shot in (value.get("shots") or [])
        if isinstance(shot, dict)
    ]
    stats = {"plans_updated": 0, "reference_links": 0, "groups": 0}
    if not shots:
        return stats
    profiles = value.get("character_profiles")
    known_names = tuple(
        str(name)
        for name in (profiles if isinstance(profiles, dict) else {})
        if str(name).strip()
    )

    live_group_index = 1
    flashback_index = 0
    active_live_group = f"scene_{live_group_index:02d}"
    previous_was_flashback = False
    last_shot_by_group: dict[str, int] = {}
    last_exit_by_group: dict[str, str] = {}
    last_cast_by_group: dict[str, str] = {}
    last_reference_eligible_by_group: dict[str, bool] = {}
    groups: set[str] = set()

    for index, shot in enumerate(shots, start=1):
        scene = str(shot.get("scene_description") or "").strip()
        combined = _text(shot)
        existing = shot.get("continuity_plan")
        existing = dict(existing) if isinstance(existing, dict) else {}
        original = dict(existing)
        is_flashback = _is_flashback(combined)

        if not is_flashback and not previous_was_flashback and index > 1:
            if any(marker in scene for marker in _SCENE_BREAK_MARKERS):
                live_group_index += 1
                active_live_group = f"scene_{live_group_index:02d}"
        if is_flashback:
            if not previous_was_flashback:
                flashback_index += 1
            planned_group = f"flashback_{flashback_index:02d}"
        else:
            planned_group = active_live_group
        group_id = (
            str(existing.get("group_id") or "").strip()
            if existing and not force
            else ""
        ) or planned_group
        groups.add(group_id)

        shot_number = int(shot.get("shot_number") or index)
        previous_number = last_shot_by_group.get(group_id, 0)
        beat_type = _beat_type(combined, index)
        video = shot.get("video_generation")
        video = video if isinstance(video, dict) else {}
        screen_direction = str(video.get("screen_direction") or "auto")
        if screen_direction not in {
            "left_to_right",
            "right_to_left",
            "static",
        }:
            screen_direction = (
                "left_to_right" if beat_type in {"action", "establish"} else "static"
            )
        names = _character_names(shot, known_names)
        cast_signature = "|".join(sorted(set(names)))
        match_anchor = _match_anchor(shot, names)
        exit_state = _exit_state(shot, scene)
        entry_state = (
            last_exit_by_group.get(group_id, "")
            or (
                "延续同组上一镜头的人物位置、视线、动作余势和环境状态"
                if previous_number
                else "建立人物、场景地标、主光方向和屏幕轴线"
            )
        )
        reference_denoise = 0.84
        if beat_type == "establish":
            reference_denoise = 0.88
        elif beat_type in {"reaction", "dialogue"}:
            reference_denoise = 0.82

        previous_cast = last_cast_by_group.get(group_id, "")
        reference_allowed = bool(
            previous_number
            and cast_signature
            and previous_cast
            and cast_signature == previous_cast
            and _reference_composition_compatible(shot)
            and last_reference_eligible_by_group.get(group_id, False)
        )
        screen_axis = (
            "保持场景既定180度轴线；人物左右位置和运动方向不得跨轴反转"
        )
        eyeline = (
            f"{'、'.join(names)}保持互相对应的左右视线，"
            "视线高度和目标位置跨镜头一致"
            if len(names) >= 2 or beat_type == "dialogue"
            else "保持主体视线目标与上一镜头一致"
        )
        match_action = (
            f"从“{entry_state[:180]}”继续，结束在“{exit_state[:180]}”"
        )
        bridge_prompt = (
            "开头短暂保持入场姿态和构图，动作只沿一个方向推进；"
            "结尾落在清晰稳定、可供下一镜头承接的姿态，避免突然转头、"
            "跨轴、跳位或在切点遮挡脸部"
        )
        planned = {
            "group_id": group_id,
            "beat_type": beat_type,
            "action_phase": _action_phase(beat_type),
            "entry_state": entry_state[:600],
            "exit_state": exit_state[:600],
            "match_anchor": match_anchor[:800],
            "cast_signature": cast_signature[:300],
            "reference_mode": (
                "previous_in_group" if reference_allowed else "none"
            ),
            "reference_shot_number": (
                previous_number if reference_allowed else 0
            ),
            "reference_denoise": reference_denoise,
            "transition_strategy": (
                "eyeline_cut" if beat_type == "dialogue" else "cut_on_action"
            ),
            "match_action": match_action[:500],
            "eyeline": eyeline[:300],
            "screen_axis": screen_axis[:300],
            "bridge_prompt": bridge_prompt[:600],
            "keyframe_prompt": _keyframe_prompt(
                beat_type=beat_type,
                entry_state=entry_state,
                exit_state=exit_state,
                match_anchor=match_anchor,
                screen_direction=screen_direction,
                bridge_prompt=bridge_prompt,
            ),
        }
        if existing and not force:
            for key, content in existing.items():
                if content not in ("", None, 0):
                    planned[key] = content
        if planned != original:
            shot["continuity_plan"] = planned
            stats["plans_updated"] += 1
        if int(planned.get("reference_shot_number") or 0):
            stats["reference_links"] += 1
        last_shot_by_group[group_id] = shot_number
        last_exit_by_group[group_id] = str(planned.get("exit_state") or "")
        last_cast_by_group[group_id] = str(
            planned.get("cast_signature") or cast_signature
        )
        last_reference_eligible_by_group[group_id] = (
            _reference_composition_compatible(shot)
        )
        previous_was_flashback = is_flashback

    for index, shot in enumerate(shots):
        plan = shot.get("continuity_plan")
        plan = plan if isinstance(plan, dict) else {}
        video = shot.get("video_generation")
        video = dict(video) if isinstance(video, dict) else {}
        if index + 1 >= len(shots):
            source_transition = str(shot.get("transition") or "").strip()
            transition_out = {
                "fade": "fade_black",
                "fade_black": "fade_black",
                "dissolve": "dissolve",
                "wipe": "match_cut",
                "match_cut": "match_cut",
                "cut": "cut",
            }.get(source_transition, "fade_black")
            strategy = transition_out
            transition_frames = (
                10
                if transition_out == "fade_black"
                else 8
                if transition_out == "dissolve"
                else 4
                if transition_out == "match_cut"
                else 0
            )
            match_action = "本章收束，人物动作减速并稳定停在明确的段尾状态"
        else:
            following = shots[index + 1]
            next_plan = following.get("continuity_plan")
            next_plan = next_plan if isinstance(next_plan, dict) else {}
            same_group = (
                str(plan.get("group_id") or "")
                == str(next_plan.get("group_id") or "")
            )
            current_beat = str(plan.get("beat_type") or "")
            next_beat = str(next_plan.get("beat_type") or "")
            same_cast = bool(
                str(plan.get("cast_signature") or "")
                and str(plan.get("cast_signature") or "")
                == str(next_plan.get("cast_signature") or "")
            )
            if not same_group:
                if "flashback" in {current_beat, next_beat}:
                    strategy = "dissolve"
                    transition_out = "dissolve"
                    transition_frames = 8
                else:
                    strategy = "fade_black"
                    transition_out = "fade_black"
                    transition_frames = 10
            elif same_cast:
                strategy = "match_cut"
                transition_out = "match_cut"
                transition_frames = 4
            elif "action" in {current_beat, next_beat}:
                strategy = "cut_on_action"
                transition_out = "match_cut"
                transition_frames = 3
            elif {current_beat, next_beat} & {"dialogue", "reaction"}:
                strategy = "eyeline_cut"
                transition_out = "match_cut"
                transition_frames = 2
            else:
                strategy = "match_cut"
                transition_out = "match_cut"
                transition_frames = 2
            match_action = (
                f"本镜头结束状态“{str(plan.get('exit_state') or '')[:180]}”"
                f"直接接入下一镜头开始状态“"
                f"{str(next_plan.get('entry_state') or '')[:180]}”"
            )
        if force or not str(plan.get("transition_strategy") or "").strip():
            plan["transition_strategy"] = strategy
            plan["match_action"] = match_action[:500]
        if force or not str(video.get("transition_out") or "").strip():
            video["transition_out"] = transition_out
            video["transition_frames"] = transition_frames
            video["handle_frames"] = max(
                int(video.get("handle_frames") or 0),
                8,
            )
        shot["continuity_plan"] = plan
        shot["video_generation"] = video

    stats["groups"] = len(groups)
    return stats
