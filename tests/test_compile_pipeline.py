from __future__ import annotations

from app.compiler.importer import import_text_file
from app.compiler.repository import persist_import
from app.database.db import get_session, init_db
from app.database.models import ChapterAnalysisRun, Job
from app.pipeline.compile_novel import run_compile_novel


class EmptyAnalysisLLM:
    model_name = "fake-model"

    def __init__(self):
        self.calls = 0

    def complete(self, **_kwargs):
        self.calls += 1
        return {
            "mentions": [],
            "events": [],
            "dialogues": [],
            "state_changes": [],
            "summary": "测试摘要",
            "adaptation_notes": [],
        }


class InvalidAnalysisLLM:
    model_name = "invalid-model"

    def complete(self, **_kwargs):
        return {"unexpected": True}


def _prepare_project(tmp_path):
    project = tmp_path / "project"
    source = tmp_path / "book.txt"
    source.write_text("第1章 开始\n测试正文。", encoding="utf-8")
    init_db(project / "database" / "world.db")
    persist_import(import_text_file(source, project))
    return project


def test_compile_pipeline_reuses_completed_job(tmp_path):
    _prepare_project(tmp_path)
    first_llm = EmptyAnalysisLLM()
    first = run_compile_novel(llm=first_llm)
    second_llm = EmptyAnalysisLLM()
    second = run_compile_novel(llm=second_llm)

    assert first["status"] == "SUCCEEDED"
    assert first["analyzed"] == 1
    assert first_llm.calls == 1
    assert second["reused_job"] is True
    assert second_llm.calls == 0


def test_compile_pipeline_records_failed_chapter(tmp_path, monkeypatch):
    _prepare_project(tmp_path)
    monkeypatch.setattr("app.compiler.analyzer.time.sleep", lambda _seconds: None)

    stats = run_compile_novel(llm=InvalidAnalysisLLM(), force=True)

    assert stats["status"] == "FAILED"
    assert stats["failed_chapters"] == ["ch_000001"]
    with get_session() as session:
        run = session.query(ChapterAnalysisRun).one()
        job = session.query(Job).one()
        assert run.status == "FAILED"
        assert job.status == "FAILED"

