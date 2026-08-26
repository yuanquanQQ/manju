"""分镜（Storyboard）领域模型。

一个 Episode 包含多个 Shot，每个 Shot 描述一个镜头，
包含可直接用于 AI 生图的场景描写和人物刻画。
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class CharacterAppearance(BaseModel):
    """单个人物在当前镜头中的外貌描写。"""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(description="人物名称")
    appearance: str = Field(
        min_length=1, max_length=300,
        description="外貌细节：发型、发色、脸型、五官特征、年龄感",
    )
    clothing: str = Field(
        default="",
        max_length=300,
        description="服饰细节：风格、颜色、材质、层次、配件",
    )
    pose: str = Field(
        default="",
        max_length=200,
        description="姿态与动作：身体姿态、手势、站位",
    )
    expression: str = Field(
        default="",
        max_length=150,
        description="面部表情：眼神、嘴角、眉宇间的细节",
    )


class EnvironmentDetail(BaseModel):
    """场景环境详情。"""

    model_config = ConfigDict(extra="forbid")

    layout: str = Field(
        default="",
        max_length=400,
        description="环境布局：前景、中景、远景的空间层次，主要物体的位置关系",
    )
    lighting: str = Field(
        default="",
        max_length=300,
        description="光影描写：光源方向（顶光/侧光/逆光/漫射）、强度、色温（暖/冷/中性）、阴影形态",
    )
    color_palette: str = Field(
        default="",
        max_length=200,
        description="色彩风格：主色调、辅助色、色彩饱和度、对比度",
    )
    atmosphere: str = Field(
        default="",
        max_length=200,
        description="氛围效果：雾气、粒子、天气、粉尘、气流等环境特效",
    )


class ShotVideoGeneration(BaseModel):
    """AI video instructions generated together with the storyboard shot."""

    model_config = ConfigDict(extra="forbid")

    engine_profile: str = Field(default="minimax_h3_fl2va", max_length=64)
    subject_motion: str = Field(default="", max_length=1600)
    environment_motion: str = Field(default="", max_length=1200)
    continuity_constraints: str = Field(default="", max_length=1600)
    negative_prompt: str = Field(default="", max_length=1600)
    motion_prompt: str = Field(default="", max_length=1600)
    end_frame_prompt: str = Field(default="", max_length=2400)
    end_frame_prompt_version: int = Field(default=0, ge=0)
    routing_reason: str = Field(default="", max_length=500)
    routing_version: int = Field(default=0, ge=0)
    routing_locked: bool = False
    native_audio_mode: Literal[
        "off",
        "ambience_sfx_music",
        "native_full",
    ] = "ambience_sfx_music"
    dialogue_prompt: str = Field(default="", max_length=1600)
    sound_effect_prompt: str = Field(default="", max_length=800)
    music_prompt: str = Field(default="", max_length=800)
    camera_movement: str = Field(default="slow_push", max_length=64)
    motion_strength: Literal["low", "medium", "high"] = "low"
    screen_direction: Literal[
        "auto",
        "left_to_right",
        "right_to_left",
        "static",
    ] = "auto"
    transition_out: Literal["cut", "match_cut", "dissolve", "fade_black"] = "cut"
    transition_frames: int = Field(default=8, ge=0, le=48)
    handle_frames: int = Field(default=8, ge=0, le=48)
    candidate_count: int = Field(default=1, ge=1, le=4)
    duration_seconds: float = Field(default=3.0, ge=1.0, le=15.0)
    source_image: str = ""
    end_image: str = ""
    selected_video: str = ""
    manifest_file: str = ""


class ShotImageGeneration(BaseModel):
    """Generated keyframe binding and review metadata."""

    model_config = ConfigDict(extra="allow")

    selected_image: str = ""
    selected_source: str = ""
    manifest: str = ""
    candidates: list[dict[str, object]] = Field(default_factory=list)
    qc_status: Literal["", "pending", "approved", "rejected"] = ""
    qc_note: str = Field(default="", max_length=1000)
    qc_checked_at: str = ""
    qc_selected_image: str = ""


class ShotSpeechSegment(BaseModel):
    """One natural spoken-text segment used to plan dialogue coverage."""

    model_config = ConfigDict(extra="forbid")

    index: int = Field(ge=1)
    text: str = Field(min_length=1, max_length=500)
    estimated_duration_seconds: float = Field(default=0.0, ge=0.0)


class ShotAudioGeneration(BaseModel):
    """Editable speech and subtitle settings for one shot."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    mode: Literal["auto_narration", "dialogue", "mute"] = "auto_narration"
    speaker: str = Field(default="旁白", max_length=80)
    text: str = Field(default="", max_length=1600)
    voice_id: str = Field(default="zh-CN-YunyangNeural", max_length=128)
    engine: Literal["edge_tts", "cosyvoice"] = "edge_tts"
    reference_audio: str = ""
    reference_text: str = Field(default="", max_length=1600)
    instruct_text: str = Field(default="", max_length=500)
    fallback_to_edge: bool = True
    rate: str = Field(default="+5%", max_length=16)
    volume: str = Field(default="+0%", max_length=16)
    pitch: str = Field(default="-5Hz", max_length=16)
    subtitle_enabled: bool = True
    preserve_source_audio: bool = True
    source_audio_gain_db: float = Field(default=-6.0, ge=-30.0, le=6.0)
    ducking_gain_db: float = Field(default=-12.0, ge=-30.0, le=0.0)
    voice_profile_id: str = Field(default="", max_length=120)
    voice_assignment_mode: Literal["", "auto", "manual"] = ""
    audio_file: str = ""
    subtitle_file: str = ""
    manifest_file: str = ""
    estimated_duration_seconds: float = Field(default=0.0, ge=0.0)
    planned_timeline_duration_seconds: float = Field(default=0.0, ge=0.0)
    timing_status: Literal[
        "unplanned",
        "ready",
        "needs_regeneration",
        "needs_split",
        "mute",
        "no_text",
    ] = "unplanned"
    recommended_segments: int = Field(default=1, ge=1, le=20)
    segments: list[ShotSpeechSegment] = Field(default_factory=list)


class ShotContinuityPlan(BaseModel):
    """Shot-to-shot state shared by keyframe and video generation."""

    model_config = ConfigDict(extra="forbid")

    group_id: str = Field(default="scene_01", max_length=80)
    beat_type: Literal[
        "establish",
        "action",
        "reaction",
        "dialogue",
        "flashback",
    ] = "dialogue"
    action_phase: Literal[
        "setup",
        "anticipation",
        "reaction",
        "interaction",
        "impact",
        "recovery",
    ] = "anticipation"
    entry_state: str = Field(default="", max_length=600)
    exit_state: str = Field(default="", max_length=600)
    match_anchor: str = Field(default="", max_length=800)
    cast_signature: str = Field(default="", max_length=300)
    reference_mode: Literal["none", "previous_in_group"] = "none"
    reference_shot_number: int = Field(default=0, ge=0)
    reference_denoise: float = Field(default=0.76, ge=0.45, le=0.95)
    transition_strategy: Literal[
        "cut",
        "cut_on_action",
        "eyeline_cut",
        "match_cut",
        "dissolve",
        "fade_black",
    ] = "cut"
    match_action: str = Field(default="", max_length=500)
    eyeline: str = Field(default="", max_length=300)
    screen_axis: str = Field(default="", max_length=300)
    bridge_prompt: str = Field(default="", max_length=600)
    keyframe_prompt: str = Field(default="", max_length=1600)


class ShotLipSyncGeneration(BaseModel):
    """Target-face lip-sync settings prepared before the GPU worker is online."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    engine: Literal["latentsync_1_6", "latentsync_1_5"] = "latentsync_1_6"
    target_character: str = Field(default="", max_length=80)
    mode: Literal[
        "auto_single_face",
        "speaker_tracking",
        "manual_anchor",
    ] = "speaker_tracking"
    target_face_anchor: tuple[float, float, float, float] | None = None
    face_reference: str = ""
    inference_steps: int = Field(default=20, ge=10, le=50)
    guidance_scale: float = Field(default=1.5, ge=1.0, le=3.0)
    status: Literal[
        "disabled",
        "pending",
        "ready",
        "processing",
        "succeeded",
        "failed",
        "needs_face_selection",
    ] = "disabled"
    sync_score: float = Field(default=0.0, ge=0.0)
    source_video: str = ""
    audio_file: str = ""
    output_file: str = ""
    previous_output_file: str = ""
    manifest_file: str = ""
    generated_at: str = ""
    elapsed_seconds: float = Field(default=0.0, ge=0.0)
    error: str = Field(default="", max_length=1000)


class Shot(BaseModel):
    """单个镜头，包含视觉级场景描写和人物刻画。"""

    model_config = ConfigDict(extra="forbid")

    shot_number: int = Field(ge=1, description="镜头序号")
    scene_description: str = Field(
        min_length=1, max_length=800,
        description="画面描述（视觉内容，100-300 字，细致到可生图）",
    )
    environment: EnvironmentDetail = Field(
        default_factory=EnvironmentDetail,
        description="场景环境详情",
    )
    characters: list[CharacterAppearance] = Field(
        default_factory=list,
        description="出场人物及其外貌刻画",
    )
    camera_angle: str = Field(
        default="medium shot",
        description="镜头角度：close-up / medium shot / wide shot / panoramic / low angle / high angle / POV / over-shoulder / dutch angle",
    )
    camera_movement: str = Field(
        default="static",
        description="镜头运动：static / pan / tilt / zoom / dolly / handheld / crane / tracking",
    )
    emotion: str = Field(
        default="neutral",
        description="情绪氛围关键词（影响生图风格）",
    )
    dialogue: str = Field(
        default="",
        max_length=500,
        description="对白/旁白（如有）",
    )
    sound_effect: str = Field(
        default="",
        max_length=200,
        description="音效提示",
    )
    duration_seconds: float = Field(
        default=3.0, ge=1.0, le=30.0,
        description="预估时长（秒）",
    )
    transition: str = Field(
        default="cut",
        description="转场：cut / fade / dissolve / wipe",
    )
    image_prompt: str = Field(
        default="",
        max_length=600,
        description="可直接用于 SDXL/ComfyUI 生图的正向 Prompt（英文，包含质量标签和风格描述）",
    )
    style_preset: str = Field(default="真人电影", max_length=64)
    image_generation: ShotImageGeneration = Field(
        default_factory=ShotImageGeneration,
        description="关键帧候选、选用结果和人工质检状态",
    )
    video_generation: ShotVideoGeneration = Field(
        default_factory=ShotVideoGeneration,
        description="随分镜自动生成的图生视频动作、连续性和运镜参数",
    )
    continuity_plan: ShotContinuityPlan = Field(
        default_factory=ShotContinuityPlan,
        description="镜头连续组、入出状态、匹配锚点与上一镜头视觉参考",
    )
    audio_generation: ShotAudioGeneration = Field(
        default_factory=ShotAudioGeneration,
        description="逐镜头配音、角色音色和字幕参数",
    )
    lip_sync: ShotLipSyncGeneration = Field(
        default_factory=ShotLipSyncGeneration,
        description="说话人物、目标脸和口型同步任务参数",
    )


class Episode(BaseModel):
    """一集的分镜脚本。"""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.0"] = "1.0"
    episode_number: int = Field(ge=1)
    episode_title: str = Field(default="", max_length=200)
    chapter_ids: list[str] = Field(default_factory=list)
    artifact_binding_policy: Literal[
        "legacy_fallback",
        "explicit_only",
    ] = "legacy_fallback"
    character_profiles: dict[str, str] = Field(default_factory=dict)
    character_visual_fingerprints: dict[str, str] = Field(default_factory=dict)
    character_styles: dict[str, str] = Field(default_factory=dict)
    character_generation_presets: dict[str, str] = Field(default_factory=dict)
    shots: list[Shot] = Field(min_length=1)
    summary: str = Field(default="", max_length=500, description="本集剧情梗概")
