"""按自然边界切分长章节，并保留全章字符偏移。"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TextChunk:
    index: int
    start: int
    end: int
    text: str


def split_text_chunks(
    content: str,
    *,
    max_chars: int = 6000,
    overlap: int = 300,
) -> list[TextChunk]:
    if max_chars < 500:
        raise ValueError("max_chars 不能小于 500")
    if overlap < 0 or overlap >= max_chars // 2:
        raise ValueError("overlap 必须在 0 到 max_chars/2 之间")
    if not content:
        return []

    chunks: list[TextChunk] = []
    start = 0
    while start < len(content):
        target = min(start + max_chars, len(content))
        end = target
        if target < len(content):
            minimum_break = start + int(max_chars * 0.6)
            paragraph_break = content.rfind("\n\n", minimum_break, target)
            line_break = content.rfind("\n", minimum_break, target)
            sentence_break = max(
                content.rfind("。", minimum_break, target),
                content.rfind("！", minimum_break, target),
                content.rfind("？", minimum_break, target),
            )
            boundary = max(paragraph_break, line_break, sentence_break)
            if boundary >= minimum_break:
                end = boundary + 1
        chunks.append(
            TextChunk(
                index=len(chunks),
                start=start,
                end=end,
                text=content[start:end],
            )
        )
        if end >= len(content):
            break
        start = max(start + 1, end - overlap)
    return chunks

