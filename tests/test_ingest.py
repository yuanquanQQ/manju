from __future__ import annotations

import json

from app.pipeline import ingest


def _write_chapter(path, chapter_id):
    path.write_text(
        json.dumps(
            {
                "chapter_id": chapter_id,
                "title": f"第 {chapter_id} 章",
                "content": "测试正文",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def test_limit_does_not_rename_unselected_chapters(tmp_path, monkeypatch):
    project = tmp_path / "project"
    chapters = project / "chapters"
    chapters.mkdir(parents=True)
    _write_chapter(chapters / "chapter_001.json", 1)
    _write_chapter(chapters / "chapter_002.json", 2)

    monkeypatch.setattr(
        ingest,
        "extract_chapter",
        lambda _chapter: {
            "new_character": [],
            "new_scene": [],
            "new_event": [],
            "summary": "摘要",
        },
    )

    stats = ingest.run_ingest(project, limit=1)
    first = json.loads((chapters / "chapter_001.json").read_text(encoding="utf-8"))
    second = json.loads((chapters / "chapter_002.json").read_text(encoding="utf-8"))

    assert stats == {"total": 1, "extracted": 1, "skipped": 0, "failed": 0}
    assert first["summary"] == "摘要"
    assert "summary" not in second
    assert list(chapters.glob("*.hold")) == []

