"""novel2anime 入口。

用法：
    python main.py                       # 显示帮助
    python main.py create my_project     # 新建项目目录
    python main.py ingest my_project     # 抽取 chapters/*.json 并入库
"""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import Annotated

import typer

from app.compiler.importer import import_novel
from app.compiler.repository import (
    list_compiled_chapters,
    persist_import,
)
from app.core.config import settings
from app.core.doctor import run_diagnostics
from app.core.logger import setup_logger
from app.database.db import init_db, restore_database
from app.domain.jobs import JobStatus
from app.pipeline.compile_novel import run_compile_novel
from app.pipeline.generate_image import (
    generate_custom,
    generate_from_entities,
)
from app.pipeline.ingest import run_ingest
from app.pipeline.storyboard import generate_storyboard
from app.knowledge.knowledge_base import KnowledgeBase
from app.validator.validator import run_validation
from app.services.job_service import (
    InvalidJobTransitionError,
    JobNotFoundError,
    list_jobs,
    recover_interrupted_jobs,
    request_cancel,
    request_pause,
    resume_job,
)
from app.services.project_service import (
    create_project,
    ensure_legacy_project_metadata,
    resolve_project_dir,
)

app = typer.Typer(help="AI 漫剧生成系统 V1（novel2anime）", no_args_is_help=True)


def _open_project(name: str, *, initialize_db: bool = True) -> Path:
    try:
        root = resolve_project_dir(name, must_exist=True)
    except (ValueError, FileNotFoundError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    ensure_legacy_project_metadata(root, name)
    if initialize_db:
        init_db(root / "database" / "world.db")
    return root


@app.command()
def create(
    name: str = typer.Argument(..., help="项目名"),
    display_name: str | None = typer.Option(None, help="项目显示名称"),
):
    """创建一个新项目目录。"""
    setup_logger()
    try:
        root = create_project(name, display_name=display_name)
    except (ValueError, FileExistsError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(f"已创建项目: {root}")


@app.command("gui")
def gui_cmd():
    """启动本地桌面制作应用。"""
    from app.ui.desktop import run_desktop_app

    raise typer.Exit(code=run_desktop_app())


@app.command()
def ingest(
    name: str = typer.Argument(..., help="项目名"),
    chapters_dir: str = typer.Option("chapters", help="chapters 目录名"),
    limit: int = typer.Option(0, help="只处理前 N 章（0=全部）"),
    force: bool = typer.Option(False, "--force", "-f", help="强制重新抽取（忽略已抽取标记）"),
):
    """扫描 chapters/*.json，调用 LLM 抽取结构化信息并写入 SQLite。"""
    setup_logger()
    root = _open_project(name, initialize_db=False)

    chapters_path = root / chapters_dir
    if not chapters_path.exists():
        typer.echo(f"未找到目录: {chapters_path}")
        typer.echo(
            "请把章节 JSON 放入该目录（命名 chapter_001.json / chapter_002.json ...）"
        )
        raise typer.Exit(code=1)

    stats = run_ingest(root, chapters_dir, force=force, limit=limit)

    typer.echo("完成")
    for k, v in stats.items():
        typer.echo(f"  {k}: {v}")


@app.command("import-novel")
def import_novel_cmd(
    name: Annotated[str, typer.Argument(help="项目名")],
    source: Annotated[
        Path,
        typer.Argument(help="TXT、Markdown 或章节 JSON 目录"),
    ],
    limit: int = typer.Option(0, min=0, help="只导入前 N 章（0=全部）"),
):
    """导入原始小说并写入版本化标准章节。"""
    setup_logger()
    root = _open_project(name)
    try:
        result = import_novel(source, root, limit=limit)
        stats = persist_import(result)
    except (FileNotFoundError, UnicodeError, ValueError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(f"来源: {result.source.original_name}")
    typer.echo(f"文档 ID: {result.source.document_id}")
    typer.echo(f"标准章节: {len(result.chapters)}")
    typer.echo(f"新增/更新: {stats['created']}/{stats['updated']}")


@app.command("compile")
def compile_novel_cmd(
    name: str = typer.Argument(..., help="项目名"),
    limit: int = typer.Option(0, min=0, help="只分析前 N 章（0=全部，与 --start/--end 互斥）"),
    start: int = typer.Option(0, min=0, help="起始章节号"),
    end: int = typer.Option(0, min=0, help="结束章节号"),
    force: bool = typer.Option(False, "--force", "-f", help="忽略可复用分析"),
):
    """对标准章节执行严格 Schema、带原文证据的结构化分析。"""
    setup_logger()
    root = _open_project(name)

    if start or end:
        limit = 0

    try:
        stats = run_compile_novel(limit=limit, start=start, end=end, force=force, project_root=root)
    except (ValueError, RuntimeError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    for key, value in stats.items():
        typer.echo(f"{key}: {value}")
    if stats.get("failed", 0):
        raise typer.Exit(code=1)


@app.command()
def info(name: str | None = typer.Argument(None, help="可选项目名")):
    """显示当前配置与目录状态。"""
    setup_logger()
    typer.echo(f"项目根目录: {settings.project_root}")
    typer.echo(f"LLM URL: {settings.llm_base_url}")
    typer.echo(f"LLM 模型: {settings.llm_model}")
    typer.echo(f"projects/: {settings.projects_dir}")
    if name:
        root = _open_project(name)
        typer.echo(f"当前项目: {root}")


@app.command("doctor")
def doctor_cmd(
    name: str | None = typer.Argument(None, help="可选项目名"),
    skip_llm: bool = typer.Option(False, help="跳过本地 LLM 连接检查"),
):
    """检查 Python、媒体工具、GPU、LLM、磁盘和项目数据库。"""
    setup_logger()
    root = _open_project(name) if name else None
    checks = run_diagnostics(project_root=root, check_llm=not skip_llm)
    for check in checks:
        typer.echo(f"[{check.status:4}] {check.name}: {check.message}")
    if any(check.status == "FAIL" for check in checks):
        raise typer.Exit(code=1)


@app.command()
def status(
    name: str = typer.Argument(..., help="项目名"),
    state: Annotated[
        JobStatus | None,
        typer.Option(help="按任务状态过滤"),
    ] = None,
    limit: Annotated[int, typer.Option(min=1, max=500)] = 20,
):
    """查看项目任务状态。"""
    setup_logger()
    _open_project(name)
    jobs = list_jobs(status=state, limit=limit)
    if not jobs:
        typer.echo("暂无任务")
        return
    for job in jobs:
        typer.echo(
            f"{job.id}  {job.status:<10}  {job.progress:>6.1%}  "
            f"{job.job_type}  attempt={job.attempt}/{job.max_retries}"
        )


@app.command()
def pause(
    name: str = typer.Argument(..., help="项目名"),
    job_id: str = typer.Argument(..., help="任务 ID"),
):
    """暂停等待中任务，或向运行中任务发送协作式暂停请求。"""
    setup_logger()
    _open_project(name)
    try:
        job = request_pause(job_id)
    except (JobNotFoundError, InvalidJobTransitionError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(f"任务状态: {job.id} {job.status}")


@app.command()
def cancel(
    name: str = typer.Argument(..., help="项目名"),
    job_id: str = typer.Argument(..., help="任务 ID"),
):
    """取消等待中任务，或向运行中任务发送协作式取消请求。"""
    setup_logger()
    _open_project(name)
    try:
        job = request_cancel(job_id)
    except (JobNotFoundError, InvalidJobTransitionError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(f"任务状态: {job.id} {job.status}")


@app.command()
def resume(
    name: str = typer.Argument(..., help="项目名"),
    job_id: str = typer.Argument(..., help="任务 ID"),
):
    """把 PAUSED、FAILED 或 STALE 任务恢复为 PENDING。"""
    setup_logger()
    _open_project(name)
    try:
        job = resume_job(job_id)
    except (JobNotFoundError, InvalidJobTransitionError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(f"任务已恢复: {job.id} {job.status}")


@app.command()
def recover(
    name: str = typer.Argument(..., help="项目名"),
    stale_after: int = typer.Option(
        settings.pipeline_stale_after_seconds,
        min=1,
        help="超过多少秒无心跳视为中断",
    ),
):
    """识别失去心跳的运行任务并安全暂停。"""
    setup_logger()
    _open_project(name)
    recovered = recover_interrupted_jobs(stale_after)
    typer.echo(f"已暂停 {len(recovered)} 个中断任务")
    for job_id in recovered:
        typer.echo(f"  {job_id}")


@app.command("clean-cache")
def clean_cache(
    name: str = typer.Argument(..., help="项目名"),
    yes: bool = typer.Option(False, "--yes", help="确认删除项目缓存"),
):
    """清理可重建的项目 cache/，不删除已登记资产和输出。"""
    setup_logger()
    root = _open_project(name, initialize_db=False)
    cache_dir = (root / "cache").resolve()
    if cache_dir.parent != root.resolve():
        typer.echo("缓存路径超出项目目录，拒绝清理", err=True)
        raise typer.Exit(code=1)
    if not yes:
        typer.echo(f"将清理: {cache_dir}")
        typer.echo("请添加 --yes 确认")
        raise typer.Exit(code=1)
    if cache_dir.exists():
        shutil.rmtree(cache_dir)
    cache_dir.mkdir()
    typer.echo(f"缓存已清理: {cache_dir}")


@app.command("restore-db")
def restore_db(
    name: str = typer.Argument(..., help="项目名"),
    backup: str = typer.Argument(
        "world.db.pre-schema-v1.bak",
        help="database/ 内的备份文件名",
    ),
    yes: bool = typer.Option(False, "--yes", help="确认恢复数据库"),
):
    """从 database/ 内备份恢复；当前数据库会先保存为安全备份。"""
    setup_logger()
    root = _open_project(name, initialize_db=False)
    if Path(backup).name != backup:
        typer.echo("备份参数只能是 database/ 内的文件名", err=True)
        raise typer.Exit(code=1)
    database_dir = (root / "database").resolve()
    target = (database_dir / "world.db").resolve()
    backup_path = (database_dir / backup).resolve()
    if target.parent != database_dir or backup_path.parent != database_dir:
        typer.echo("数据库路径超出项目目录，拒绝恢复", err=True)
        raise typer.Exit(code=1)
    if not yes:
        typer.echo(f"将使用备份恢复数据库: {backup_path}")
        typer.echo("当前数据库会先创建安全备份；请添加 --yes 确认")
        raise typer.Exit(code=1)
    try:
        safety_backup = restore_database(target, backup_path)
    except (FileNotFoundError, ValueError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(f"数据库恢复完成；恢复前版本保存在: {safety_backup}")


@app.command("generate")
def generate_cmd(
    name: str = typer.Argument(..., help="项目名"),
    entity_type: str = typer.Option(
        "character",
        "--type",
        "-t",
        help="实体类型: character/location/prop/ability/creature/organization",
    ),
    limit: int = typer.Option(3, min=1, max=20, help="生成数量"),
    width: int = typer.Option(1024, min=512, max=2048, help="图片宽度"),
    height: int = typer.Option(1024, min=512, max=2048, help="图片高度"),
    steps: int = typer.Option(25, min=10, max=50, help="采样步数"),
    cfg: float = typer.Option(7.0, min=1.0, max=20.0, help="CFG scale"),
    seed: int = typer.Option(-1, help="随机种子，-1 为随机"),
):
    """从数据库取实体描述，调 ComfyUI 生成图片。"""
    from app.adapters.comfyui import ComfyUIClient

    setup_logger()
    root = _open_project(name, initialize_db=False)

    comfy = ComfyUIClient(
        base_url=settings.comfyui_url,
        timeout=settings.comfyui_timeout,
    )
    try:
        comfy.health()
    except Exception as exc:
        typer.echo(f"ComfyUI 连接失败 ({settings.comfyui_url}): {exc}", err=True)
        raise typer.Exit(code=1)

    output_dir = root / "assets" / entity_type
    seed_val = None if seed < 0 else seed

    saved = generate_from_entities(
        output_dir,
        entity_type=entity_type,
        limit=limit,
        client=comfy,
        width=width,
        height=height,
        steps=steps,
        cfg=cfg,
        seed=seed_val,
    )
    typer.echo(f"生成完成，共 {len(saved)} 张图片")
    for p in saved:
        typer.echo(f"  {p}")


@app.command("generate-custom")
def generate_custom_cmd(
    name: str = typer.Argument(..., help="项目名"),
    prompt: str = typer.Argument(..., help="正向提示词"),
    negative: str = typer.Option("", "--negative", "-n", help="负向提示词"),
    width: int = typer.Option(1024, min=512, max=2048, help="图片宽度"),
    height: int = typer.Option(1024, min=512, max=2048, help="图片高度"),
    steps: int = typer.Option(25, min=10, max=50, help="采样步数"),
    cfg: float = typer.Option(7.0, min=1.0, max=20.0, help="CFG scale"),
    seed: int = typer.Option(-1, help="随机种子，-1 为随机"),
):
    """用自定义 prompt 调 ComfyUI 生成图片。"""
    from app.adapters.comfyui import ComfyUIClient

    setup_logger()
    root = _open_project(name, initialize_db=False)

    comfy = ComfyUIClient(
        base_url=settings.comfyui_url,
        timeout=settings.comfyui_timeout,
    )
    try:
        comfy.health()
    except Exception as exc:
        typer.echo(f"ComfyUI 连接失败 ({settings.comfyui_url}): {exc}", err=True)
        raise typer.Exit(code=1)

    output_dir = root / "assets" / "custom"
    seed_val = None if seed < 0 else seed

    saved = generate_custom(
        prompt,
        output_dir,
        negative_prompt=negative,
        client=comfy,
        width=width,
        height=height,
        steps=steps,
        cfg=cfg,
        seed=seed_val,
    )
    for p in saved:
        typer.echo(f"  {p}")


@app.command("storyboard")
def storyboard_cmd(
    name: str = typer.Argument(..., help="项目名"),
    limit: int = typer.Option(0, min=0, help="只处理前 N 章（0=全部，与 --start/--end 互斥）"),
    start: int = typer.Option(0, min=0, help="起始章节号"),
    end: int = typer.Option(0, min=0, help="结束章节号"),
    list_only: bool = typer.Option(
        False, "--list", "-l", help="仅列出已生成和可用的章节"
    ),
):
    """将已分析章节转为分镜脚本。"""
    setup_logger()
    root = _open_project(name)

    # 查看进度
    episodes_dir = root / "production" / "episodes"
    existing = sorted(
        episodes_dir.glob("episode_*.json")
    ) if episodes_dir.exists() else []
    all_chapters = list_compiled_chapters()

    if list_only:
        typer.echo(f"数据库中共 {len(all_chapters)} 个活跃章节")
        typer.echo(f"已生成分镜: {len(existing)} 集\n")
        for ep in existing:
            num = ep.stem.replace("episode_", "")
            typer.echo(f"  episode_{num}.json")
        typer.echo(f"\n章节范围: 第1章 ~ 第{len(all_chapters)}章")
        return

    if start or end:
        limit = 0  # 范围模式和 limit 互斥

    try:
        episodes = generate_storyboard(root, limit=limit, start=start, end=end)
    except (ValueError, RuntimeError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc

    if not episodes:
        typer.echo("未生成分镜，请先执行 import-novel + compile")
        return

    total_shots = sum(len(ep.shots) for ep in episodes)
    total_duration = sum(
        sum(s.duration_seconds for s in ep.shots) for ep in episodes
    )
    typer.echo(f"完成！{len(episodes)} 集，{total_shots} 个镜头，总时长约 {total_duration:.0f}s")
    for ep in episodes:
        typer.echo(f"  第{ep.episode_number}集 [{ep.episode_title}]: {len(ep.shots)} 镜头")


@app.command("validate")
def validate_cmd(
    name: str = typer.Argument(..., help="项目名"),
    sample_count: int = typer.Option(3, min=1, max=20, help="抽查章节数"),
    seed: int = typer.Option(42, help="随机种子"),
):
    """随机抽查分析质量，计算准确率。"""
    setup_logger()
    root = _open_project(name)

    try:
        report = run_validation(root, sample_count=sample_count, seed=seed)
    except ValueError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc

    typer.echo(f"\n抽查 {report.total_checked} 章 | "
               f"通过 {report.passed} | 部分 {report.partial} | 失败 {report.failed}")
    typer.echo(f"准确率: {report.accuracy_rate:.1f}%")
    for d in report.details:
        icon = {"pass": "OK", "partial": "~", "fail": "X"}.get(d.overall_verdict, "?")
        typer.echo(f"  [{icon}] {d.chapter_id} ({d.chapter_title})")
        typer.echo(f"       实体={d.entity_score.verdict} "
                    f"事件={d.event_score.verdict} "
                    f"对白={d.dialogue_score.verdict} "
                    f"状态={d.state_change_score.verdict}")
    typer.echo(f"\n完整报告: {root / 'production' / 'validation_report.json'}")


@app.command("knowledge")
def knowledge_cmd(
    name: str = typer.Argument(..., help="项目名"),
    action: str = typer.Option("export", help="export | search"),
    query: str = typer.Option("", help="搜索关键词（action=search 时使用）"),
):
    """导出知识库或语义搜索。"""

    def _init_db():
        from pathlib import Path
        init_db(Path(f"projects/{name}/database/world.db"))

    if action == "export":
        setup_logger()
        root = _open_project(name)
        kb = KnowledgeBase(root)
        kb.export_all()
        typer.echo(f"知识文件已导出到: {kb.output_dir}")
        typer.echo(f"  world.json / characters.json / timeline.json")

    elif action == "search":
        setup_logger()
        _init_db()
        root = Path(f"projects/{name}")
        kb = KnowledgeBase(root)
        results = kb.search(query, top_k=5)
        if not results:
            typer.echo("未找到匹配结果。")
            return
        for r in results:
            typer.echo(f"  [{r.type}] {r.title} (score={r.score:.1f})")
            typer.echo(f"    {r.content[:100]}")
    else:
        typer.echo(f"未知操作: {action}，可选: export / search", err=True)


if __name__ == "__main__":
    app()
