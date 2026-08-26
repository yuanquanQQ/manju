from __future__ import annotations

from app.compiler.importer import import_text_file
from app.compiler.repository import (
    find_reusable_analysis,
    list_compiled_chapters,
    load_source_document,
    persist_import,
    save_analysis,
    save_analysis_failure,
)
from app.database.db import get_session, init_db
from app.database.models import (
    ChapterAnalysisRun,
    Entity,
    EntityMentionRecord,
    NarrativeEventRecord,
)
from app.domain.novel import (
    AnalysisProvenance,
    ChapterAnalysis,
    EntityMention,
    EntityType,
    EvidenceSpan,
    NarrativeEvent,
)


def test_import_and_analysis_are_versioned_and_reusable(tmp_path):
    project = tmp_path / "project"
    source = tmp_path / "book.txt"
    source.write_text("第1章 开始\n林凡拔出长剑。", encoding="utf-8")
    init_db(project / "database" / "world.db")

    imported = import_text_file(source, project)
    stats = persist_import(imported)
    chapters = list_compiled_chapters()
    chapter = chapters[0]

    assert stats == {"created": 1, "updated": 0, "active": 1}
    assert load_source_document(imported.source.document_id).byte_hash
    assert chapter.content_hash == imported.chapters[0].content_hash

    quote = "林凡拔出长剑"
    start = chapter.content.index(quote)
    evidence = EvidenceSpan(
        chapter_id=chapter.chapter_id,
        start=start,
        end=start + len(quote),
        quote=quote,
    )
    analysis = ChapterAnalysis(
        chapter_id=chapter.chapter_id,
        mentions=[
            EntityMention(
                mention_id="mention_test",
                surface_text="林凡",
                entity_type=EntityType.CHARACTER,
                description="少年",
                evidence=evidence,
                confidence=0.9,
            )
        ],
        events=[
            NarrativeEvent(
                event_id="event_test",
                sequence_index=0,
                summary="林凡拔剑",
                participants=["林凡"],
                evidence=evidence,
                confidence=0.9,
            )
        ],
        summary="林凡拔剑。",
        provenance=AnalysisProvenance(
            model="fake",
            prompt_version="test-v1",
            input_hash=chapter.content_hash,
            chunk_count=1,
        ),
    )
    run = save_analysis(analysis)
    reused = find_reusable_analysis(
        chapter,
        model="fake",
        prompt_version="test-v1",
    )

    assert run.status == "SUCCEEDED"
    assert reused == analysis
    with get_session() as session:
        assert session.query(EntityMentionRecord).count() == 1
        assert session.query(NarrativeEventRecord).count() == 1
        entity = session.query(Entity).one()
        mention = session.query(EntityMentionRecord).one()
        assert entity.canonical_name == "林凡"
        assert mention.resolved_entity_id == entity.id


def test_failed_analysis_is_explicit(tmp_path):
    project = tmp_path / "project"
    source = tmp_path / "book.txt"
    source.write_text("第1章 开始\n正文。", encoding="utf-8")
    init_db(project / "database" / "world.db")
    imported = import_text_file(source, project)
    persist_import(imported)
    chapter = list_compiled_chapters()[0]

    run = save_analysis_failure(
        chapter,
        model="fake",
        prompt_version="test-v1",
        error_message="schema invalid",
    )

    with get_session() as session:
        stored = session.get(ChapterAnalysisRun, run.id)
        assert stored.status == "FAILED"
        assert stored.error_message == "schema invalid"

