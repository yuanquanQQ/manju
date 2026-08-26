"""验收工具：随机抽查章节分析的准确性。

从已分析的章节中随机抽取 N 章，将原文和分析结果呈现给 LLM 进行交叉验证，
计算准确率并输出报告。
"""
from __future__ import annotations

import json
import random
from pathlib import Path

import pydantic

from app.adapters.llm import OpenAICompatibleLLM, StructuredLLM
from app.core.files import atomic_write_json
from app.core.logger import logger
from app.database.db import get_session
from app.database.models import ChapterAnalysisRun, CompiledChapter

VALIDATOR_SYSTEM_PROMPT = """你是一个小说分析的质量审核员。

你的任务：对比"原文"和"分析结果"，判断分析是否准确。

评审维度：
1. 实体抽取：surface_text 是否确实在原文中出现？entity_type 判断是否合理？
2. 事件抽取：summary 是否忠实反映了原文内容？importance 评分是否合理？
3. 对白抽取：dialogue 的 speaker 和 text 是否与原文一致？
4. 状态变化：是否确实在原文中发生？

每个维度给出：pass / partial / fail，并附简要理由。
禁止输出思考过程，只输出 JSON。"""


class DimensionScore(pydantic.BaseModel):
    verdict: str  # "pass" / "partial" / "fail"
    reason: str


class SpotCheckResult(pydantic.BaseModel):
    chapter_id: str
    chapter_title: str
    model: str
    entity_score: DimensionScore
    event_score: DimensionScore
    dialogue_score: DimensionScore
    state_change_score: DimensionScore
    overall_verdict: str  # "pass" / "partial" / "fail"
    remarks: str = ""


class ValidationReport(pydantic.BaseModel):
    total_checked: int
    passed: int
    partial: int
    failed: int
    accuracy_rate: float
    details: list[SpotCheckResult]


def _validate_one_chapter(
    chapter: CompiledChapter,
    run: ChapterAnalysisRun,
    llm: StructuredLLM,
) -> SpotCheckResult | None:
    """对单个章节的分析结果进行 LLM 交叉验证。"""
    analysis = json.loads(run.output_json)

    # 取原文前 1500 字符 + 分析摘要
    excerpt = chapter.content[:1500]
    analysis_summary = {
        "mentions": [
            {
                "surface_text": m.get("surface_text", ""),
                "entity_type": m.get("entity_type", ""),
                "description": m.get("description", ""),
                "quote": m.get("quote", "")[:200],
            }
            for m in analysis.get("mentions", [])[:5]
        ],
        "events": [
            {
                "summary": e.get("summary", ""),
                "participants": e.get("participants", []),
                "importance": e.get("importance", 0),
                "quote": e.get("quote", "")[:200],
            }
            for e in analysis.get("events", [])[:3]
        ],
        "dialogues": [
            {
                "speaker": d.get("speaker", ""),
                "text": d.get("text", ""),
                "quote": d.get("quote", "")[:200],
            }
            for d in analysis.get("dialogues", [])[:3]
        ],
        "state_changes": [
            {
                "entity": sc.get("entity", ""),
                "attribute": sc.get("attribute", ""),
                "before": sc.get("before", ""),
                "after": sc.get("after", ""),
            }
            for sc in analysis.get("state_changes", [])[:2]
        ],
    }

    prompt = (
        "请评审以下小说片段的 AI 分析准确性。\n\n"
        f"原文片段（前1500字）：\n```\n{excerpt}\n```\n\n"
        f"AI 分析结果：\n```json\n{json.dumps(analysis_summary, ensure_ascii=False, indent=2)}\n```\n\n"
        "请输出：\n"
        '{"entity_score": {"verdict": "pass/partial/fail", "reason": "..."},\n'
        ' "event_score": {"verdict": "pass/partial/fail", "reason": "..."},\n'
        ' "dialogue_score": {"verdict": "pass/partial/fail", "reason": "..."},\n'
        ' "state_change_score": {"verdict": "pass/partial/fail", "reason": "..."},\n'
        ' "overall_verdict": "pass/partial/fail",\n'
        ' "remarks": "..."}'
    )

    try:
        value = llm.complete(
            system_prompt=VALIDATOR_SYSTEM_PROMPT,
            user_prompt=prompt,
            json_schema={
                "type": "object",
                "properties": {
                    "entity_score": {
                        "type": "object",
                        "properties": {
                            "verdict": {"type": "string"},
                            "reason": {"type": "string"},
                        },
                        "required": ["verdict", "reason"],
                    },
                    "event_score": {
                        "type": "object",
                        "properties": {
                            "verdict": {"type": "string"},
                            "reason": {"type": "string"},
                        },
                        "required": ["verdict", "reason"],
                    },
                    "dialogue_score": {
                        "type": "object",
                        "properties": {
                            "verdict": {"type": "string"},
                            "reason": {"type": "string"},
                        },
                        "required": ["verdict", "reason"],
                    },
                    "state_change_score": {
                        "type": "object",
                        "properties": {
                            "verdict": {"type": "string"},
                            "reason": {"type": "string"},
                        },
                        "required": ["verdict", "reason"],
                    },
                    "overall_verdict": {"type": "string"},
                    "remarks": {"type": "string"},
                },
                "required": [
                    "entity_score",
                    "event_score",
                    "dialogue_score",
                    "state_change_score",
                    "overall_verdict",
                ],
            },
        )
    except Exception as exc:
        logger.error(f"验证 {chapter.chapter_id} 失败: {exc}")
        return None

    return SpotCheckResult(
        chapter_id=chapter.chapter_id,
        chapter_title=chapter.title,
        model=run.model,
        entity_score=DimensionScore(**value.get("entity_score", {})),
        event_score=DimensionScore(**value.get("event_score", {})),
        dialogue_score=DimensionScore(**value.get("dialogue_score", {})),
        state_change_score=DimensionScore(**value.get("state_change_score", {})),
        overall_verdict=value.get("overall_verdict", "fail"),
        remarks=value.get("remarks", ""),
    )


def run_validation(
    project_root: str | Path,
    *,
    sample_count: int = 3,
    seed: int | None = None,
    llm: StructuredLLM | None = None,
) -> ValidationReport:
    """随机抽查已分析章节的准确性。"""
    root = Path(project_root)
    client = llm or OpenAICompatibleLLM()
    if seed is not None:
        random.seed(seed)

    with get_session() as session:
        # 查找所有成功的分析
        runs = (
            session.query(ChapterAnalysisRun)
            .filter(ChapterAnalysisRun.status == "SUCCEEDED")
            .all()
        )
        runs.sort(key=lambda r: r.completed_at or "", reverse=True)

        if not runs:
            raise ValueError("没有成功的分析数据，请先执行 compile")

        actual_count = min(sample_count, len(runs))
        sampled = random.sample(runs, actual_count)

        details: list[SpotCheckResult] = []
        for run in sampled:
            chapter = session.query(CompiledChapter).filter(
                CompiledChapter.id == run.chapter_id
            ).first()
            if not chapter:
                logger.warning(f"章节 {run.chapter_id} 找不到原文，跳过")
                continue

            result = _validate_one_chapter(chapter, run, client)
            if result:
                details.append(result)
                verdict = result.overall_verdict
                logger.info(
                    f"抽查 {chapter.chapter_id}: {verdict} "
                    f"(E={result.entity_score.verdict} "
                    f"EV={result.event_score.verdict} "
                    f"D={result.dialogue_score.verdict})"
                )

    passed = sum(1 for d in details if d.overall_verdict == "pass")
    partial = sum(1 for d in details if d.overall_verdict == "partial")
    failed = sum(1 for d in details if d.overall_verdict == "fail")

    report = ValidationReport(
        total_checked=len(details),
        passed=passed,
        partial=partial,
        failed=failed,
        accuracy_rate=(
            (passed + partial * 0.5) / len(details) * 100
            if details
            else 0.0
        ),
        details=details,
    )

    # 保存报告
    report_path = root / "production" / "validation_report.json"
    atomic_write_json(report_path, report.model_dump(mode="json"))
    logger.info(f"验收报告已保存: {report_path}")

    return report
