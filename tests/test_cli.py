from __future__ import annotations

from dataclasses import replace

from typer.testing import CliRunner

import app.services.project_service as project_service
from app.core.config import settings
from main import app


def test_create_and_status_commands(tmp_path, monkeypatch):
    monkeypatch.setattr(
        project_service,
        "settings",
        replace(settings, projects_dir=tmp_path),
    )
    runner = CliRunner()

    created = runner.invoke(
        app,
        ["create", "cli_demo", "--display-name", "CLI 演示"],
    )
    assert created.exit_code == 0, created.output
    assert "已创建项目" in created.output
    assert (tmp_path / "cli_demo" / "project.json").is_file()

    status = runner.invoke(app, ["status", "cli_demo"])
    assert status.exit_code == 0, status.output
    assert "暂无任务" in status.output

    source = tmp_path / "book.txt"
    source.write_text("第1章 开始\n测试正文。", encoding="utf-8")
    imported = runner.invoke(
        app,
        ["import-novel", "cli_demo", str(source)],
    )
    assert imported.exit_code == 0, imported.output
    assert "标准章节: 1" in imported.output
    assert (
        tmp_path
        / "cli_demo"
        / "novel"
        / "chapters"
        / "ch_000001.json"
    ).is_file()
