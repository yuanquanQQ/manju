"""轻量数据库迁移入口。

后续升级在 MIGRATIONS 中追加显式函数，禁止通过删除 world.db 完成结构升级。
"""
from __future__ import annotations

from collections.abc import Callable

from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from app.database.models import Base, SchemaMetadata

CURRENT_SCHEMA_VERSION = 2
Migration = Callable[[Engine], None]


def _migration_1(engine: Engine) -> None:
    """建立原型表和阶段 01 的任务/产物表。"""
    Base.metadata.create_all(engine)


def _migration_2(engine: Engine) -> None:
    """建立标准小说、分析版本、证据事实与实体解析表。"""
    Base.metadata.create_all(engine)


MIGRATIONS: dict[int, Migration] = {
    1: _migration_1,
    2: _migration_2,
}


def _read_version(engine: Engine) -> int:
    # 先建元数据表，才能读取旧库版本。
    SchemaMetadata.__table__.create(engine, checkfirst=True)
    with Session(engine) as session:
        row = session.get(SchemaMetadata, "schema_version")
        return int(row.value) if row else 0


def _write_version(engine: Engine, version: int) -> None:
    with Session(engine) as session, session.begin():
        row = session.get(SchemaMetadata, "schema_version")
        if row is None:
            session.add(
                SchemaMetadata(key="schema_version", value=str(version))
            )
        else:
            row.value = str(version)


def migrate_engine(engine: Engine) -> int:
    version = _read_version(engine)
    if version > CURRENT_SCHEMA_VERSION:
        raise RuntimeError(
            f"数据库版本 {version} 高于程序支持版本 {CURRENT_SCHEMA_VERSION}"
        )
    for target in range(version + 1, CURRENT_SCHEMA_VERSION + 1):
        migration = MIGRATIONS.get(target)
        if migration is None:
            raise RuntimeError(f"缺少数据库迁移: {target}")
        migration(engine)
        _write_version(engine, target)

    # create_all 不修改已有列，但可确保当前版本声明的新增表完整存在。
    Base.metadata.create_all(engine)
    return CURRENT_SCHEMA_VERSION


def get_schema_version(engine: Engine) -> int:
    with Session(engine) as session:
        value = session.scalar(
            select(SchemaMetadata.value).where(
                SchemaMetadata.key == "schema_version"
            )
        )
    return int(value or 0)
