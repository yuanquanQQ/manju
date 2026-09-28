"""Combine one or more analyzed chapters into one adaptation unit."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256

from app.domain.novel import AnalysisProvenance, ChapterAnalysis, StandardChapter


@dataclass(frozen=True, slots=True)
class AdaptationMaterial:
    chapter_ids: list[str]
    title: str
    source_text: str
    analysis: ChapterAnalysis


def combine_adaptation_material(
    chapters: list[StandardChapter],
    analyses: list[ChapterAnalysis],
) -> AdaptationMaterial:
    if not chapters or len(chapters) != len(analyses):
        raise ValueError("改编单元的章节与分析必须非空且一一对应")
    chapter_ids = [chapter.chapter_id for chapter in chapters]
    if [analysis.chapter_id for analysis in analyses] != chapter_ids:
        raise ValueError("改编单元的章节与分析顺序不一致")
    source_text = "\n\n".join(
        f"【{chapter.title}】\n{chapter.content}" for chapter in chapters
    )
    combined_hash = sha256(
        "|".join(analysis.provenance.input_hash for analysis in analyses).encode()
    ).hexdigest()
    analysis = ChapterAnalysis(
        chapter_id="unit_" + "_".join(chapter_ids),
        mentions=[item for value in analyses for item in value.mentions],
        events=[item for value in analyses for item in value.events],
        dialogues=[item for value in analyses for item in value.dialogues],
        state_changes=[item for value in analyses for item in value.state_changes],
        summary="；".join(
            value.summary for value in analyses if value.summary.strip()
        )[:1000],
        adaptation_notes=list(
            dict.fromkeys(
                note
                for value in analyses
                for note in value.adaptation_notes
                if note.strip()
            )
        ),
        warnings=[warning for value in analyses for warning in value.warnings],
        provenance=AnalysisProvenance(
            model=analyses[0].provenance.model,
            prompt_version=analyses[0].provenance.prompt_version,
            input_hash=combined_hash,
            chunk_count=sum(value.provenance.chunk_count for value in analyses),
        ),
    )
    return AdaptationMaterial(
        chapter_ids=chapter_ids,
        title=" / ".join(chapter.title for chapter in chapters),
        source_text=source_text,
        analysis=analysis,
    )
