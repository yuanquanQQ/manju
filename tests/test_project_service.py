from __future__ import annotations

import sqlite3

import pytest

from app.database.db import migrate_database, restore_database
from app.services.project_service import (
    PROJECT_SUBDIRECTORIES,
    create_project,
    load_project_config,
    load_project_manifest,
    resolve_project_dir,
)


def test_create_project_is_complete_and_versioned(tmp_path):
    root = create_project(
        "demo_project",
        display_name="演示项目",
        projects_dir=tmp_path,
    )

    manifest = load_project_manifest(root)
    config = load_project_config(root)
    assert manifest.slug == "demo_project"
    assert manifest.display_name == "演示项目"
    assert manifest.schema_version == "1.0"
    assert config.schema_version == "1.0"
    assert all((root / item).is_dir() for item in PROJECT_SUBDIRECTORIES)

    connection = sqlite3.connect(root / "database" / "world.db")
    try:
        version = connection.execute(
            "SELECT value FROM schema_metadata WHERE key='schema_version'"
        ).fetchone()
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
    finally:
        connection.close()
    assert version == ("2",)
    assert {"jobs", "artifacts", "reviews", "settings_snapshots"} <= tables


@pytest.mark.parametrize("name", ["../escape", "a/b", "CON", ".", "has space"])
def test_project_name_rejects_unsafe_values(tmp_path, name):
    with pytest.raises(ValueError):
        resolve_project_dir(name, projects_dir=tmp_path)


def test_create_project_does_not_overwrite(tmp_path):
    create_project("demo", projects_dir=tmp_path)
    with pytest.raises(FileExistsError):
        create_project("demo", projects_dir=tmp_path)


def test_legacy_database_is_backed_up_before_migration(tmp_path):
    database = tmp_path / "world.db"
    connection = sqlite3.connect(database)
    try:
        connection.execute("CREATE TABLE legacy_data (id INTEGER PRIMARY KEY)")
        connection.execute("INSERT INTO legacy_data(id) VALUES (1)")
        connection.commit()
    finally:
        connection.close()

    migrate_database(database)

    backup = tmp_path / "world.db.pre-schema-v1.bak"
    assert backup.is_file()
    backup_connection = sqlite3.connect(backup)
    try:
        assert backup_connection.execute(
            "SELECT id FROM legacy_data"
        ).fetchall() == [(1,)]
    finally:
        backup_connection.close()


def test_versioned_database_is_backed_up_before_next_migration(tmp_path):
    database = tmp_path / "world.db"
    connection = sqlite3.connect(database)
    try:
        connection.execute(
            "CREATE TABLE schema_metadata "
            "(key TEXT PRIMARY KEY, value TEXT, updated_at DATETIME)"
        )
        connection.execute(
            "INSERT INTO schema_metadata(key, value) "
            "VALUES ('schema_version', '1')"
        )
        connection.execute("CREATE TABLE v1_data (value TEXT)")
        connection.execute("INSERT INTO v1_data(value) VALUES ('kept')")
        connection.commit()
    finally:
        connection.close()

    migrate_database(database)

    backup = tmp_path / "world.db.pre-schema-v2.bak"
    assert backup.is_file()
    backup_connection = sqlite3.connect(backup)
    try:
        assert backup_connection.execute(
            "SELECT value FROM schema_metadata WHERE key='schema_version'"
        ).fetchone() == ("1",)
        assert backup_connection.execute(
            "SELECT value FROM v1_data"
        ).fetchone() == ("kept",)
    finally:
        backup_connection.close()


def test_database_restore_keeps_safety_backup(tmp_path):
    database = tmp_path / "world.db"
    connection = sqlite3.connect(database)
    try:
        connection.execute("CREATE TABLE data (value TEXT)")
        connection.execute("INSERT INTO data(value) VALUES ('old')")
        connection.commit()
    finally:
        connection.close()
    migrate_database(database)
    original_backup = tmp_path / "world.db.pre-schema-v1.bak"

    connection = sqlite3.connect(database)
    try:
        connection.execute("UPDATE data SET value='new'")
        connection.commit()
    finally:
        connection.close()

    safety_backup = restore_database(database, original_backup)

    assert safety_backup.is_file()
    connection = sqlite3.connect(database)
    try:
        assert connection.execute("SELECT value FROM data").fetchone() == ("old",)
        assert connection.execute(
            "SELECT value FROM schema_metadata WHERE key='schema_version'"
        ).fetchone() == ("2",)
    finally:
        connection.close()
