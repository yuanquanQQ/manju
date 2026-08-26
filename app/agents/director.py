"""导演 Agent：将章节结构化分析转换为视觉级分镜脚本。

调用 LLM，输入章节的事件、对话、实体描述，
输出每镜包含可落地的场景描写、人物刻画、环境细节和生图 Prompt。
"""
from __future__ import annotations

from typing import Any

from app.adapters.llm import OpenAICompatibleLLM, StructuredLLM
from app.core.logger import logger
from app.domain.novel import ChapterAnalysis
from app.domain.storyboard import (
    CharacterAppearance,
    EnvironmentDetail,
    Episode,
    Shot,
    ShotContinuityPlan,
    ShotVideoGeneration,
)
from app.pipeline.character_identity import derive_visual_fingerprints
from app.pipeline.continuity import plan_episode_continuity
from app.pipeline.pacing import normalize_episode_duration, pacing_target

DIRECTOR_SYSTEM_PROMPT = """你是漫剧导演兼视觉设计，负责把小说章节转为可直接用于 AI 生图的分镜脚本。

核心要求：
1. 每个完整章节必须生成 18-28 个镜头，总时长不得少于 60 秒。禁止把整段情节压缩成一张概括性插画。
   每个核心事件至少拆成“建立/动作准备/动作结果/人物反应”中的 2-4 个镜头；
   对话必须使用说话者、听者反应、过肩或细节插入组成镜头组。
2. 每个镜头的 scene_description 是 50-140 字中文画面描写，包含人物位置关系、动作细节、关键背景元素的精确刻画，避免概括性总结和剧情概述。
3. 每个镜头必须有 environment 对象，包含：
   - layout: 前景/中景/远景的空间层次（10-60字）
   - lighting: 光源方向（顶光/侧光/逆光/漫射）、强度（强/柔/暗）、色温（暖/冷/中性）、阴影形态（10-50字）
   - color_palette: 主色调和辅助色（10-40字，如"深蓝与金色为主，暗红点缀"）
   - atmosphere: 环境特效描述（10-40字，如"薄雾缭绕""落叶飘飞""灵气光点浮动"）
4. 每个出场人物必须出现在 characters 数组中，每人包含：
   - name: 人物名
   - appearance: 外貌（发型发色、脸型、五官、年龄感，20-80字）
   - clothing: 服饰（风格、颜色、材质、层次、配件，20-80字）
   - pose: 姿态动作（身体姿态、手势、站位，10-50字）
   - expression: 表情（眼神、嘴角、眉宇细节，10-40字）
   同一人物在全章必须重复完全一致的脸型、眼型、鼻形、下颌、发型轮廓、服装主色和标志配件；
   不同人物必须至少有三项明显不可互换的差异。禁止所有年轻男性共享同一张脸、同一发冠或同色衣服。
5. image_prompt 是 50-150 词的英文正向 Prompt，以 "masterpiece, best quality," 开头，包含画风标签（如 photorealistic, cinematic, chinese fantasy, xianxia）、场景关键词和人物外观关键词。
   只要镜头有人物，主要人物必须使用中景或中近景，脸部完整无遮挡、双眼清晰可见，
   额头与下巴不得裁切；多人场景最多两名主要人物位于前景，其余人物退到背景并保持分离。
   提示词必须包含 real human actors, video-safe first frame，并排除 anime、illustration、CGI。
6. camera_angle 从以下选择：close-up / medium shot / wide shot / panoramic / low angle / high angle / POV / over-shoulder / dutch angle
7. camera_movement 从以下选择：static / pan / tilt / zoom / dolly / handheld / crane / tracking
8. duration_seconds: 反应/细节镜头 2.5-3.5s，对话镜头 3-4.5s，动作镜头 3-5s，建立镜头 3-4s；
   完整章节所有镜头合计必须达到 60-90 秒
   dialogue 只写本镜头实际说出的台词或旁白，格式优先为“角色名：台词”；
   单句尽量控制在 6-24 个汉字，画面无人说话时可以留空，后续配音模块会自动生成旁白。
9. 禁止输出任何思考过程、分析文本或解释。回复必须且只能是纯 JSON 对象。"""

DIRECTOR_SYSTEM_PROMPT = DIRECTOR_SYSTEM_PROMPT.replace(
    "9. 禁止输出",
    """9. 每个镜头必须同时生成 video_generation：
   - subject_motion: 只写画面中真实可见、可连续执行的人物动作，不要复述剧情
   - environment_motion: 雾、风、衣摆、树叶、光影等可见环境运动
   - motion_prompt: 合并人物与环境动作，使用简短明确的中文指令
   - continuity_constraints: 要保持稳定的人脸、发型、服装、道具、站位和背景
   - negative_prompt: 英文负面词，至少包含 face morphing, identity change, extra limbs, flicker, camera shake
   - camera_movement: slow_push / slow_pull / pan_left / pan_right / tilt_up / tilt_down / still
   - motion_strength: low / medium / high
   - screen_direction: auto / left_to_right / right_to_left / static
   - transition_out: cut / match_cut / dissolve / fade_black
10. 相邻镜头不是独立插画，必须同时生成 continuity_plan：
   - group_id: 同一地点、同一时间和同一组人物使用同一个连续组；闪回必须单独成组
   - beat_type: establish / action / reaction / dialogue / flashback
   - action_phase: setup / anticipation / reaction / interaction / impact / recovery
   - entry_state: 本镜头开始时承接上一镜头的人物姿态、视线、道具和环境状态
   - exit_state: 本镜头结束时必须留给下一镜头承接的可见状态
   - match_anchor: 必须跨镜头一致的人脸、服装、道具、主光、背景地标、屏幕方向
   - transition_strategy: cut / cut_on_action / eyeline_cut / match_cut / dissolve / fade_black
   - match_action: 上一镜头结束动作与本镜头开始动作如何精确衔接
   - eyeline: 对话双方的左右视线方向
   - screen_axis: 180度轴线和人物左右位置
   - bridge_prompt: 用于首尾各数帧稳定衔接的明确画面指令
   - 首帧应选“主要动作发生前一刻”，有重心、动作线和运动空间，禁止站桩、正面对称摆拍
11. 禁止输出""",
)


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
    if not motion_prompt:
        motion_prompt = "；".join(
            part for part in (subject_motion, environment_motion) if part
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
        motion_prompt=motion_prompt[:1600],
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
            "appearance": (
                str(profiles.get(name) or f"已锁定定妆的{name}")[:300]
            ),
            "clothing": "严格沿用已锁定定妆的服装主色、材质与标志配件",
            "pose": visible_action[:200],
            "expression": expression[:150],
        }
        for name in names
    ]
    visual_prompt = str(item.get("visual_prompt") or scene).strip()
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
        "emotion": str(item.get("emotion") or "克制的戏剧张力")[:100],
        "dialogue": str(item.get("dialogue") or "")[:500],
        "sound_effect": str(item.get("sound_effect") or "")[:200],
        "duration_seconds": duration,
        "transition": source_transition,
        "image_prompt": (
            "masterpiece, best quality, photorealistic live-action Chinese "
            f"xianxia cinematic scene, {visual_prompt}, {framing}, real human "
            "actors, natural skin texture, coherent anatomy, cinematic lighting, "
            "video-safe first frame, no anime, no illustration, no CGI, no text, "
            "no logo, no watermark"
        )[:600],
        "style_preset": "真人电影",
        "continuity_plan": {
            "group_id": "scene_01",
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
                )
            )
        except Exception as exc:
            logger.warning(f"Shot {idx} 解析失败: {exc}")
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
) -> Episode:
    """将单章分析结果转为视觉级分镜。"""
    client = llm or OpenAICompatibleLLM()
    summary_text = _build_analysis_summary(analysis)
    profiles = dict(character_profiles or {})
    target = pacing_target(
        len(source_text),
        event_count=len(analysis.events),
        dialogue_count=len(analysis.dialogues),
    )
    segments = _source_segments(source_text, target_shots=target.target_shots)
    raw_shots: list[dict[str, Any]] = []
    format_prompt = (
        "只输出紧凑镜头节拍，不要输出 environment、完整人物外貌、"
        "continuity_plan 或 video_generation；程序会根据定妆和连续性规则自动补齐。"
        "每项只保留 scene_description、characters（姓名字符串数组）、location、"
        "camera_angle、camera_movement、beat_type、visible_action、expression、"
        "dialogue、duration_seconds、visual_prompt、lighting、atmosphere、"
        "screen_direction、transition_hint。"
    )
    for segment_index, (segment_text, segment_target) in enumerate(
        segments,
        start=1,
    ):
        segment_min = max(8, segment_target - 1)
        prompt = (
            f"请把本章第 {segment_index}/{len(segments)} 个连续段落转换为"
            f" {segment_target}-{segment_target + 2} 个镜头，至少 {segment_min} 个。"
            "本段中的铺垫、物件特写、动作过程、对话双方反应和段尾状态都必须呈现；"
            "不要跨越或概括本段事件。每镜只表现一个可见动作或一个明确反应。\n\n"
            f"全章节奏目标：{target.target_shots} 个左右、"
            f"{target.target_duration_seconds:.0f} 秒，最低 "
            f"{target.min_shots} 镜头且不少于 60 秒。\n\n"
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
            if len(segment_items) >= segment_min:
                break
            logger.warning(
                f"章节 {analysis.chapter_id} 第 {segment_index} 段仅生成 "
                f"{len(segment_items)} 个镜头，要求至少 {segment_min}，正在重试"
            )
        if len(segment_items) < segment_min:
            raise RuntimeError(
                f"章节 {analysis.chapter_id} 第 {segment_index} 段镜头密度不足："
                f"{len(segment_items)}/{segment_min}"
            )
        raw_shots.extend(
            _expand_compact_beat(item, profiles=profiles)
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

    # 重新编号确保连续
    for i, shot in enumerate(shots, start=1):
        shot.shot_number = i

    fingerprints = derive_visual_fingerprints(
        profiles,
        shots,
        existing=character_visual_fingerprints,
    )
    return Episode(
        episode_number=episode_number,
        episode_title=episode_title or f"第 {episode_number} 集",
        chapter_ids=[analysis.chapter_id],
        artifact_binding_policy="explicit_only",
        character_profiles=profiles,
        character_visual_fingerprints=fingerprints,
        character_styles=dict(character_styles or {}),
        character_generation_presets=dict(
            character_generation_presets or {}
        ),
        shots=shots,
        summary=analysis.summary[:500],
    )


def _COMPACT_BEAT_OUTPUT_SCHEMA() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "shots": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "scene_description": {"type": "string"},
                        "characters": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                        "location": {"type": "string"},
                        "camera_angle": {"type": "string"},
                        "camera_movement": {"type": "string"},
                        "beat_type": {"type": "string"},
                        "visible_action": {"type": "string"},
                        "expression": {"type": "string"},
                        "dialogue": {"type": "string"},
                        "sound_effect": {"type": "string"},
                        "duration_seconds": {"type": "number"},
                        "visual_prompt": {"type": "string"},
                        "lighting": {"type": "string"},
                        "atmosphere": {"type": "string"},
                        "emotion": {"type": "string"},
                        "motion_strength": {"type": "string"},
                        "screen_direction": {"type": "string"},
                        "transition_hint": {"type": "string"},
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
