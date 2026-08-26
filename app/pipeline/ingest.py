"""小说入库流水线。

流程：
1. 扫描 chapters/*.json
2. 已抽取（包含 new_character 等字段）的跳过
3. 调 LLM 抽取
4. 写 SQLite
5. 备份原 json，再覆盖回写
"""
from __future__ import annotations

import json
import shutil
from collections.abc import Iterable
from pathlib import Path

from sqlalchemy.orm import Session

from app.agents.novel_extractor import extract_chapter, merge_into_chapter
from app.core.logger import logger
from app.database.db import get_session, init_db
from app.database.models import (
    Chapter,
    ChapterSummary,
    Character,
    CharacterAppearance,
    Event,
    Scene,
    SceneAppearance,
)


def _iter_chapter_files(
    chapters_dir: Path,
    limit: int = 0,
) -> Iterable[Path]:
    files = sorted(chapters_dir.glob("chapter_*.json"))
    return files[:limit] if limit > 0 else files


def _load_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _save_json(path: Path, data: dict) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    tmp.replace(path)


def _already_extracted(data: dict) -> bool:
    return all(k in data for k in ("new_character", "new_scene", "new_event", "summary"))


def _get_or_create_character(s: Session, name: str, desc: str, chapter_id: int) -> Character:
    name = name.strip()
    if not name:
        raise ValueError("character name is empty")
    c = s.query(Character).filter(Character.name == name).one_or_none()
    if c is None:
        c = Character(name=name, description=desc, first_chapter_id=chapter_id)
        s.add(c)
        s.flush()
    elif not c.description and desc:
        c.description = desc
    return c


def _get_or_create_scene(s: Session, name: str, desc: str, chapter_id: int) -> Scene:
    name = name.strip()
    if not name:
        raise ValueError("scene name is empty")
    sc = s.query(Scene).filter(Scene.name == name).one_or_none()
    if sc is None:
        sc = Scene(name=name, description=desc, first_chapter_id=chapter_id)
        s.add(sc)
        s.flush()
    elif not sc.description and desc:
        sc.description = desc
    return sc


def _upsert_chapter(s: Session, data: dict, source_path: str) -> Chapter:
    ch_id = int(data["chapter_id"])
    ch = s.query(Chapter).filter(Chapter.chapter_id == ch_id).one_or_none()
    if ch is None:
        ch = Chapter(
            chapter_id=ch_id,
            title=data.get("title", ""),
            content=data.get("content", ""),
            source_path=source_path,
        )
        s.add(ch)
        s.flush()
    else:
        # 已存在则更新基础字段
        ch.title = data.get("title", ch.title)
        ch.content = data.get("content", ch.content)
        ch.source_path = source_path or ch.source_path
    return ch


def _persist_extraction(s: Session, ch: Chapter, extracted: dict) -> None:
    """把单章抽取结果写入数据库。"""
    # 摘要
    summary_text = (extracted.get("summary") or "").strip()
    if summary_text:
        cs = (
            s.query(ChapterSummary)
            .filter(ChapterSummary.chapter_id == ch.id)
            .one_or_none()
        )
        if cs is None:
            s.add(ChapterSummary(chapter_id=ch.id, summary=summary_text))
        else:
            cs.summary = summary_text

    # 人物 + 出场
    for item in extracted.get("new_character", []):
        name = (item.get("name") or "").strip()
        if not name:
            continue
        c = _get_or_create_character(s, name, item.get("description", ""), ch.chapter_id)
        exists = (
            s.query(CharacterAppearance)
            .filter_by(character_id=c.id, chapter_id=ch.id)
            .one_or_none()
        )
        if exists is None:
            s.add(CharacterAppearance(character_id=c.id, chapter_id=ch.id))

    # 场景 + 出场
    for item in extracted.get("new_scene", []):
        name = (item.get("name") or "").strip()
        if not name:
            continue
        sc = _get_or_create_scene(s, name, item.get("description", ""), ch.chapter_id)
        exists = (
            s.query(SceneAppearance)
            .filter_by(scene_id=sc.id, chapter_id=ch.id)
            .one_or_none()
        )
        if exists is None:
            s.add(SceneAppearance(scene_id=sc.id, chapter_id=ch.id))

    # 事件
    for item in extracted.get("new_event", []):
        s.add(
            Event(
                chapter_id=ch.id,
                summary=item.get("summary", ""),
                characters_json=json.dumps(item.get("characters", []), ensure_ascii=False),
                location=item.get("location", ""),
                importance=int(item.get("importance", 1) or 1),
            )
        )


def _backup_chapters(chapters_dir: Path, backup_dir: Path) -> None:
    if backup_dir.exists():
        return
    backup_dir.mkdir(parents=True, exist_ok=True)
    for p in _iter_chapter_files(chapters_dir):
        shutil.copy2(p, backup_dir / p.name)
    logger.info(f"已备份 {chapters_dir} -> {backup_dir}")


def run_ingest(
    project_dir: str | Path,
    chapters_subdir: str = "chapters",
    force: bool = False,
    limit: int = 0,
) -> dict:
    """执行入库；返回统计信息。 force=True 时强制重新抽取所有章节。"""
    project_dir = Path(project_dir)
    chapters_dir = project_dir / chapters_subdir
    backup_dir = project_dir / f"{chapters_subdir}_backup"
    db_path = project_dir / "database" / "world.db"

    if not chapters_dir.exists():
        raise FileNotFoundError(f"找不到目录: {chapters_dir}")

    _backup_chapters(chapters_dir, backup_dir)
    init_db(db_path)

    total = extracted = skipped = failed = 0
    for fp in _iter_chapter_files(chapters_dir, limit=limit):
        total += 1
        data = _load_json(fp)
        ch_id = data.get("chapter_id", fp.stem)

        if _already_extracted(data) and not force:
            # 已抽过：只补全数据库
            with get_session() as s, s.begin():
                ch = _upsert_chapter(s, data, str(fp))
                _persist_extraction(
                    s,
                    ch,
                    {
                        "new_character": data.get("new_character", []),
                        "new_scene": data.get("new_scene", []),
                        "new_event": data.get("new_event", []),
                        "summary": data.get("summary", ""),
                    },
                )
            skipped += 1
            continue

        # 需要 LLM 抽取
        try:
            ext = extract_chapter(data)
        except Exception as e:
            logger.error(f"ch{ch_id} 抽取异常: {e}")
            failed += 1
            continue

        merged = merge_into_chapter(data, ext)
        _save_json(fp, merged)

        with get_session() as s, s.begin():
            ch = _upsert_chapter(s, merged, str(fp))
            if force:
                # force 重抽：先清掉该章的旧事件和出场关联（人物/场景 master 保留）
                s.query(Event).filter(Event.chapter_id == ch.id).delete()
                s.query(CharacterAppearance).filter(CharacterAppearance.chapter_id == ch.id).delete()
                s.query(SceneAppearance).filter(SceneAppearance.chapter_id == ch.id).delete()
                s.query(ChapterSummary).filter(ChapterSummary.chapter_id == ch.id).delete()
            _persist_extraction(s, ch, ext)
        extracted += 1
        logger.info(
            f"[{total}] ch{ch_id} 抽取: "
            f"人物{len(ext['new_character'])} 场景{len(ext['new_scene'])} 事件{len(ext['new_event'])}"
        )

    return {"total": total, "extracted": extracted, "skipped": skipped, "failed": failed}
