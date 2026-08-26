from __future__ import annotations

import pytest

from app.adapters.llm import extract_json_object
from app.compiler.analyzer import ChapterAnalysisError, analyze_chapter
from app.compiler.chunking import split_text_chunks
from app.core.files import sha256_text
from app.domain.novel import StandardChapter


class FakeLLM:
    model_name = "fake-model"

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0

    def complete(self, **_kwargs):
        self.calls += 1
        return self.responses.pop(0)


def _chapter(content: str) -> StandardChapter:
    return StandardChapter(
        chapter_id="ch_000001",
        order=1,
        title="第一章",
        content=content,
        source_document_id="doc_test",
        source_file="novel/source/test.txt",
        source_start=0,
        source_end=len(content),
        content_hash=sha256_text(content),
    )


def _valid_response():
    return {
        "mentions": [
            {
                "surface_text": "林凡",
                "entity_type": "character",
                "description": "少年",
                "quote": "林凡拔出长剑",
                "confidence": 0.95,
            }
        ],
        "events": [
            {
                "summary": "林凡拔剑",
                "participants": ["林凡"],
                "location": "山门",
                "importance": 3,
                "result": "准备战斗",
                "quote": "林凡拔出长剑",
                "confidence": 0.9,
            }
        ],
        "dialogues": [
            {
                "speaker": "林凡",
                "text": "住手！",
                "emotion": "愤怒",
                "quote": "林凡喝道：“住手！”",
                "confidence": 0.9,
            }
        ],
        "state_changes": [
            {
                "entity": "林凡",
                "attribute": "持有武器",
                "before": "",
                "after": "长剑",
                "quote": "林凡拔出长剑",
                "confidence": 0.8,
            }
        ],
        "summary": "林凡在山门拔剑喝止对方。",
        "adaptation_notes": ["可用拔剑特写"],
    }


def test_analyze_chapter_builds_verified_evidence():
    content = "山门前，林凡拔出长剑。林凡喝道：“住手！”"
    result = analyze_chapter(
        _chapter(content),
        llm=FakeLLM([_valid_response()]),
        max_chars=500,
        overlap=0,
        max_retries=1,
    )

    assert result.provenance.model == "fake-model"
    assert result.mentions[0].surface_text == "林凡"
    assert result.events[0].sequence_index == 0
    assert result.dialogues[0].text == "住手！"
    for item in [
        result.mentions[0],
        result.events[0],
        result.dialogues[0],
        result.state_changes[0],
    ]:
        evidence = item.evidence
        assert content[evidence.start : evidence.end] == evidence.quote


def test_invalid_schema_is_retried_then_succeeds():
    fake = FakeLLM([{"unexpected": True}, _valid_response()])
    result = analyze_chapter(
        _chapter("林凡拔出长剑。林凡喝道：“住手！”"),
        llm=fake,
        max_chars=500,
        overlap=0,
        max_retries=2,
    )
    assert fake.calls == 2
    assert result.events


def test_exhausted_validation_does_not_return_empty_success():
    with pytest.raises(ChapterAnalysisError):
        analyze_chapter(
            _chapter("测试正文"),
            llm=FakeLLM([{"bad": 1}, {"bad": 2}]),
            max_chars=500,
            overlap=0,
            max_retries=2,
        )


def test_unsupported_quote_is_skipped_with_warning():
    response = _valid_response()
    response["mentions"][0]["quote"] = "原文里没有这句话"
    result = analyze_chapter(
        _chapter("林凡拔出长剑。林凡喝道：“住手！”"),
        llm=FakeLLM([response]),
        max_chars=500,
        overlap=0,
        max_retries=1,
    )
    assert result.mentions == []
    assert any("逐字找到" in warning for warning in result.warnings)


def test_chunking_preserves_global_offsets_and_overlap():
    content = ("第一段。" * 200) + "\n\n" + ("第二段。" * 200)
    chunks = split_text_chunks(content, max_chars=500, overlap=50)

    assert chunks[0].start == 0
    assert chunks[-1].end == len(content)
    assert all(content[item.start : item.end] == item.text for item in chunks)
    assert all(
        current.start < previous.end
        for previous, current in zip(chunks, chunks[1:], strict=False)
    )


def test_extract_json_object_accepts_fenced_or_prefixed_output():
    value = extract_json_object('说明文字\n```json\n{"ok": true}\n```')
    assert value == {"ok": True}

