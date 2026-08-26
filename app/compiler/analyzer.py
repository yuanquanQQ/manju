"""分块、严格校验并合并章节结构化分析。"""
from __future__ import annotations

import time
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.adapters.llm import OpenAICompatibleLLM, StructuredLLM
from app.compiler.chunking import TextChunk, split_text_chunks
from app.core.config import settings
from app.domain.novel import (
    AnalysisProvenance,
    ChapterAnalysis,
    Dialogue,
    EntityMention,
    EntityType,
    EvidenceSpan,
    NarrativeEvent,
    StandardChapter,
    StateChange,
)

PROMPT_VERSION = "chapter-analysis-v1"

SYSTEM_PROMPT = """你是中文小说事实抽取器。

规则：
1. 只抽取当前文本块中明确出现的事实，不判断是否为全书首次出现。
2. 每一项必须提供原文中连续、逐字一致、最长 300 字的 quote；只保留能证明该项的最短片段，不要复制整段或整块。
3. 不补写、推测或改写 quote；找不到原文证据就不要输出该项。
4. mentions 包含本块所有重要人物、地点、组织、道具、能力或生物提及。
5. events 按本块发生顺序输出，importance 为 1-5。
6. dialogues 只提取能判断说话人的重要对白。
7. state_changes 只提取本块明确发生的属性或关系变化。
8. 禁止输出任何思考过程、分析文本或解释，回复必须且只能是纯 JSON 对象。"""



class RawMention(BaseModel):
    model_config = ConfigDict(extra="forbid")

    surface_text: str = Field(min_length=1, max_length=128)
    entity_type: EntityType
    description: str = Field(default="", max_length=300)
    quote: str = Field(min_length=1, max_length=300)
    confidence: float = Field(default=0.8, ge=0.0, le=1.0)


class RawEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary: str = Field(min_length=1, max_length=500)
    participants: list[str] = Field(default_factory=list)
    location: str = ""
    importance: int = Field(default=1, ge=1, le=5)
    result: str = Field(default="", max_length=500)
    quote: str = Field(min_length=1, max_length=300)
    confidence: float = Field(default=0.8, ge=0.0, le=1.0)


class RawDialogue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    speaker: str = Field(min_length=1, max_length=128)
    addressee: str = Field(default="", max_length=128)
    text: str = Field(min_length=1, max_length=1000)
    emotion: str = Field(default="", max_length=64)
    quote: str = Field(min_length=1, max_length=300)
    confidence: float = Field(default=0.8, ge=0.0, le=1.0)


class RawStateChange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entity: str = Field(min_length=1, max_length=128)
    attribute: str = Field(min_length=1, max_length=128)
    before: str = Field(default="", max_length=300)
    after: str = Field(min_length=1, max_length=300)
    quote: str = Field(min_length=1, max_length=300)
    confidence: float = Field(default=0.8, ge=0.0, le=1.0)


class ChunkExtraction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mentions: list[RawMention] = Field(default_factory=list)
    events: list[RawEvent] = Field(default_factory=list)
    dialogues: list[RawDialogue] = Field(default_factory=list)
    state_changes: list[RawStateChange] = Field(default_factory=list)
    summary: str = Field(default="", max_length=500)
    adaptation_notes: list[str] = Field(default_factory=list)


class ChapterAnalysisError(RuntimeError):
    pass


def _stable_id(chapter_id: str, kind: str, start: int, value: str) -> str:
    return f"{kind}_{uuid5(NAMESPACE_URL, f'{chapter_id}|{kind}|{start}|{value}').hex}"


def _evidence(
    chapter: StandardChapter,
    chunk: TextChunk,
    quote: str,
    warnings: list[str],
    label: str,
) -> EvidenceSpan | None:
    local_start = chunk.text.find(quote)
    if local_start < 0:
        warnings.append(f"{label} 的 quote 未在原文中逐字找到，已跳过: {quote[:40]}")
        return None
    start = chunk.start + local_start
    end = start + len(quote)
    if chapter.content[start:end] != quote:
        warnings.append(f"{label} 的证据偏移校验失败，已跳过")
        return None
    return EvidenceSpan(
        chapter_id=chapter.chapter_id,
        start=start,
        end=end,
        quote=quote,
    )


def _extract_chunk(
    chapter: StandardChapter,
    chunk: TextChunk,
    llm: StructuredLLM,
    *,
    max_retries: int,
) -> ChunkExtraction:
    prompt = (
        f"章节 ID：{chapter.chapter_id}\n"
        f"章节标题：{chapter.title}\n"
        f"本块全章起始偏移：{chunk.start}\n\n"
        f"文本块：\n{chunk.text}"
    )
    last_error: Exception | None = None
    for attempt in range(1, max_retries + 1):
        try:
            value = llm.complete(
                system_prompt=SYSTEM_PROMPT,
                user_prompt=prompt,
                json_schema=ChunkExtraction.model_json_schema(),
            )
            return ChunkExtraction.model_validate(value)
        except (ValidationError, ValueError, RuntimeError) as exc:
            last_error = exc
            if attempt < max_retries:
                time.sleep(min(attempt, 3))
    raise ChapterAnalysisError(
        f"{chapter.chapter_id} chunk {chunk.index} 分析失败: {last_error}"
    ) from last_error


def _deduplicate(items: list[Any], key) -> list[Any]:
    result: list[Any] = []
    seen: set[Any] = set()
    for item in items:
        identity = key(item)
        if identity not in seen:
            seen.add(identity)
            result.append(item)
    return result


def analyze_chapter(
    chapter: StandardChapter,
    *,
    llm: StructuredLLM | None = None,
    max_chars: int | None = None,
    overlap: int = 300,
    max_retries: int | None = None,
) -> ChapterAnalysis:
    client = llm or OpenAICompatibleLLM()
    chunks = split_text_chunks(
        chapter.content,
        max_chars=max_chars or settings.extract_max_chars,
        overlap=overlap,
    )
    if not chunks:
        raise ChapterAnalysisError(f"章节正文为空: {chapter.chapter_id}")

    extracted = [
        _extract_chunk(
            chapter,
            chunk,
            client,
            max_retries=max_retries or settings.llm_max_retries,
        )
        for chunk in chunks
    ]
    warnings: list[str] = []
    mentions: list[EntityMention] = []
    events: list[NarrativeEvent] = []
    dialogues: list[Dialogue] = []
    changes: list[StateChange] = []

    for chunk, raw in zip(chunks, extracted, strict=True):
        for item in raw.mentions:
            evidence = _evidence(
                chapter, chunk, item.quote, warnings, f"mention:{item.surface_text}"
            )
            if evidence:
                mentions.append(
                    EntityMention(
                        mention_id=_stable_id(
                            chapter.chapter_id,
                            "mention",
                            evidence.start,
                            f"{item.entity_type.value}:{item.surface_text}",
                        ),
                        surface_text=item.surface_text,
                        entity_type=item.entity_type,
                        description=item.description,
                        evidence=evidence,
                        confidence=item.confidence,
                    )
                )
        for item in raw.events:
            evidence = _evidence(chapter, chunk, item.quote, warnings, "event")
            if evidence:
                events.append(
                    NarrativeEvent(
                        event_id=_stable_id(
                            chapter.chapter_id,
                            "event",
                            evidence.start,
                            item.summary,
                        ),
                        sequence_index=0,
                        summary=item.summary,
                        participants=item.participants,
                        location=item.location,
                        importance=item.importance,
                        result=item.result,
                        evidence=evidence,
                        confidence=item.confidence,
                    )
                )
        for item in raw.dialogues:
            evidence = _evidence(
                chapter, chunk, item.quote, warnings, f"dialogue:{item.speaker}"
            )
            if evidence:
                dialogues.append(
                    Dialogue(
                        dialogue_id=_stable_id(
                            chapter.chapter_id,
                            "dialogue",
                            evidence.start,
                            f"{item.speaker}:{item.text}",
                        ),
                        speaker=item.speaker,
                        addressee=item.addressee,
                        text=item.text,
                        emotion=item.emotion,
                        evidence=evidence,
                        confidence=item.confidence,
                    )
                )
        for item in raw.state_changes:
            evidence = _evidence(
                chapter, chunk, item.quote, warnings, f"state:{item.entity}"
            )
            if evidence:
                changes.append(
                    StateChange(
                        change_id=_stable_id(
                            chapter.chapter_id,
                            "change",
                            evidence.start,
                            f"{item.entity}:{item.attribute}:{item.after}",
                        ),
                        entity=item.entity,
                        attribute=item.attribute,
                        before=item.before,
                        after=item.after,
                        evidence=evidence,
                        confidence=item.confidence,
                    )
                )

    mentions = _deduplicate(
        mentions,
        lambda item: (
            item.entity_type,
            item.surface_text,
            item.evidence.start,
        ),
    )
    events = _deduplicate(
        events,
        lambda item: (item.summary, item.evidence.start),
    )
    events.sort(key=lambda item: item.evidence.start)
    events = [
        item.model_copy(update={"sequence_index": index})
        for index, item in enumerate(events)
    ]
    dialogues = _deduplicate(
        dialogues,
        lambda item: (item.speaker, item.text, item.evidence.start),
    )
    changes = _deduplicate(
        changes,
        lambda item: (
            item.entity,
            item.attribute,
            item.after,
            item.evidence.start,
        ),
    )
    summaries = [item.summary.strip() for item in extracted if item.summary.strip()]
    summary = "；".join(dict.fromkeys(summaries))
    if len(summary) > 1000:
        summary = f"{summary[:997]}..."
        warnings.append("分块摘要合并后超过 1000 字，已截断")
    notes = list(
        dict.fromkeys(
            note.strip()
            for item in extracted
            for note in item.adaptation_notes
            if note.strip()
        )
    )
    return ChapterAnalysis(
        chapter_id=chapter.chapter_id,
        mentions=mentions,
        events=events,
        dialogues=dialogues,
        state_changes=changes,
        summary=summary,
        adaptation_notes=notes,
        warnings=warnings,
        provenance=AnalysisProvenance(
            model=client.model_name,
            prompt_version=PROMPT_VERSION,
            input_hash=chapter.content_hash,
            chunk_count=len(chunks),
        ),
    )
