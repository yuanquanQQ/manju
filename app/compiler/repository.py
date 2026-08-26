"""标准小说与章节分析的持久化边界。"""
from __future__ import annotations

import json
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import select

from app.database.db import get_session
from app.database.models import (
    ChapterAnalysisRun,
    CompiledChapter,
    DialogueRecord,
    Entity,
    EntityAlias,
    EntityMentionRecord,
    NarrativeEventRecord,
    NovelSourceDocument,
    StateChangeRecord,
)
from app.domain.novel import (
    AnalysisProvenance,
    ChapterAnalysis,
    Dialogue,
    EntityMention,
    EntityType,
    EvidenceSpan,
    NarrativeEvent,
    NovelImportResult,
    SourceDocument,
    StandardChapter,
    StateChange,
)


def persist_import(
    result: NovelImportResult,
    *,
    replace_active_set: bool = True,
) -> dict[str, int]:
    created = updated = 0
    with get_session() as session, session.begin():
        source = session.get(NovelSourceDocument, result.source.document_id)
        source_values = {
            "source_type": result.source.source_type,
            "original_name": result.source.original_name,
            "stored_path": result.source.stored_path,
            "encoding": result.source.encoding,
            "byte_hash": result.source.byte_hash,
            "normalized_hash": result.source.normalized_hash,
            "character_count": result.source.character_count,
            "offset_basis": result.source.offset_basis,
        }
        if source is None:
            source = NovelSourceDocument(
                id=result.source.document_id,
                **source_values,
            )
            session.add(source)
        else:
            for key, value in source_values.items():
                setattr(source, key, value)
        session.flush()

        if replace_active_set:
            session.query(CompiledChapter).update(
                {CompiledChapter.active: False},
                synchronize_session=False,
            )
        for chapter in result.chapters:
            stored = session.get(CompiledChapter, chapter.chapter_id)
            values = {
                "source_document_id": chapter.source_document_id,
                "chapter_order": chapter.order,
                "title": chapter.title,
                "content": chapter.content,
                "source_file": chapter.source_file,
                "source_start": chapter.source_start,
                "source_end": chapter.source_end,
                "content_hash": chapter.content_hash,
                "schema_version": chapter.schema_version,
                "active": True,
            }
            if stored is None:
                session.add(CompiledChapter(id=chapter.chapter_id, **values))
                created += 1
            else:
                for key, value in values.items():
                    setattr(stored, key, value)
                updated += 1
    return {"created": created, "updated": updated, "active": len(result.chapters)}


def _to_standard_chapter(stored: CompiledChapter) -> StandardChapter:
    return StandardChapter(
        schema_version=stored.schema_version,
        chapter_id=stored.id,
        order=stored.chapter_order,
        title=stored.title,
        content=stored.content,
        source_document_id=stored.source_document_id,
        source_file=stored.source_file,
        source_start=stored.source_start,
        source_end=stored.source_end,
        content_hash=stored.content_hash,
    )


def list_compiled_chapters(
    *,
    limit: int = 0,
    start: int = 0,
    end: int = 0,
) -> list[StandardChapter]:
    with get_session() as session:
        statement = (
            select(CompiledChapter)
            .where(CompiledChapter.active.is_(True))
            .order_by(CompiledChapter.chapter_order)
        )
        if start > 0:
            statement = statement.where(CompiledChapter.chapter_order >= start)
        if end > 0:
            statement = statement.where(CompiledChapter.chapter_order <= end)
        if limit > 0 and start == 0 and end == 0:
            statement = statement.limit(limit)
        return [_to_standard_chapter(item) for item in session.scalars(statement)]


def get_compiled_chapter(chapter_id: str) -> StandardChapter:
    with get_session() as session:
        stored = session.get(CompiledChapter, chapter_id)
        if stored is None:
            raise LookupError(f"标准章节不存在: {chapter_id}")
        return _to_standard_chapter(stored)


def find_reusable_analysis(
    chapter: StandardChapter,
    *,
    model: str,
    prompt_version: str,
) -> ChapterAnalysis | None:
    with get_session() as session:
        run = session.scalar(
            select(ChapterAnalysisRun)
            .where(
                ChapterAnalysisRun.chapter_id == chapter.chapter_id,
                ChapterAnalysisRun.input_hash == chapter.content_hash,
                ChapterAnalysisRun.model == model,
                ChapterAnalysisRun.prompt_version == prompt_version,
                ChapterAnalysisRun.status == "SUCCEEDED",
            )
            .order_by(ChapterAnalysisRun.completed_at.desc())
            .limit(1)
        )
        if run is None:
            return None
        return ChapterAnalysis.model_validate_json(run.output_json)


def build_analysis_from_raw_tables(
    chapter: StandardChapter,
    *,
    model: str,
    prompt_version: str,
) -> ChapterAnalysis | None:
    """从数据库原始表（narrative_events/dialogues/state_changes/entity_mentions）
    构建 ChapterAnalysis，作为 ChapterAnalysisRun 不可用时的兜底方案。"""
    with get_session() as session:
        # 查询事件
        event_records = list(
            session.scalars(
                select(NarrativeEventRecord)
                .where(NarrativeEventRecord.chapter_id == chapter.chapter_id)
                .order_by(NarrativeEventRecord.sequence_index)
            )
        )
        # 查询对白
        dialogue_records = list(
            session.scalars(
                select(DialogueRecord)
                .where(DialogueRecord.chapter_id == chapter.chapter_id)
                .order_by(DialogueRecord.evidence_start)
            )
        )
        # 查询状态变化
        state_records = list(
            session.scalars(
                select(StateChangeRecord)
                .where(StateChangeRecord.chapter_id == chapter.chapter_id)
            )
        )
        # 查询实体提及
        mention_records = list(
            session.scalars(
                select(EntityMentionRecord)
                .where(EntityMentionRecord.chapter_id == chapter.chapter_id)
            )
        )

        # 如果所有表都为空，返回 None
        if not any([event_records, dialogue_records, state_records, mention_records]):
            return None

        # 转换事件
        events = [
            NarrativeEvent(
                event_id=r.fact_id or r.id,
                sequence_index=r.sequence_index,
                summary=r.summary,
                participants=json.loads(r.participants_json or "[]"),
                location=r.location or "",
                importance=r.importance,
                result=r.result or "",
                evidence=EvidenceSpan(
                    chapter_id=r.chapter_id,
                    start=r.evidence_start,
                    end=r.evidence_end,
                    quote=r.evidence_quote or "",
                ),
                confidence=r.confidence,
            )
            for r in event_records
        ]

        # 转换对白
        dialogues = [
            Dialogue(
                dialogue_id=r.fact_id or r.id,
                speaker=r.speaker,
                addressee=r.addressee or "",
                text=r.text,
                emotion=r.emotion or "",
                evidence=EvidenceSpan(
                    chapter_id=r.chapter_id,
                    start=r.evidence_start,
                    end=r.evidence_end,
                    quote=r.evidence_quote or "",
                ),
                confidence=r.confidence,
            )
            for r in dialogue_records
        ]

        # 转换状态变化
        state_changes = [
            StateChange(
                change_id=r.fact_id or r.id,
                entity=r.entity_name,
                attribute=r.attribute,
                before=r.before_value or "",
                after=r.after_value,
                evidence=EvidenceSpan(
                    chapter_id=r.chapter_id,
                    start=r.evidence_start,
                    end=r.evidence_end,
                    quote=r.evidence_quote or "",
                ),
                confidence=r.confidence,
            )
            for r in state_records
        ]

        # 转换实体提及
        mentions = [
            EntityMention(
                mention_id=r.fact_id or r.id,
                surface_text=r.surface_text,
                entity_type=EntityType(r.entity_type),
                description=r.description or "",
                evidence=EvidenceSpan(
                    chapter_id=r.chapter_id,
                    start=r.evidence_start,
                    end=r.evidence_end,
                    quote=r.evidence_quote or "",
                ),
                confidence=r.confidence,
                resolved_entity_id=r.resolved_entity_id,
            )
            for r in mention_records
        ]

        # 生成摘要（取自第一个事件的前200字，或章节标题）
        summary = ""
        if events:
            summary = "；".join(e.summary[:100] for e in events[:3])
            if len(summary) > 500:
                summary = summary[:497] + "..."

        return ChapterAnalysis(
            schema_version="1.0",
            chapter_id=chapter.chapter_id,
            mentions=mentions,
            events=events,
            dialogues=dialogues,
            state_changes=state_changes,
            summary=summary,
            provenance=AnalysisProvenance(
                model=model,
                prompt_version=prompt_version,
                input_hash=chapter.content_hash,
                chunk_count=1,
            ),
        )


def save_analysis(analysis: ChapterAnalysis) -> ChapterAnalysisRun:
    run_id = str(uuid4())
    now = datetime.now(UTC)
    run = ChapterAnalysisRun(
        id=run_id,
        chapter_id=analysis.chapter_id,
        status="SUCCEEDED",
        schema_version=analysis.schema_version,
        model=analysis.provenance.model,
        prompt_version=analysis.provenance.prompt_version,
        input_hash=analysis.provenance.input_hash,
        output_json=analysis.model_dump_json(),
        warnings_json=json.dumps(analysis.warnings, ensure_ascii=False),
        completed_at=now,
    )
    with get_session() as session, session.begin():
        if session.get(CompiledChapter, analysis.chapter_id) is None:
            raise LookupError(f"标准章节不存在: {analysis.chapter_id}")
        session.add(run)
        session.flush()
        mention_records: list[tuple[EntityMentionRecord, object]] = []
        for item in analysis.mentions:
            record = EntityMentionRecord(
                id=str(uuid4()),
                fact_id=item.mention_id,
                analysis_run_id=run_id,
                chapter_id=analysis.chapter_id,
                entity_type=item.entity_type.value,
                surface_text=item.surface_text,
                description=item.description,
                resolved_entity_id=item.resolved_entity_id,
                evidence_start=item.evidence.start,
                evidence_end=item.evidence.end,
                evidence_quote=item.evidence.quote,
                confidence=item.confidence,
                review_status=item.review_status.value,
            )
            session.add(record)
            mention_records.append((record, item))
        for item in analysis.events:
            session.add(
                NarrativeEventRecord(
                    id=str(uuid4()),
                    fact_id=item.event_id,
                    analysis_run_id=run_id,
                    chapter_id=analysis.chapter_id,
                    sequence_index=item.sequence_index,
                    summary=item.summary,
                    participants_json=json.dumps(
                        item.participants,
                        ensure_ascii=False,
                    ),
                    location=item.location,
                    importance=item.importance,
                    result=item.result,
                    evidence_start=item.evidence.start,
                    evidence_end=item.evidence.end,
                    evidence_quote=item.evidence.quote,
                    confidence=item.confidence,
                    review_status=item.review_status.value,
                )
            )
        for item in analysis.dialogues:
            session.add(
                DialogueRecord(
                    id=str(uuid4()),
                    fact_id=item.dialogue_id,
                    analysis_run_id=run_id,
                    chapter_id=analysis.chapter_id,
                    speaker=item.speaker,
                    addressee=item.addressee,
                    text=item.text,
                    emotion=item.emotion,
                    evidence_start=item.evidence.start,
                    evidence_end=item.evidence.end,
                    evidence_quote=item.evidence.quote,
                    confidence=item.confidence,
                    review_status=item.review_status.value,
                )
            )
        for item in analysis.state_changes:
            session.add(
                StateChangeRecord(
                    id=str(uuid4()),
                    fact_id=item.change_id,
                    analysis_run_id=run_id,
                    chapter_id=analysis.chapter_id,
                    entity_name=item.entity,
                    attribute=item.attribute,
                    before_value=item.before,
                    after_value=item.after,
                    evidence_start=item.evidence.start,
                    evidence_end=item.evidence.end,
                    evidence_quote=item.evidence.quote,
                    confidence=item.confidence,
                    review_status=item.review_status.value,
                )
            )
        session.flush()

        chapter = session.get(CompiledChapter, analysis.chapter_id)
        for record, mention in mention_records:
            entity = session.scalar(
                select(Entity).where(
                    Entity.entity_type == mention.entity_type.value,
                    Entity.canonical_name == mention.surface_text,
                )
            )
            if entity is None:
                entity = Entity(
                    id=str(uuid4()),
                    entity_type=mention.entity_type.value,
                    canonical_name=mention.surface_text,
                    description=mention.description,
                    resolution_status="unreviewed",
                    first_chapter_order=chapter.chapter_order,
                )
                session.add(entity)
                session.flush()
                session.add(
                    EntityAlias(
                        id=str(uuid4()),
                        entity_id=entity.id,
                        alias=mention.surface_text,
                        evidence_chapter_id=analysis.chapter_id,
                    )
                )
            else:
                entity.first_chapter_order = min(
                    entity.first_chapter_order or chapter.chapter_order,
                    chapter.chapter_order,
                )
                if not entity.description and mention.description:
                    entity.description = mention.description
            record.resolved_entity_id = entity.id
    return run


def save_analysis_failure(
    chapter: StandardChapter,
    *,
    model: str,
    prompt_version: str,
    error_message: str,
) -> ChapterAnalysisRun:
    run = ChapterAnalysisRun(
        id=str(uuid4()),
        chapter_id=chapter.chapter_id,
        status="FAILED",
        schema_version="1.0",
        model=model,
        prompt_version=prompt_version,
        input_hash=chapter.content_hash,
        error_message=error_message,
        completed_at=datetime.now(UTC),
    )
    with get_session() as session, session.begin():
        session.add(run)
    return run


def load_source_document(document_id: str) -> SourceDocument:
    with get_session() as session:
        source = session.get(NovelSourceDocument, document_id)
        if source is None:
            raise LookupError(f"小说来源不存在: {document_id}")
        return SourceDocument(
            document_id=source.id,
            source_type=source.source_type,
            original_name=source.original_name,
            stored_path=source.stored_path,
            encoding=source.encoding,
            byte_hash=source.byte_hash,
            normalized_hash=source.normalized_hash,
            character_count=source.character_count,
            offset_basis=source.offset_basis,
        )
