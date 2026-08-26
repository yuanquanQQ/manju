"""数据库初始化与 Session 工厂。"""
from __future__ import annotations

import shutil
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings
from app.core.logger import logger
from app.database.migrations import CURRENT_SCHEMA_VERSION, migrate_engine


def make_engine(db_path: str | Path) -> Engine:
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    url = f"sqlite:///{db_path}"
    engine = create_engine(
        url,
        echo=False,
        future=True,
        connect_args={"timeout": settings.sqlite_busy_timeout_ms / 1000},
    )

    @event.listens_for(engine, "connect")
    def _configure_sqlite(dbapi_connection, _connection_record) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute(f"PRAGMA busy_timeout={settings.sqlite_busy_timeout_ms}")
        cursor.close()

    return engine


_engine = None
_SessionLocal: sessionmaker[Session] | None = None


def _sqlite_backup(source_path: Path, destination_path: Path) -> None:
    source = sqlite3.connect(source_path)
    destination = sqlite3.connect(destination_path)
    try:
        source.backup(destination)
    finally:
        destination.close()
        source.close()


def _sqlite_integrity(path: Path) -> str:
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        return str(connection.execute("PRAGMA integrity_check").fetchone()[0])
    finally:
        connection.close()


def _database_schema_version(db_path: Path) -> int | None:
    if not db_path.exists() or db_path.stat().st_size == 0:
        return None
    connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        if not tables:
            return None
        if "schema_metadata" not in tables:
            return 0
        row = connection.execute(
            "SELECT value FROM schema_metadata WHERE key='schema_version'"
        ).fetchone()
        return int(row[0]) if row else 0
    finally:
        connection.close()


def _backup_before_migration(db_path: Path) -> Path | None:
    version = _database_schema_version(db_path)
    if version is None or version >= CURRENT_SCHEMA_VERSION:
        return None
    next_version = version + 1
    backup = db_path.with_name(
        f"{db_path.name}.pre-schema-v{next_version}.bak"
    )
    if not backup.exists():
        temp_backup = backup.with_suffix(backup.suffix + ".tmp")
        _sqlite_backup(db_path, temp_backup)
        temp_backup.replace(backup)
        shutil.copystat(db_path, backup)
        logger.info(f"迁移前备份数据库: {backup}")
    return backup


def migrate_database(db_path: str | Path) -> int:
    """迁移指定数据库并立即释放连接，适合项目创建/升级。"""
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    _backup_before_migration(path)
    engine = make_engine(path)
    try:
        return migrate_engine(engine)
    finally:
        engine.dispose()


def restore_database(
    db_path: str | Path,
    backup_path: str | Path,
) -> Path:
    """恢复数据库；恢复前保留当前库，并在完成后升级到当前 schema。"""
    global _engine, _SessionLocal
    path = Path(db_path).resolve()
    backup = Path(backup_path).resolve()
    if not backup.is_file():
        raise FileNotFoundError(f"数据库备份不存在: {backup}")
    if backup.parent != path.parent or backup == path:
        raise ValueError("数据库备份必须位于目标 database 目录内")
    integrity = _sqlite_integrity(backup)
    if integrity != "ok":
        raise ValueError(f"数据库备份完整性检查失败: {integrity}")

    if _engine is not None:
        _engine.dispose()
        _engine = None
        _SessionLocal = None

    timestamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    safety_backup = path.with_name(f"{path.name}.before-restore-{timestamp}.bak")
    if path.exists():
        _sqlite_backup(path, safety_backup)

    temporary = path.with_name(f".{path.name}.restore-{uuid4().hex}.tmp")
    try:
        _sqlite_backup(backup, temporary)
        if _sqlite_integrity(temporary) != "ok":
            raise ValueError("恢复后的临时数据库完整性检查失败")
        for suffix in ("-wal", "-shm"):
            sidecar = Path(f"{path}{suffix}")
            sidecar.unlink(missing_ok=True)
        temporary.replace(path)
        migrate_database(path)
    finally:
        temporary.unlink(missing_ok=True)
    logger.info(f"数据库已从备份恢复: {backup}")
    return safety_backup


def init_db(db_path: str | Path) -> sessionmaker[Session]:
    """迁移数据库并创建当前进程使用的全局 Session 工厂。"""
    global _engine, _SessionLocal
    path = Path(db_path)
    _backup_before_migration(path)
    if _engine is not None:
        _engine.dispose()
    _engine = make_engine(path)
    version = migrate_engine(_engine)
    _SessionLocal = sessionmaker(
        bind=_engine,
        expire_on_commit=False,
        autoflush=False,
    )
    logger.info(f"SQLite 初始化完成: {path} (schema v{version})")
    return _SessionLocal


def get_session() -> Session:
    if _SessionLocal is None:
        raise RuntimeError("DB 未初始化，请先调用 init_db()")
    return _SessionLocal()


@contextmanager
def session_scope():
    s = get_session()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()
