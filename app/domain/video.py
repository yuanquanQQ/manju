"""Domain contracts for shot-video generation and episode previews."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

VideoEngineProfile = Literal[
    "comic_motion",
    "minimax_h3_fl2va",
]
MotionStrength = Literal["low", "medium", "high"]
ScreenDirection = Literal["auto", "left_to_right", "right_to_left", "static"]
TransitionKind = Literal["cut", "match_cut", "dissolve", "fade_black"]
NativeAudioMode = Literal["off", "ambience_sfx_music", "native_full"]


class VideoRenderSpec(BaseModel):
    """Stable, engine-independent instructions for one shot clip."""

    model_config = ConfigDict(extra="forbid")

    episode_number: int = Field(ge=1)
    shot_number: int = Field(ge=1)
    source_image: Path
    end_image: Path | None = None
    scene_description: str = Field(default="", max_length=1200)
    subject_motion: str = Field(default="", max_length=1600)
    environment_motion: str = Field(default="", max_length=1200)
    continuity_constraints: str = Field(default="", max_length=1600)
    negative_prompt: str = Field(default="", max_length=1600)
    motion_prompt: str = Field(default="", max_length=1600)
    native_audio_mode: NativeAudioMode = "ambience_sfx_music"
    dialogue_prompt: str = Field(default="", max_length=1600)
    sound_effect_prompt: str = Field(default="", max_length=800)
    music_prompt: str = Field(default="", max_length=800)
    camera_movement: str = Field(default="auto", max_length=64)
    motion_strength: MotionStrength = "low"
    screen_direction: ScreenDirection = "auto"
    transition_out: TransitionKind = "cut"
    transition_frames: int = Field(default=8, ge=0, le=48)
    handle_frames: int = Field(default=8, ge=0, le=48)
    candidate_count: int = Field(default=1, ge=1, le=4)
    duration_seconds: float = Field(default=3.0, ge=1.0, le=15.0)
    fps: int = Field(default=24, ge=12, le=60)
    width: int = Field(default=1280, ge=320, le=3840)
    height: int = Field(default=720, ge=240, le=2160)
    engine_profile: VideoEngineProfile = "comic_motion"

    @model_validator(mode="after")
    def validate_dimensions(self) -> VideoRenderSpec:
        if self.width % 2 or self.height % 2:
            raise ValueError("视频宽高必须为偶数")
        if self.engine_profile != "comic_motion" and (
            self.width % 16 or self.height % 16
        ):
            raise ValueError("AI 视频宽高必须是 16 的倍数")
        if self.engine_profile == "minimax_h3_fl2va":
            if self.width % 32 or self.height % 32:
                raise ValueError("MiniMax H3 视频宽高必须是 32 的倍数")
            if self.fps != 24:
                raise ValueError("MiniMax H3 固定使用 24fps")
        return self


class EpisodeClipSpec(BaseModel):
    """One approved shot on an episode composition timeline."""

    model_config = ConfigDict(extra="forbid")

    path: Path
    shot_number: int = Field(ge=1)
    duration_seconds: float = Field(default=3.0, gt=0, le=60)
    transition_out: TransitionKind = "cut"
    transition_frames: int = Field(default=8, ge=0, le=48)


class VideoArtifactMetadata(BaseModel):
    """Traceability data persisted next to every generated clip."""

    model_config = ConfigDict(extra="forbid")

    schema_version: str = "1.0"
    engine_profile: str
    episode_number: int
    shot_number: int
    source_image: str
    end_image: str = ""
    output_file: str
    subject_motion: str = ""
    environment_motion: str = ""
    continuity_constraints: str = ""
    negative_prompt: str = ""
    motion_prompt: str = ""
    native_audio_mode: str = "off"
    dialogue_prompt: str = ""
    sound_effect_prompt: str = ""
    music_prompt: str = ""
    native_audio: bool = False
    camera_movement: str
    motion_strength: MotionStrength = "low"
    screen_direction: ScreenDirection = "auto"
    transition_out: TransitionKind = "cut"
    transition_frames: int = 8
    handle_frames: int = 8
    candidate_count: int = 1
    candidate_index: int = Field(default=1, ge=1)
    technical_qc: dict[str, Any] = Field(default_factory=dict)
    approval_status: str = "pending_visual_motion_audio_review"
    duration_seconds: float
    fps: int
    width: int
    height: int
    generated_at: str
    elapsed_seconds: float = Field(ge=0)
    job_id: str
    ffmpeg_version: str = ""
