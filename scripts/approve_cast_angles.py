"""Approve three character angles and publish project-local identity references."""

from __future__ import annotations

import argparse
import json
import shutil
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ANGLE_KEYS = ("front", "left_profile", "back")


def _read_json(path: Path, default: dict) -> dict:
    if not path.is_file():
        return default
    return json.loads(path.read_text(encoding="utf-8-sig"))


def run(args: argparse.Namespace) -> Path:
    workspace = args.workspace.resolve()
    project_root = workspace / "projects" / args.project
    source_dir = args.source_dir.resolve()
    selections = {
        "front": args.front,
        "left_profile": args.left_profile,
        "back": args.back,
    }
    paths = {angle: source_dir / filename for angle, filename in selections.items()}
    missing = [str(path) for path in paths.values() if not path.is_file()]
    if missing:
        raise FileNotFoundError("missing selected angle files: " + ", ".join(missing))

    publish_dir = (
        project_root
        / "production"
        / "cast"
        / args.character
        / (args.version or datetime.now().strftime("%Y%m%d_%H%M%S"))
    )
    publish_dir.mkdir(parents=True, exist_ok=False)

    approved: dict[str, str] = {}
    panels: list[Image.Image] = []
    for angle in ANGLE_KEYS:
        destination = publish_dir / f"{angle}.png"
        shutil.copy2(paths[angle], destination)
        approved[angle] = destination.relative_to(project_root).as_posix()
        with Image.open(destination) as image:
            panels.append(image.convert("RGB"))

    panel_width = max(image.width for image in panels)
    panel_height = max(image.height for image in panels)
    label_height = 48
    sheet = Image.new("RGB", (panel_width * len(panels), panel_height + label_height), "white")
    draw = ImageDraw.Draw(sheet)
    font = ImageFont.load_default()
    for index, (angle, image) in enumerate(zip(ANGLE_KEYS, panels, strict=True)):
        x = index * panel_width + (panel_width - image.width) // 2
        sheet.paste(image, (x, 0))
        draw.text(
            (index * panel_width + 16, panel_height + 16),
            angle,
            fill="black",
            font=font,
        )
    turnaround = publish_dir / "turnaround.png"
    sheet.save(turnaround, compress_level=4)
    identity_anchor = publish_dir / "identity_anchor.png"
    shutil.copy2(paths["front"], identity_anchor)

    approval = {
        "schema_version": "1.0",
        "character": args.character,
        "status": "approved",
        "source_dir": str(source_dir),
        "selected_angles": selections,
        "published_angles": approved,
        "turnaround": turnaround.relative_to(project_root).as_posix(),
        "identity_anchor": identity_anchor.relative_to(project_root).as_posix(),
        "approved_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    }
    (publish_dir / "approval.json").write_text(
        json.dumps(approval, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    cast_path = project_root / "production" / "cast_selection.json"
    cast = _read_json(cast_path, {"schema_version": "1.0", "selections": {}})
    selections_value = cast.setdefault("selections", {})
    selections_value[args.character] = approval["identity_anchor"]
    references = cast.setdefault("references", {})
    references[args.character] = {
        "turnaround": approval["turnaround"],
        "angles": approved,
        "approval": (publish_dir / "approval.json").relative_to(project_root).as_posix(),
    }
    cast_path.write_text(json.dumps(cast, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[OUTPUT_ROOT] {publish_dir}", flush=True)
    return publish_dir


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", default="jueshi")
    parser.add_argument("--character", required=True)
    parser.add_argument("--source-dir", type=Path, required=True)
    parser.add_argument("--front", required=True)
    parser.add_argument("--left-profile", required=True)
    parser.add_argument("--back", required=True)
    parser.add_argument("--version")
    parser.add_argument("--workspace", type=Path, default=Path.cwd())
    run(parser.parse_args())


if __name__ == "__main__":
    main()
