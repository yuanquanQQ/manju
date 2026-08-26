from __future__ import annotations

import json

from app.compiler.importer import (
    detect_text_encoding,
    import_chapter_json_set,
    import_text_file,
    split_text_chapters,
)


def test_split_text_chapters_keeps_offsets():
    text = "简介\n第1章 开始\n正文一\n第2章 继续\n正文二"
    segments = split_text_chapters(text)

    assert [item[0] for item in segments] == ["前言", "第1章 开始", "第2章 继续"]
    for _title, start, end, content in segments:
        assert text[start:end] == content


def test_import_text_file_writes_immutable_source_and_standard_chapters(tmp_path):
    source = tmp_path / "book.txt"
    source.write_text("第1章 开始\n正文一\n第2章 继续\n正文二", encoding="utf-8")
    project = tmp_path / "project"

    result = import_text_file(source, project)

    assert len(result.chapters) == 2
    assert result.chapters[0].chapter_id == "ch_000001"
    assert result.chapters[1].title == "第2章 继续"
    assert (project / result.source.stored_path).read_bytes() == source.read_bytes()
    assert (project / "novel" / "chapters" / "ch_000001.json").is_file()
    assert (project / "novel" / "import_manifest.json").is_file()


def test_detect_and_import_gb18030(tmp_path):
    raw = "第1章 测试\n中文正文".encode("gb18030")
    source = tmp_path / "book.txt"
    source.write_bytes(raw)

    assert detect_text_encoding(raw) == "gb18030"
    result = import_text_file(source, tmp_path / "project")
    assert result.source.encoding == "gb18030"
    assert "中文正文" in result.chapters[0].content


def test_import_json_set_uses_numeric_file_order_and_limit(tmp_path):
    source = tmp_path / "chapters"
    source.mkdir()
    for number in (10, 2, 1):
        (source / f"chapter_{number}.json").write_text(
            json.dumps(
                {
                    "chapter_id": number,
                    "title": f"第{number}章",
                    "content": f"正文{number}",
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

    result = import_chapter_json_set(source, tmp_path / "project", limit=2)

    assert [chapter.title for chapter in result.chapters] == ["第1章", "第2章"]
    assert result.source.source_type == "chapter_json_set"
    assert result.source.character_count == len("正文1正文2")

