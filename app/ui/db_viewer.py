"""数据库浏览面板 — Streamlit 可视化。

用法: streamlit run app/ui/db_viewer.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

# 项目根目录
_root = Path(__file__).resolve().parents[2]
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

import streamlit as st
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.database.db import make_engine
from app.database.models import (
    Artifact,
    ChapterAnalysisRun,
    CompiledChapter,
    DialogueRecord,
    Entity,
    EntityAlias,
    EntityMentionRecord,
    Job,
    NarrativeEventRecord,
    NovelSourceDocument,
    StateChangeRecord,
)

st.set_page_config(page_title="novel2anime DB", layout="wide")
st.title("novel2anime 数据库浏览")

# ── 项目选择 ─────────────────────────────────────────────
projects_dir = _root / "projects"
project_names = sorted(
    d.name for d in projects_dir.iterdir()
    if d.is_dir() and (d / "database" / "world.db").exists()
)

if not project_names:
    st.error("未找到项目数据库，请先创建项目")
    st.stop()

project = st.selectbox("选择项目", project_names)
db_path = str(projects_dir / project / "database" / "world.db")
engine = make_engine(db_path)

TABLES = {
    "compiled_chapters": CompiledChapter,
    "chapter_analysis_runs": ChapterAnalysisRun,
    "entities": Entity,
    "entity_aliases": EntityAlias,
    "entity_mentions": EntityMentionRecord,
    "narrative_events": NarrativeEventRecord,
    "dialogues": DialogueRecord,
    "state_changes": StateChangeRecord,
    "jobs": Job,
    "artifacts": Artifact,
    "source_documents": NovelSourceDocument,
}

# ── 概览 ─────────────────────────────────────────────────
st.header("总览")
with Session(engine) as session:
    cols = st.columns(4)
    for i, (label, model) in enumerate(TABLES.items()):
        cnt = session.query(func.count()).select_from(model).scalar()
        with cols[i % 4]:
            st.metric(label, cnt)

# ── Tabs —————————————————————————————————————————————————
tabs = st.tabs([
    "章节", "分析运行", "实体", "实体提及",
    "事件", "对白", "状态变化", "任务",
    "生产文件",
])

# ── 章节 ─────────────────────────────────────────────────
with tabs[0]:
    st.subheader("compiled_chapters — 已导入章节")
    with Session(engine) as session:
        chapters = session.query(CompiledChapter).order_by(
            CompiledChapter.chapter_order
        ).all()
    if chapters:
        st.dataframe(
            [{
                "ID": ch.id,
                "章节号": ch.chapter_order,
                "标题": ch.title,
                "字数": len(ch.content),
                "活跃": "✅" if ch.active else "❌",
                "预览": ch.content[:100].replace("\n", " "),
            } for ch in chapters],
            use_container_width=True, hide_index=True,
        )

# ── 分析运行 ─────────────────────────────────────────────
with tabs[1]:
    st.subheader("chapter_analysis_runs — LLM 分析运行")
    with Session(engine) as session:
        runs = session.query(ChapterAnalysisRun).order_by(
            ChapterAnalysisRun.completed_at.desc()
        ).all()
    if runs:
        rows = []
        for r in runs:
            preview = ""
            try:
                p = json.loads(r.output_json)
                preview = f"实体={len(p.get('mentions',[]))} 事件={len(p.get('events',[]))} | {p.get('summary','')[:60]}"
            except Exception:
                pass
            rows.append({
                "章节": r.chapter_id, "状态": r.status,
                "模型": r.model, "摘要": preview,
                "错误": (r.error_message or "")[:60],
                "完成时间": str(r.completed_at or ""),
            })
        st.dataframe(rows, use_container_width=True, hide_index=True)
    else:
        st.info("无数据")

# ── 实体 ─────────────────────────────────────────────────
with tabs[2]:
    st.subheader("entities — 人物/地点/物品")
    tp = st.selectbox("类型", ["全部","character","location","prop","ability","organization","creature"], key="et")
    with Session(engine) as session:
        q = session.query(Entity)
        if tp != "全部":
            q = q.filter(Entity.entity_type == tp)
        entities = q.order_by(Entity.entity_type, Entity.canonical_name).all()
        eids = [e.id for e in entities]
        aliases = session.query(EntityAlias).filter(EntityAlias.entity_id.in_(eids)).all() if eids else []
        amap: dict = {}
        for a in aliases:
            amap.setdefault(a.entity_id, []).append(a.alias)
    if entities:
        st.dataframe(
            [{
                "名称": e.canonical_name, "类型": e.entity_type,
                "描述": e.description[:120],
                "别名": ", ".join(amap.get(e.id, [])[:5]),
                "首现": e.first_chapter_order,
            } for e in entities],
            use_container_width=True, hide_index=True,
        )
        sel = st.selectbox("详情", [e.canonical_name for e in entities])
        if sel:
            e = next(x for x in entities if x.canonical_name == sel)
            st.text_area("完整描述", e.description, height=100)
            st.caption(f"ID: {e.id} | 首现章节: {e.first_chapter_order} | 别名: {', '.join(amap.get(e.id, []))}")

# ── 实体提及 ─────────────────────────────────────────────
with tabs[3]:
    st.subheader("entity_mentions — 原文中的实体提及")
    with Session(engine) as session:
        mentions = session.query(EntityMentionRecord).order_by(
            EntityMentionRecord.chapter_id
        ).limit(500).all()
        total = session.query(func.count()).select_from(EntityMentionRecord).scalar()
    if mentions:
        st.dataframe(
            [{
                "章节": m.chapter_id, "类型": m.entity_type,
                "表面文本": m.surface_text, "描述": (m.description or "")[:60],
                "原文": (m.evidence_quote or "")[:60],
                "置信度": f"{m.confidence:.0%}",
            } for m in mentions],
            use_container_width=True, hide_index=True,
        )
        st.caption(f"显示前 500 条 / 共 {total} 条")

# ── 事件 ─────────────────────────────────────────────────
with tabs[4]:
    st.subheader("narrative_events — 叙事事件")
    with Session(engine) as session:
        events = session.query(NarrativeEventRecord, CompiledChapter).join(
            CompiledChapter, NarrativeEventRecord.chapter_id == CompiledChapter.id
        ).order_by(CompiledChapter.chapter_order, NarrativeEventRecord.sequence_index).all()
    if events:
        st.dataframe(
            [{
                "章节": f"第{ch.chapter_order}章", "序号": ev.sequence_index,
                "摘要": ev.summary[:100],
                "参与": ", ".join(json.loads(ev.participants_json or "[]"))[:40],
                "地点": ev.location or "", "重要度": "⭐" * ev.importance,
            } for ev, ch in events],
            use_container_width=True, hide_index=True,
        )

# ── 对白 ─────────────────────────────────────────────────
with tabs[5]:
    st.subheader("dialogues — 对白")
    with Session(engine) as session:
        dialogues = session.query(DialogueRecord, CompiledChapter).join(
            CompiledChapter, DialogueRecord.chapter_id == CompiledChapter.id
        ).order_by(CompiledChapter.chapter_order, DialogueRecord.evidence_start).all()
    if dialogues:
        st.dataframe(
            [{
                "章节": f"第{ch.chapter_order}章", "说话者": d.speaker,
                "接收者": d.addressee or "—", "内容": d.text[:100],
                "情感": d.emotion or "",
            } for d, ch in dialogues],
            use_container_width=True, hide_index=True,
        )

# ── 状态变化 ─────────────────────────────────────────────
with tabs[6]:
    st.subheader("state_changes — 状态变化")
    with Session(engine) as session:
        changes = session.query(StateChangeRecord, CompiledChapter).join(
            CompiledChapter, StateChangeRecord.chapter_id == CompiledChapter.id
        ).order_by(CompiledChapter.chapter_order).all()
    if changes:
        st.dataframe(
            [{
                "章节": f"第{ch.chapter_order}章", "实体": sc.entity_name,
                "属性": sc.attribute,
                "变化": f"{sc.before_value} → {sc.after_value}"[:60],
            } for sc, ch in changes],
            use_container_width=True, hide_index=True,
        )

# ── 任务 ─────────────────────────────────────────────────
with tabs[7]:
    st.subheader("jobs — 后台任务")
    with Session(engine) as session:
        jobs = session.query(Job).order_by(Job.created_at.desc()).limit(50).all()
    if jobs:
        st.dataframe(
            [{
                "ID": j.id[:8], "类型": j.job_type, "状态": j.status,
                "进度": f"{j.progress:.0%}", "错误": (j.error_message or "")[:60],
            } for j in jobs],
            use_container_width=True, hide_index=True,
        )

# ── 生产文件 ─────────────────────────────────────────────
with tabs[8]:
    prod = projects_dir / project / "production"
    st.subheader("production/ — 生成的 JSON 文件")

    for sub, label in [
        ("episodes", "分镜 (episodes/)"),
        ("knowledge", "知识库 (knowledge/)"),
        ("analysis", "分析结果 (analysis/)"),
    ]:
        d = prod / sub
        st.markdown(f"**{label}**")
        files = sorted(d.glob("*.json")) if d.exists() else []
        if files:
            for f in files:
                with st.expander(f.name):
                    try:
                        st.json(json.loads(f.read_text(encoding="utf-8")))
                    except Exception:
                        st.code(f.read_text(encoding="utf-8")[:2000])
        else:
            st.caption("  (无文件)")
