"""项目清单与项目级配置 Schema。"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field


def utc_now() -> datetime:
    return datetime.now(UTC)


class OutputConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    width: int = Field(default=1280, ge=256, le=7680)
    height: int = Field(default=720, ge=256, le=4320)
    fps: int = Field(default=24, ge=1, le=120)


class ProjectConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.0"] = "1.0"
    llm_profile: str = "default"
    embedding_profile: str = "default"
    image_profile: str = "rtx3090"
    video_profile: str = "rtx3090"
    tts_profile: str = "default"
    output: OutputConfig = Field(default_factory=OutputConfig)


class ProjectManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.0"] = "1.0"
    project_id: str = Field(default_factory=lambda: str(uuid4()))
    slug: str
    display_name: str
    app_version: str = "0.1.0"
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

