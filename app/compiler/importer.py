"""小说文件导入、编码检测与标准章节切分。"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from app.core.files import (
    atomic_write_bytes,
    atomic_write_json,
    atomic_write_text,
    sha256_bytes,
    sha256_text,
)
from app.domain.novel import NovelImportResult, SourceDocument, StandardChapter

_CHAPTER_HEADING_RE = re.compile(
    r"(?im)^[ \t]*(?:#{1,3}[ \t]*)?"
    r"(?P<title>"
    r"第[零〇一二三四五六七八九十百千万两0-9]+[章节回卷部篇][^\r\n]*"
    r"|序章[^\r\n]*|楔子[^\r\n]*|前言[^\r\n]*"
    r"|chapter[ \t]+\d+[^\r\n]*"
    r")[ \t]*$"
)
_CHAPTER_NUMBER_RE = re.compile(r"(\d+)")


def detect_text_encoding(data: bytes) -> str:
    if data.startswith(b"\xef\xbb\xbf"):
        return "utf-8-sig"
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return "utf-16"
    for encoding in ("utf-8", "gb18030"):
        try:
            data.decode(encoding)
            return encoding
        except UnicodeDecodeError:
            continue
    raise UnicodeError("无法识别文本编码；请转换为 UTF-8 或 GB18030")


def normalize_text(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n").lstrip("\ufeff")


def split_text_chapters(text: str) -> list[tuple[str, int, int, str]]:
    """返回 (title, start, end, content)，偏移基于标准化全文。"""
    matches = list(_CHAPTER_HEADING_RE.finditer(text))
    if not matches:
        title = next((line.strip() for line in text.splitlines() if line.strip()), "正文")
        return [(title[:255], 0, len(text), text)]

    segments: list[tuple[str, int, int, str]] = []
    first_start = matches[0].start()
    if text[:first_start].strip():
        preface = text[:first_start]
        segments.append(("前言", 0, first_start, preface))

    for index, match in enumerate(matches):
        start = match.start()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        content = text[start:end]
        if content.strip():
            segments.append((match.group("title").strip()[:255], start, end, content))
    return segments


def _document_id(content_hash: str) -> str:
    return f"doc_{uuid5(NAMESPACE_URL, content_hash).hex}"


def _chapter_id(order: int) -> str:
    return f"ch_{order:06d}"


def _chapter_from_segment(
    *,
    order: int,
    title: str,
    content: str,
    source_document_id: str,
    source_file: str,
    start: int,
    end: int,
) -> StandardChapter:
    return StandardChapter(
        chapter_id=_chapter_id(order),
        order=order,
        title=title or f"第 {order} 章",
        content=content,
        source_document_id=source_document_id,
        source_file=source_file,
        source_start=start,
        source_end=end,
        content_hash=sha256_text(content),
    )


def _write_import_result(
    project_root: Path,
    result: NovelImportResult,
) -> NovelImportResult:
    chapters_dir = project_root / "novel" / "chapters"
    chapters_dir.mkdir(parents=True, exist_ok=True)
    for chapter in result.chapters:
        atomic_write_json(
            chapters_dir / f"{chapter.chapter_id}.json",
            chapter.model_dump(mode="json"),
        )
    atomic_write_json(
        project_root / "novel" / "import_manifest.json",
        {
            "schema_version": "1.0",
            "source": result.source.model_dump(mode="json"),
            "chapter_ids": [chapter.chapter_id for chapter in result.chapters],
        },
    )
    return result


def import_text_file(
    source_path: str | Path,
    project_root: str | Path,
    *,
    limit: int = 0,
) -> NovelImportResult:
    source = Path(source_path).resolve()
    root = Path(project_root).resolve()
    raw = source.read_bytes()
    encoding = detect_text_encoding(raw)
    normalized = normalize_text(raw.decode(encoding))
    if not normalized.strip():
        raise ValueError(f"小说文件为空: {source}")

    byte_hash = sha256_bytes(raw)
    normalized_hash = sha256_text(normalized)
    document_id = _document_id(byte_hash)
    source_dir = root / "novel" / "source" / document_id
    stored_raw = source_dir / source.name
    stored_normalized = source_dir / "normalized.txt"
    atomic_write_bytes(stored_raw, raw)
    atomic_write_text(stored_normalized, normalized)
    relative_normalized = stored_normalized.relative_to(root).as_posix()

    segments = split_text_chapters(normalized)
    if limit > 0:
        segments = segments[:limit]
    document = SourceDocument(
        document_id=document_id,
        source_type="markdown" if source.suffix.lower() in {".md", ".markdown"} else "text",
        original_name=source.name,
        stored_path=stored_raw.relative_to(root).as_posix(),
        encoding=encoding,
        byte_hash=byte_hash,
        normalized_hash=normalized_hash,
        character_count=len(normalized),
        offset_basis="normalized_text",
    )
    chapters = [
        _chapter_from_segment(
            order=index,
            title=title,
            content=content,
            source_document_id=document_id,
            source_file=relative_normalized,
            start=start,
            end=end,
        )
        for index, (title, start, end, content) in enumerate(segments, start=1)
    ]
    return _write_import_result(
        root,
        NovelImportResult(source=document, chapters=chapters),
    )


def _json_file_order(path: Path) -> tuple[int, str]:
    match = _CHAPTER_NUMBER_RE.search(path.stem)
    return (int(match.group(1)) if match else 10**12, path.name)


def import_chapter_json_set(
    source_dir: str | Path,
    project_root: str | Path,
    *,
    limit: int = 0,
) -> NovelImportResult:
    source = Path(source_dir).resolve()
    root = Path(project_root).resolve()
    files = sorted(source.glob("chapter_*.json"), key=_json_file_order)
    if limit > 0:
        files = files[:limit]
    if not files:
        raise FileNotFoundError(f"没有找到 chapter_*.json: {source}")

    digest = hashlib.sha256()
    loaded: list[tuple[Path, dict, bytes]] = []
    for path in files:
        raw = path.read_bytes()
        digest.update(path.name.encode("utf-8"))
        digest.update(raw)
        value = json.loads(raw.decode("utf-8-sig"))
        if not isinstance(value, dict):
            raise ValueError(f"章节 JSON 必须是对象: {path}")
        loaded.append((path, value, raw))

    byte_hash = f"sha256:{digest.hexdigest()}"
    document_id = _document_id(byte_hash)
    stored_dir = root / "novel" / "source" / document_id
    chapters: list[StandardChapter] = []
    normalized_digest = hashlib.sha256()

    for order, (path, value, raw) in enumerate(loaded, start=1):
        content = normalize_text(str(value.get("content", "")))
        if not content.strip():
            raise ValueError(f"章节正文为空: {path}")
        title = str(value.get("title") or f"第 {order} 章").strip()
        stored_file = stored_dir / path.name
        atomic_write_bytes(stored_file, raw)
        normalized_digest.update(content.encode("utf-8"))
        chapters.append(
            _chapter_from_segment(
                order=order,
                title=title,
                content=content,
                source_document_id=document_id,
                source_file=stored_file.relative_to(root).as_posix(),
                start=0,
                end=len(content),
            )
        )

    document = SourceDocument(
        document_id=document_id,
        source_type="chapter_json_set",
        original_name=source.name,
        stored_path=stored_dir.relative_to(root).as_posix(),
        encoding="utf-8",
        byte_hash=byte_hash,
        normalized_hash=f"sha256:{normalized_digest.hexdigest()}",
        character_count=sum(len(chapter.content) for chapter in chapters),
        offset_basis="chapter_content",
    )
    return _write_import_result(
        root,
        NovelImportResult(source=document, chapters=chapters),
    )


def import_novel(
    source_path: str | Path,
    project_root: str | Path,
    *,
    limit: int = 0,
) -> NovelImportResult:
    source = Path(source_path)
    if source.is_dir():
        return import_chapter_json_set(source, project_root, limit=limit)
    if source.suffix.lower() == ".json":
        return import_chapter_json_set(source.parent, project_root, limit=limit)
    if source.suffix.lower() not in {".txt", ".md", ".markdown"}:
        raise ValueError("仅支持 TXT、Markdown 或章节 JSON 目录")
    return import_text_file(source, project_root, limit=limit)

