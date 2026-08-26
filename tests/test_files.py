from __future__ import annotations

import json

from app.core.files import (
    atomic_write_json,
    atomic_write_text,
    sha256_file,
    sha256_text,
)


def test_atomic_write_and_hash(tmp_path):
    path = tmp_path / "nested" / "value.txt"
    atomic_write_text(path, "漫剧")

    assert path.read_text(encoding="utf-8") == "漫剧"
    assert sha256_file(path) == sha256_text("漫剧")
    assert list(path.parent.glob("*.tmp")) == []


def test_atomic_json_preserves_chinese(tmp_path):
    path = tmp_path / "value.json"
    atomic_write_json(path, {"name": "林凡"})

    assert json.loads(path.read_text(encoding="utf-8")) == {"name": "林凡"}
    assert "\\u6797" not in path.read_text(encoding="utf-8")

