"""Domain contracts for speech synthesis and dubbed episode composition."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

DubbingMode = Literal["auto_narration", "dialogue", "mute"]
TtsEngine = Literal["edge_tts", "cosyvoice"]


class DubbingLineSpec(BaseModel):
    """Speech instructions for one storyboard shot."""

    model_config = ConfigDict(extra="forbid")

    episode_number: int = Field(ge=1)
    shot_number: int = Field(ge=1)
    source_video: Path
    prepared_audio: Path | None = None
    mode: DubbingMode = "auto_narration"
    text: str = Field(default="", max_length=1600)
    speaker: str = Field(default="旁白", max_length=80)
    voice_id: str = Field(default="zh-CN-YunyangNeural", max_length=128)
    engine: TtsEngine = "edge_tts"
    reference_audio: Path | None = None
    reference_text: str = Field(default="", max_length=1600)
    instruct_text: str = Field(default="", max_length=500)
    fallback_to_edge: bool = True
    rate: str = Field(default="+5%", pattern=r"^[+-]\d{1,3}%$")
    volume: str = Field(default="+0%", pattern=r"^[+-]\d{1,3}%$")
    pitch: str = Field(default="-5Hz", pattern=r"^[+-]\d{1,3}Hz$")
    subtitle_enabled: bool = True
    preserve_source_audio: bool = True
    source_audio_gain_db: float = Field(default=-6.0, ge=-30.0, le=6.0)
    ducking_gain_db: float = Field(default=-12.0, ge=-30.0, le=0.0)
    lead_seconds: float = Field(default=0.15, ge=0.0, le=2.0)
    tail_seconds: float = Field(default=0.25, ge=0.0, le=3.0)


class DubbingArtifactMetadata(BaseModel):
    """Traceability data stored next to synthesized speech and dubbed videos."""

    model_config = ConfigDict(extra="allow")

    schema_version: str = "1.0"
    kind: str
    engine: str
    episode_number: int = Field(ge=1)
    shot_number: int = Field(default=0, ge=0)
    speaker: str = ""
    text: str = ""
    voice_id: str = ""
    rate: str = ""
    volume: str = ""
    pitch: str = ""
    source_video: str = ""
    audio_file: str = ""
    subtitle_file: str = ""
    output_file: str = ""
    generated_at: str
    duration_seconds: float = Field(default=0.0, ge=0.0)
    job_id: str
