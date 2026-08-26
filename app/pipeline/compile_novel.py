"""标准章节的可恢复结构化分析流水线。"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

from app.adapters.llm import OpenAICompatibleLLM, StructuredLLM
from app.compiler.analyzer import (
    PROMPT_VERSION,
    ChapterAnalysisError,
    analyze_chapter,
)
from app.compiler.repository import (
    find_reusable_analysis,
    list_compiled_chapters,
    save_analysis,
    save_analysis_failure,
)
from app.core.config import settings
from app.core.files import atomic_write_json, sha256_text
from app.core.logger import bind_logger
from app.domain.jobs import JobStatus
from app.services.job_service import (
    create_job,
    create_settings_snapshot,
    get_job,
    heartbeat_job,
    resume_job,
    transition_job,
)


def _compile_input_hash(chapter_hashes: list[str], model: str) -> str:
    return sha256_text("|".join([PROMPT_VERSION, model, *chapter_hashes]))


def run_compile_novel(
    *,
    limit: int = 0,
    start: int = 0,
    end: int = 0,
    force: bool = False,
    llm: StructuredLLM | None = None,
    project_root: str | Path | None = None,
    progress_callback: Callable[[int, int, str], None] | None = None,
) -> dict:
    client = llm or OpenAICompatibleLLM()
    chapters = list_compiled_chapters(limit=limit, start=start, end=end)
    if not chapters:
        raise ValueError("没有可编译的标准章节，请先执行 import-novel")

    # 分析结果 JSON 输出目录
    analysis_dir: Path | None = None
    if project_root:
        analysis_dir = Path(project_root) / "production" / "analysis"
        analysis_dir.mkdir(parents=True, exist_ok=True)

    input_hash = _compile_input_hash(
        [chapter.content_hash for chapter in chapters],
        client.model_name,
    )
    job = create_job(
        "novel.compile",
        payload={
            "chapter_ids": [chapter.chapter_id for chapter in chapters],
            "force": force,
        },
        input_hash=input_hash,
        max_retries=settings.llm_max_retries,
        reuse_existing=not force,
    )
    if JobStatus(job.status) is JobStatus.SUCCEEDED:
        result = json.loads(job.result_json or "{}")
        result["reused_job"] = True
        return result
    if JobStatus(job.status) is JobStatus.PAUSED:
        job = resume_job(job.id)
    if JobStatus(job.status) is not JobStatus.PENDING:
        raise RuntimeError(f"相同编译任务已在处理中: {job.id} {job.status}")

    transition_job(job.id, JobStatus.RUNNING)
    create_settings_snapshot(
        {
            "llm_model": client.model_name,
            "prompt_version": PROMPT_VERSION,
            "extract_max_chars": settings.extract_max_chars,
            "llm_max_retries": settings.llm_max_retries,
        },
        job_id=job.id,
    )
    log = bind_logger(job_id=job.id)
    stats = {
        "job_id": job.id,
        "total": len(chapters),
        "analyzed": 0,
        "reused": 0,
        "failed": 0,
        "failed_chapters": [],
        "reused_job": False,
    }

    try:
        for index, chapter in enumerate(chapters, start=1):
            if progress_callback:
                progress_callback(
                    index - 1,
                    len(chapters),
                    f"正在分析 {chapter.title}",
                )
            state = get_job(job.id)
            if state.cancel_requested:
                transition_job(job.id, JobStatus.CANCELED, result=stats)
                stats["status"] = JobStatus.CANCELED.value
                return stats
            if state.pause_requested:
                transition_job(job.id, JobStatus.PAUSED, result=stats)
                stats["status"] = JobStatus.PAUSED.value
                return stats

            if not force:
                reusable = find_reusable_analysis(
                    chapter,
                    model=client.model_name,
                    prompt_version=PROMPT_VERSION,
                )
                if reusable is not None:
                    stats["reused"] += 1
                    heartbeat_job(job.id, index / len(chapters))
                    continue

            try:
                analysis = analyze_chapter(chapter, llm=client)
                save_analysis(analysis)
                stats["analyzed"] += 1
                # 即时输出分析摘要
                mention_count = len(analysis.mentions) if analysis.mentions else 0
                event_count = len(analysis.events) if analysis.events else 0
                dialogue_count = len(analysis.dialogues) if analysis.dialogues else 0
                summary_preview = analysis.summary[:80].replace("\n", " ")
                log.info(
                    f"{chapter.chapter_id} ({chapter.title}) 分析完成 | "
                    f"实体={mention_count} 事件={event_count} 对白={dialogue_count} | "
                    f"{summary_preview}..."
                )
                # 保存 JSON 文件
                if analysis_dir:
                    json_path = analysis_dir / f"{chapter.chapter_id}.json"
                    atomic_write_json(json_path, analysis.model_dump(mode="json"))
                    log.info(f"  分析 JSON 已保存: {json_path}")
            except ChapterAnalysisError as exc:
                save_analysis_failure(
                    chapter,
                    model=client.model_name,
                    prompt_version=PROMPT_VERSION,
                    error_message=str(exc),
                )
                stats["failed"] += 1
                stats["failed_chapters"].append(chapter.chapter_id)
                log.error(str(exc))
            heartbeat_job(job.id, index / len(chapters))
            if progress_callback:
                progress_callback(
                    index,
                    len(chapters),
                    f"已分析 {index}/{len(chapters)} 章",
                )

        if stats["failed"]:
            stats["status"] = JobStatus.FAILED.value
            transition_job(
                job.id,
                JobStatus.FAILED,
                error_code="chapter_analysis_failed",
                error_message=f"{stats['failed']} 个章节分析失败",
                result=stats,
            )
        else:
            stats["status"] = JobStatus.SUCCEEDED.value
            transition_job(job.id, JobStatus.SUCCEEDED, result=stats)
        return stats
    except Exception as exc:
        current = get_job(job.id)
        if JobStatus(current.status) is JobStatus.RUNNING:
            transition_job(
                job.id,
                JobStatus.FAILED,
                error_code="pipeline_error",
                error_message=str(exc),
                result=stats,
            )
        raise
