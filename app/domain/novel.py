"""小说导入与结构化分析的版本化数据契约。"""
from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class EntityType(StrEnum):
    CHARACTER = "character"
    LOCATION = "location"
    ORGANIZATION = "organization"
    PROP = "prop"
    ABILITY = "ability"
    CREATURE = "creature"


class ReviewStatus(StrEnum):
    UNREVIEWED = "unreviewed"
    APPROVED = "approved"
    REJECTED = "rejected"
    EDITED = "edited"


class SourceDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.0"] = "1.0"
    document_id: str
    source_type: Literal["text", "markdown", "chapter_json_set"]
    original_name: str
    stored_path: str
    encoding: str
    byte_hash: str
    normalized_hash: str
    character_count: int = Field(ge=0)
    offset_basis: Literal["normalized_text", "chapter_content"] = "normalized_text"


class StandardChapter(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.0"] = "1.0"
    chapter_id: str = Field(pattern=r"^ch_\d{6,}$")
    order: int = Field(ge=1)
    title: str
    content: str = Field(min_length=1)
    source_document_id: str
    source_file: str
    source_start: int = Field(ge=0)
    source_end: int = Field(ge=0)
    content_hash: str

    @model_validator(mode="after")
    def validate_source_range(self) -> StandardChapter:
        if self.source_end <= self.source_start:
            raise ValueError("source_end 必须大于 source_start")
        return self


class EvidenceSpan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chapter_id: str
    start: int = Field(ge=0)
    end: int = Field(ge=0)
    quote: str = Field(min_length=1, max_length=300)

    @model_validator(mode="after")
    def validate_range(self) -> EvidenceSpan:
        if self.end <= self.start:
            raise ValueError("evidence end 必须大于 start")
        return self


class EntityMention(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mention_id: str
    surface_text: str = Field(min_length=1, max_length=128)
    entity_type: EntityType
    description: str = Field(default="", max_length=300)
    evidence: EvidenceSpan
    confidence: float = Field(ge=0.0, le=1.0)
    resolved_entity_id: str | None = None
    review_status: ReviewStatus = ReviewStatus.UNREVIEWED


class NarrativeEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: str
    sequence_index: int = Field(ge=0)
    summary: str = Field(min_length=1, max_length=500)
    participants: list[str] = Field(default_factory=list)
    location: str = ""
    importance: int = Field(default=1, ge=1, le=5)
    result: str = Field(default="", max_length=500)
    evidence: EvidenceSpan
    confidence: float = Field(ge=0.0, le=1.0)
    review_status: ReviewStatus = ReviewStatus.UNREVIEWED


class Dialogue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dialogue_id: str
    speaker: str = Field(min_length=1, max_length=128)
    addressee: str = Field(default="", max_length=128)
    text: str = Field(min_length=1, max_length=1000)
    emotion: str = Field(default="", max_length=64)
    evidence: EvidenceSpan
    confidence: float = Field(ge=0.0, le=1.0)
    review_status: ReviewStatus = ReviewStatus.UNREVIEWED


class StateChange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    change_id: str
    entity: str = Field(min_length=1, max_length=128)
    attribute: str = Field(min_length=1, max_length=128)
    before: str = Field(default="", max_length=300)
    after: str = Field(min_length=1, max_length=300)
    evidence: EvidenceSpan
    confidence: float = Field(ge=0.0, le=1.0)
    review_status: ReviewStatus = ReviewStatus.UNREVIEWED


class AnalysisProvenance(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model: str
    prompt_version: str
    input_hash: str
    chunk_count: int = Field(ge=1)


class ChapterAnalysis(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.0"] = "1.0"
    chapter_id: str
    mentions: list[EntityMention] = Field(default_factory=list)
    events: list[NarrativeEvent] = Field(default_factory=list)
    dialogues: list[Dialogue] = Field(default_factory=list)
    state_changes: list[StateChange] = Field(default_factory=list)
    summary: str = Field(default="", max_length=1000)
    adaptation_notes: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    provenance: AnalysisProvenance


class NovelImportResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: SourceDocument
    chapters: list[StandardChapter]

