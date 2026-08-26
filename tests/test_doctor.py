from __future__ import annotations

from app.core.doctor import run_diagnostics
from app.database.db import migrate_database


def test_doctor_checks_project_database_without_llm(tmp_path):
    project = tmp_path / "project"
    migrate_database(project / "database" / "world.db")

    checks = run_diagnostics(project_root=project, check_llm=False)
    by_name = {check.name: check for check in checks}

    assert by_name["Python"].status == "PASS"
    assert by_name["SQLite"].status == "PASS"
    assert by_name["项目数据库"].status == "PASS"
    assert "schema v2" in by_name["项目数据库"].message
