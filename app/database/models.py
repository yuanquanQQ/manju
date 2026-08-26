"""SQLAlchemy ORM 模型。

设计原则：每条新信息都落到独立行，便于跨章聚合与检索。
- chapters：章节原始记录
- characters：人物（按 name 唯一）
- character_appearances：人物在哪些章节出现
- scenes：场景/地点
- scene_appearances：场景在哪些章节出现
- events：事件（每章每事件一行）
- chapter_summaries：章节摘要
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Chapter(Base):
    __tablename__ = "chapters"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    chapter_id: Mapped[int] = mapped_column(Integer, unique=True, index=True)
    title: Mapped[str] = mapped_column(String(255))
    content: Mapped[str] = mapped_column(Text)
    source_path: Mapped[str] = mapped_column(String(512), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    characters: Mapped[list[Character]] = relationship(
        "Character", secondary="character_appearances", back_populates="chapters"
    )


class Character(Base):
    __tablename__ = "characters"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    description: Mapped[str] = mapped_column(Text, default="")
    first_chapter_id: Mapped[int] = mapped_column(Integer, default=0)

    chapters: Mapped[list[Chapter]] = relationship(
        "Chapter", secondary="character_appearances", back_populates="characters"
    )


class CharacterAppearance(Base):
    __tablename__ = "character_appearances"
    __table_args__ = (
        UniqueConstraint("character_id", "chapter_id", name="uq_char_chapter"),
        Index("ix_char_app_chapter", "chapter_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    character_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("characters.id", ondelete="CASCADE")
    )
    chapter_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("chapters.id", ondelete="CASCADE")
    )


class Scene(Base):
    __tablename__ = "scenes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    description: Mapped[str] = mapped_column(Text, default="")
    first_chapter_id: Mapped[int] = mapped_column(Integer, default=0)


class SceneAppearance(Base):
    __tablename__ = "scene_appearances"
    __table_args__ = (
        UniqueConstraint("scene_id", "chapter_id", name="uq_scene_chapter"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    scene_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("scenes.id", ondelete="CASCADE")
    )
    chapter_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("chapters.id", ondelete="CASCADE")
    )


class Event(Base):
    __tablename__ = "events"
    __table_args__ = (Index("ix_event_chapter", "chapter_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    chapter_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("chapters.id", ondelete="CASCADE")
    )
    summary: Mapped[str] = mapped_column(Text)
    importance: Mapped[int] = mapped_column(Integer, default=1)  # 1-5
    characters_json: Mapped[str] = mapped_column(Text, default="[]")  # 参与人物
    location: Mapped[str] = mapped_column(String(128), default="")


class ChapterSummary(Base):
    __tablename__ = "chapter_summaries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    chapter_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("chapters.id", ondelete="CASCADE"), unique=True
    )
    summary: Mapped[str] = mapped_column(Text)


class SchemaMetadata(Base):
    """数据库自身的版本与迁移元数据。"""

    __tablename__ = "schema_metadata"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(String(255))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        server_default=func.now(),
        onupdate=func.now(),
    )


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (
        Index("ix_jobs_status_priority", "status", "priority"),
        Index("ix_jobs_input_hash", "input_hash"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    job_type: Mapped[str] = mapped_column(String(128), index=True)
    status: Mapped[str] = mapped_column(String(24), index=True)
    priority: Mapped[int] = mapped_column(Integer, default=0)
    progress: Mapped[float] = mapped_column(Float, default=0.0)
    attempt: Mapped[int] = mapped_column(Integer, default=0)
    max_retries: Mapped[int] = mapped_column(Integer, default=3)
    input_hash: Mapped[str] = mapped_column(String(80), default="")
    payload_json: Mapped[str] = mapped_column(Text, default="{}")
    result_json: Mapped[str] = mapped_column(Text, default="{}")
    error_code: Mapped[str] = mapped_column(String(128), default="")
    error_message: Mapped[str] = mapped_column(Text, default="")
    pause_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        server_default=func.now(),
        onupdate=func.now(),
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class JobDependency(Base):
    __tablename__ = "job_dependencies"
    __table_args__ = (
        UniqueConstraint("job_id", "depends_on_job_id", name="uq_job_dependency"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("jobs.id", ondelete="CASCADE"),
        index=True,
    )
    depends_on_job_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("jobs.id", ondelete="CASCADE"),
        index=True,
    )


class Artifact(Base):
    __tablename__ = "artifacts"
    __table_args__ = (
        UniqueConstraint("relative_path", name="uq_artifact_relative_path"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    job_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("jobs.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    kind: Mapped[str] = mapped_column(String(128), index=True)
    relative_path: Mapped[str] = mapped_column(String(1024))
    sha256: Mapped[str] = mapped_column(String(80))
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    metadata_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class Review(Base):
    __tablename__ = "reviews"
    __table_args__ = (
        Index("ix_reviews_subject", "subject_type", "subject_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    subject_type: Mapped[str] = mapped_column(String(64))
    subject_id: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(24), default="unreviewed")
    comment: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        server_default=func.now(),
        onupdate=func.now(),
    )


class SettingsSnapshot(Base):
    __tablename__ = "settings_snapshots"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    job_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("jobs.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    settings_json: Mapped[str] = mapped_column(Text)
    settings_hash: Mapped[str] = mapped_column(String(80), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class NovelSourceDocument(Base):
    __tablename__ = "source_documents"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    source_type: Mapped[str] = mapped_column(String(32))
    original_name: Mapped[str] = mapped_column(String(512))
    stored_path: Mapped[str] = mapped_column(String(1024))
    encoding: Mapped[str] = mapped_column(String(32))
    byte_hash: Mapped[str] = mapped_column(String(80), index=True)
    normalized_hash: Mapped[str] = mapped_column(String(80), index=True)
    character_count: Mapped[int] = mapped_column(Integer, default=0)
    offset_basis: Mapped[str] = mapped_column(String(32))
    imported_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class CompiledChapter(Base):
    __tablename__ = "compiled_chapters"
    __table_args__ = (
        Index("ix_compiled_chapters_order", "chapter_order"),
        Index("ix_compiled_chapters_active_order", "active", "chapter_order"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    source_document_id: Mapped[str] = mapped_column(
        String(40),
        ForeignKey("source_documents.id", ondelete="RESTRICT"),
        index=True,
    )
    chapter_order: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String(255))
    content: Mapped[str] = mapped_column(Text)
    source_file: Mapped[str] = mapped_column(String(1024))
    source_start: Mapped[int] = mapped_column(Integer)
    source_end: Mapped[int] = mapped_column(Integer)
    content_hash: Mapped[str] = mapped_column(String(80), index=True)
    schema_version: Mapped[str] = mapped_column(String(16), default="1.0")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        server_default=func.now(),
        onupdate=func.now(),
    )


class ChapterAnalysisRun(Base):
    __tablename__ = "chapter_analysis_runs"
    __table_args__ = (
        Index(
            "ix_analysis_reuse",
            "chapter_id",
            "input_hash",
            "model",
            "prompt_version",
            "status",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    chapter_id: Mapped[str] = mapped_column(
        String(32),
        ForeignKey("compiled_chapters.id", ondelete="CASCADE"),
        index=True,
    )
    status: Mapped[str] = mapped_column(String(24), index=True)
    schema_version: Mapped[str] = mapped_column(String(16))
    model: Mapped[str] = mapped_column(String(255))
    prompt_version: Mapped[str] = mapped_column(String(64))
    input_hash: Mapped[str] = mapped_column(String(80))
    output_json: Mapped[str] = mapped_column(Text, default="{}")
    warnings_json: Mapped[str] = mapped_column(Text, default="[]")
    error_message: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class Entity(Base):
    __tablename__ = "entities"
    __table_args__ = (
        UniqueConstraint("entity_type", "canonical_name", name="uq_entity_type_name"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    entity_type: Mapped[str] = mapped_column(String(32), index=True)
    canonical_name: Mapped[str] = mapped_column(String(128), index=True)
    description: Mapped[str] = mapped_column(Text, default="")
    resolution_status: Mapped[str] = mapped_column(String(24), default="unreviewed")
    first_chapter_order: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        server_default=func.now(),
        onupdate=func.now(),
    )


class EntityAlias(Base):
    __tablename__ = "entity_aliases"
    __table_args__ = (
        UniqueConstraint("entity_id", "alias", name="uq_entity_alias"),
        Index("ix_entity_alias_value", "alias"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    entity_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("entities.id", ondelete="CASCADE"),
    )
    alias: Mapped[str] = mapped_column(String(128))
    evidence_chapter_id: Mapped[str] = mapped_column(String(32), default="")


class EntityMentionRecord(Base):
    __tablename__ = "entity_mentions"
    __table_args__ = (
        Index("ix_entity_mentions_chapter_type", "chapter_id", "entity_type"),
        Index("ix_entity_mentions_surface", "surface_text"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    fact_id: Mapped[str] = mapped_column(String(64), index=True)
    analysis_run_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("chapter_analysis_runs.id", ondelete="CASCADE"),
        index=True,
    )
    chapter_id: Mapped[str] = mapped_column(
        String(32),
        ForeignKey("compiled_chapters.id", ondelete="CASCADE"),
        index=True,
    )
    entity_type: Mapped[str] = mapped_column(String(32))
    surface_text: Mapped[str] = mapped_column(String(128))
    description: Mapped[str] = mapped_column(Text, default="")
    resolved_entity_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("entities.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    evidence_start: Mapped[int] = mapped_column(Integer)
    evidence_end: Mapped[int] = mapped_column(Integer)
    evidence_quote: Mapped[str] = mapped_column(Text)
    confidence: Mapped[float] = mapped_column(Float)
    review_status: Mapped[str] = mapped_column(String(24))


class NarrativeEventRecord(Base):
    __tablename__ = "narrative_events"
    __table_args__ = (
        Index("ix_narrative_events_chapter_sequence", "chapter_id", "sequence_index"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    fact_id: Mapped[str] = mapped_column(String(64), index=True)
    analysis_run_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("chapter_analysis_runs.id", ondelete="CASCADE"),
        index=True,
    )
    chapter_id: Mapped[str] = mapped_column(
        String(32),
        ForeignKey("compiled_chapters.id", ondelete="CASCADE"),
        index=True,
    )
    sequence_index: Mapped[int] = mapped_column(Integer)
    summary: Mapped[str] = mapped_column(Text)
    participants_json: Mapped[str] = mapped_column(Text, default="[]")
    location: Mapped[str] = mapped_column(String(128), default="")
    importance: Mapped[int] = mapped_column(Integer, default=1)
    result: Mapped[str] = mapped_column(Text, default="")
    evidence_start: Mapped[int] = mapped_column(Integer)
    evidence_end: Mapped[int] = mapped_column(Integer)
    evidence_quote: Mapped[str] = mapped_column(Text)
    confidence: Mapped[float] = mapped_column(Float)
    review_status: Mapped[str] = mapped_column(String(24))


class DialogueRecord(Base):
    __tablename__ = "dialogues"
    __table_args__ = (
        Index("ix_dialogues_chapter_start", "chapter_id", "evidence_start"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    fact_id: Mapped[str] = mapped_column(String(64), index=True)
    analysis_run_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("chapter_analysis_runs.id", ondelete="CASCADE"),
        index=True,
    )
    chapter_id: Mapped[str] = mapped_column(
        String(32),
        ForeignKey("compiled_chapters.id", ondelete="CASCADE"),
        index=True,
    )
    speaker: Mapped[str] = mapped_column(String(128))
    addressee: Mapped[str] = mapped_column(String(128), default="")
    text: Mapped[str] = mapped_column(Text)
    emotion: Mapped[str] = mapped_column(String(64), default="")
    evidence_start: Mapped[int] = mapped_column(Integer)
    evidence_end: Mapped[int] = mapped_column(Integer)
    evidence_quote: Mapped[str] = mapped_column(Text)
    confidence: Mapped[float] = mapped_column(Float)
    review_status: Mapped[str] = mapped_column(String(24))


class StateChangeRecord(Base):
    __tablename__ = "state_changes"
    __table_args__ = (
        Index("ix_state_changes_chapter_entity", "chapter_id", "entity_name"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    fact_id: Mapped[str] = mapped_column(String(64), index=True)
    analysis_run_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("chapter_analysis_runs.id", ondelete="CASCADE"),
        index=True,
    )
    chapter_id: Mapped[str] = mapped_column(
        String(32),
        ForeignKey("compiled_chapters.id", ondelete="CASCADE"),
        index=True,
    )
    entity_name: Mapped[str] = mapped_column(String(128))
    attribute: Mapped[str] = mapped_column(String(128))
    before_value: Mapped[str] = mapped_column(Text, default="")
    after_value: Mapped[str] = mapped_column(Text)
    evidence_start: Mapped[int] = mapped_column(Integer)
    evidence_end: Mapped[int] = mapped_column(Integer)
    evidence_quote: Mapped[str] = mapped_column(Text)
    confidence: Mapped[float] = mapped_column(Float)
    review_status: Mapped[str] = mapped_column(String(24))
