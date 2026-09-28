"""Safely preview or move legacy project data into an ignored project directory."""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path

DATA_DIRECTORIES = (
    "novel",
    "production",
    "assets",
    "outputs",
    "database",
    "cache",
)
DATA_FILES = ("project.json", "config.yaml", "characters.json", "state.json")


def migration_items(source: Path) -> list[Path]:
    names = (*DATA_DIRECTORIES, *DATA_FILES)
    return [source / name for name in names if (source / name).exists()]


def migrate(source: Path, destination: Path, *, apply: bool = False) -> list[tuple[Path, Path]]:
    source = source.resolve()
    destination = destination.resolve()
    if source == destination or source in destination.parents:
        raise ValueError("目标目录不能等于旧目录或位于旧目录内部")
    moves = [(item, destination / item.name) for item in migration_items(source)]
    collisions = [target for _, target in moves if target.exists()]
    if collisions:
        raise FileExistsError(f"目标已存在，拒绝覆盖: {collisions[0]}")
    if apply:
        destination.mkdir(parents=True, exist_ok=True)
        for item, target in moves:
            shutil.move(str(item), str(target))
    return moves


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    moves = migrate(args.source, args.destination, apply=args.apply)
    mode = "已移动" if args.apply else "预览"
    for source, destination in moves:
        print(f"{mode}: {source} -> {destination}")
    if not moves:
        print("没有发现可迁移的项目数据。")


if __name__ == "__main__":
    main()
