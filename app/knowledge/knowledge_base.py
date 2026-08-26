"""知识数据库 (Knowledge Base)。

将编译后的人物、场景、事件数据导出为索引，支持：
- FAISS 向量语义检索（通过 embedding 快速定位信息）
- JSON 导出（world.json / characters.json / timeline.json）
- 全文检索兜底
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pydantic

from app.core.files import atomic_write_json
from app.core.logger import logger
from app.database.db import get_session
from app.database.models import (
    CompiledChapter,
    Entity,
    EntityAlias,
    NarrativeEventRecord,
)

# FAISS 是可选依赖
try:
    import numpy as np

    _NUMPY_OK = True
except ImportError:
    _NUMPY_OK = False

try:
    import faiss  # type: ignore[import-untyped]

    _FAISS_OK = True
except ImportError:
    _FAISS_OK = False


class SearchResult(pydantic.BaseModel):
    """单条检索结果。"""

    type: str  # "character" / "event" / "chapter"
    id: str
    title: str
    content: str
    score: float = 1.0
    metadata: dict[str, Any] = pydantic.Field(default_factory=dict)


class KnowledgeBase:
    """知识库：管理人物、场景、事件的索引与检索。"""

    def __init__(self, project_root: str | Path) -> None:
        self.root = Path(project_root)
        self.output_dir = self.root / "production" / "knowledge"
        self.output_dir.mkdir(parents=True, exist_ok=True)

    # ── 导出 JSON ───────────────────────────────────────

    def export_world_json(self) -> dict[str, Any]:
        """导出世界设定概览。"""
        with get_session() as session:
            entities = session.query(Entity).all()
            chapters = session.query(CompiledChapter).order_by(
                CompiledChapter.chapter_order
            ).all()

            # 查询所有别名
            entity_ids = [e.id for e in entities]
            alias_records: list[EntityAlias] = (
                session.query(EntityAlias)
                .filter(EntityAlias.entity_id.in_(entity_ids))
                .all()
            ) if entity_ids else []
            alias_map: dict[str, list[str]] = {}
            for a in alias_records:
                alias_map.setdefault(a.entity_id, []).append(a.alias)

        entity_list: list[dict[str, Any]] = []
        for e in sorted(entities, key=lambda x: (x.entity_type, x.canonical_name)):
            entity_list.append(
                {
                    "name": e.canonical_name,
                    "type": e.entity_type,
                    "description": e.description,
                    "first_appearance_chapter": e.first_chapter_order,
                    "aliases": alias_map.get(e.id, []),
                }
            )

        world = {
            "title": self.root.name,
            "total_chapters": len(chapters),
            "total_entities": len(entities),
            "entities": entity_list,
            "chapter_list": [
                {"order": c.chapter_order, "id": c.id, "title": c.title}
                for c in chapters
            ],
        }

        path = self.output_dir / "world.json"
        atomic_write_json(path, world)
        logger.info(f"世界设定已导出: {path} ({len(entities)} 实体)")
        return world

    def export_characters_json(self) -> list[dict[str, Any]]:
        """导出人物档案（包含所有出场章节引用）。"""
        with get_session() as session:
            entities = (
                session.query(Entity)
                .filter(Entity.entity_type == "character")
                .all()
            )

        characters: list[dict[str, Any]] = []
        for e in entities:
            # 查询该实体的别名
            aliases = (
                session.query(EntityAlias)
                .filter(EntityAlias.entity_id == e.id)
                .all()
            )
            characters.append(
                {
                    "name": e.canonical_name,
                    "description": e.description,
                    "first_chapter": e.first_chapter_order,
                    "aliases": [a.alias for a in aliases],
                }
            )

        path = self.output_dir / "characters.json"
        atomic_write_json(path, characters)
        logger.info(f"人物档案已导出: {path} ({len(characters)} 人)")
        return characters

    def export_timeline_json(self) -> list[dict[str, Any]]:
        """导出事件时间线。"""
        with get_session() as session:
            events = (
                session.query(NarrativeEventRecord, CompiledChapter)
                .join(CompiledChapter, NarrativeEventRecord.chapter_id == CompiledChapter.id)
                .order_by(CompiledChapter.chapter_order, NarrativeEventRecord.sequence_index)
                .all()
            )

        timeline: list[dict[str, Any]] = []
        for ev, ch in events:
            participants = []
            try:
                participants = json.loads(ev.participants_json)
            except (json.JSONDecodeError, TypeError):
                pass
            timeline.append(
                {
                    "chapter_order": ch.chapter_order,
                    "chapter_title": ch.title,
                    "summary": ev.summary,
                    "participants": participants,
                    "importance": ev.importance,
                    "location": ev.location or "",
                    "quote": ev.evidence_quote or "",
                }
            )

        path = self.output_dir / "timeline.json"
        atomic_write_json(path, timeline)
        logger.info(f"事件时间线已导出: {path} ({len(timeline)} 条)")
        return timeline

    def export_all(self) -> dict[str, Any]:
        """导出所有 JSON 知识文件。"""
        return {
            "world": self.export_world_json(),
            "characters": self.export_characters_json(),
            "timeline": self.export_timeline_json(),
        }

    # ── 向量检索 ─────────────────────────────────────────

    def build_index(self) -> faiss.IndexFlatIP | None:
        """构建 FAISS 向量索引。

        需要安装 faiss-cpu 和 numpy。
        返回 None 表示依赖不可用。
        """
        if not _NUMPY_OK or not _FAISS_OK:
            logger.warning("FAISS 不可用 (pip install faiss-cpu numpy)，跳过索引构建")
            return None

        with get_session() as session:
            entities = session.query(Entity).all()

        if not entities:
            logger.warning("无实体数据，跳过索引构建")
            return None

        texts = [
            f"[{e.entity_type}] {e.canonical_name}: {e.description}"
            for e in entities
        ]

        # 简易 embedding：使用字符级特征向量（TF-IDF 近似）
        # 生产环境应替换为真正的 embedding 模型（如 bge-small）
        try:
            embeddings = self._simple_embed(texts)
        except Exception as exc:
            logger.error(f"embedding 生成失败: {exc}")
            return None

        dim = embeddings.shape[1]
        index = faiss.IndexFlatIP(dim)  # 内积相似度
        index.add(embeddings.astype(np.float32))
        logger.info(f"FAISS 索引已构建: {len(texts)} 条, dim={dim}")

        # 保存文本映射
        mapping_path = self.output_dir / "index_texts.json"
        atomic_write_json(mapping_path, texts)

        return index

    def search(
        self,
        query: str,
        *,
        top_k: int = 5,
        index: faiss.IndexFlatIP | None = None,
    ) -> list[SearchResult]:
        """语义搜索（FAISS 优先，回退全文检索）。"""
        with get_session() as session:
            entities = session.query(Entity).all()

        if index is not None and _NUMPY_OK:
            return self._search_faiss(entities, query, top_k, index)

        return self._search_fallback(entities, query, top_k)

    def _search_faiss(
        self,
        entities: list[Entity],
        query: str,
        top_k: int,
        index: faiss.IndexFlatIP,
    ) -> list[SearchResult]:
        """FAISS 向量检索。"""
        q_embed = self._simple_embed([query]).astype(np.float32)
        scores, indices = index.search(q_embed, min(top_k, len(entities)))
        results: list[SearchResult] = []
        for score, idx_ptr in zip(scores[0], indices[0]):
            if idx_ptr < 0 or idx_ptr >= len(entities):
                continue
            e = entities[int(idx_ptr)]
            results.append(
                SearchResult(
                    type=e.entity_type,
                    id=e.id,
                    title=e.canonical_name,
                    content=e.description,
                    score=float(score),
                    metadata={"entity_type": e.entity_type},
                )
            )
        return results

    def _search_fallback(
        self,
        entities: list[Entity],
        query: str,
        top_k: int,
    ) -> list[SearchResult]:
        """全文检索兜底。"""
        with get_session() as session:
            entity_ids = [e.id for e in entities]
            all_aliases: list[EntityAlias] = (
                session.query(EntityAlias)
                .filter(EntityAlias.entity_id.in_(entity_ids))
                .all()
            ) if entity_ids else []
            alias_map: dict[str, list[str]] = {}
            for a in all_aliases:
                alias_map.setdefault(a.entity_id, []).append(a.alias.lower())

        results: list[SearchResult] = []
        query_lower = query.lower()
        for e in entities:
            score = 0.0
            if query_lower in e.canonical_name.lower():
                score += 5.0
            if query_lower in e.description.lower():
                score += 3.0
            entity_aliases = alias_map.get(e.id, [])
            if any(query_lower in a for a in entity_aliases):
                score += 4.0
            if score > 0:
                results.append(
                    SearchResult(
                        type=e.entity_type,
                        id=e.id,
                        title=e.canonical_name,
                        content=e.description,
                        score=score,
                        metadata={"entity_type": e.entity_type},
                    )
                )
        results.sort(key=lambda x: x.score, reverse=True)
        return results[:top_k]

    @staticmethod
    def _simple_embed(texts: list[str]) -> Any:
        """简易字符特征向量（生产环境替换为 embedding 模型）。"""
        if not _NUMPY_OK:
            raise ImportError("需要 numpy")
        dim = 256
        embeddings = np.zeros((len(texts), dim), dtype=np.float32)
        for i, text in enumerate(texts):
            for j, ch in enumerate(text[:500]):
                embeddings[i, j % dim] += ord(ch) / 65536.0
        # 归一化
        norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        embeddings /= norms
        return embeddings


def create_knowledge_base(project_root: str | Path) -> KnowledgeBase:
    """便捷工厂函数。"""
    return KnowledgeBase(project_root)


def search(
    project_root: str | Path,
    query: str,
    *,
    top_k: int = 5,
) -> list[SearchResult]:
    """便捷搜索函数（每次新建 KB，适合低频查询）。"""
    kb = KnowledgeBase(project_root)
    return kb.search(query, top_k=top_k)
