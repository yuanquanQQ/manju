"""知识库模块初始化。"""
from app.knowledge.knowledge_base import (
    KnowledgeBase,
    SearchResult,
    create_knowledge_base,
    search,
)

__all__ = ["KnowledgeBase", "SearchResult", "create_knowledge_base", "search"]
