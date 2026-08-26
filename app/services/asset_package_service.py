"""Build and expose clean local packages for reusable assets and deliverables."""

from __future__ import annotations

import json
import os
import re
import shutil
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from app.core.config import settings
from app.core.files import atomic_write_json, atomic_write_text
from app.core.naming import pinyin_slug
from app.services.voice_library_service import VoiceLibraryService

_STAMP_RE = re.compile(r"(20\d{6})[_-]?(\d{6})")


@dataclass(slots=True)
class ResourcePackage:
    key: str
    label: str
    path: Path
    description: str
    file_count: int = 0
    total_bytes: int = 0


@dataclass(slots=True)
class ResourcePackageState:
    packages: list[ResourcePackage] = field(default_factory=list)


@dataclass(slots=True)
class OrganizeResult:
    character_files: int = 0
    location_files: int = 0
    voice_files: int = 0
    episodes_organized: int = 0
    deliverable_files: int = 0


class AssetPackageService:
    """Create stable, ASCII-only user-facing views without deleting sources."""

    def __init__(self, projects_dir: Path | None = None) -> None:
        self.projects_dir = Path(projects_dir or settings.projects_dir).resolve()
        self.voice_library = VoiceLibraryService(self.projects_dir)

    def load_state(self, slug: str) -> ResourcePackageState:
        paths = self.ensure_structure(slug)
        definitions = (
            (
                "characters",
                "人物资源包",
                paths["characters"],
                "定妆照、角色参考图和人物清单",
            ),
            (
                "locations",
                "场景资源包",
                paths["locations"],
                "场景参考图、地点信息和首次出现镜头",
            ),
            (
                "voices",
                "人声资源包",
                paths["voices"],
                "授权参考音频、试听文件和声音清单",
            ),
            (
                "deliverables",
                "合成内容包",
                paths["deliverables"],
                "按集拆分的视频、字幕、音频、清单和质检文件",
            ),
        )
        packages: list[ResourcePackage] = []
        for key, label, path, description in definitions:
            files = [item for item in path.rglob("*") if item.is_file()]
            packages.append(
                ResourcePackage(
                    key=key,
                    label=label,
                    path=path,
                    description=description,
                    file_count=len(files),
                    total_bytes=sum(item.stat().st_size for item in files),
                )
            )
        return ResourcePackageState(packages)

    def ensure_structure(self, slug: str) -> dict[str, Path]:
        root = self._project_root(slug)
        paths = {
            "characters": root / "assets" / "characters",
            "locations": root / "assets" / "locations",
            "voices": root / "assets" / "voices",
            "deliverables": root / "outputs" / "episodes",
        }
        for path in paths.values():
            path.mkdir(parents=True, exist_ok=True)
        return paths

    def organize(self, slug: str) -> OrganizeResult:
        root = self._project_root(slug)
        paths = self.ensure_structure(slug)
        result = OrganizeResult()
        result.character_files = self._sync_characters(root, paths["characters"])
        result.location_files = self._sync_locations(root, paths["locations"])
        result.voice_files = self._sync_voices(slug, root, paths["voices"])
        for episode_path in sorted(
            (root / "production" / "episodes").glob("episode_*.json")
        ):
            copied = self._organize_episode(root, episode_path, paths["deliverables"])
            episode = self._read_json(episode_path)
            episode_number = int(episode.get("episode_number") or 0)
            archived = self._archive_legacy_deliverables(
                root,
                episode_number,
                paths["deliverables"],
            )
            if copied or archived:
                result.episodes_organized += 1
                result.deliverable_files += copied + archived
        self._write_root_guide(root, paths)
        return result

    def package_path(self, slug: str, key: str) -> Path:
        paths = self.ensure_structure(slug)
        if key not in paths:
            raise KeyError(f"未知资源包：{key}")
        return paths[key]

    def _sync_characters(self, root: Path, destination: Path) -> int:
        cast = self._read_json(root / "production" / "cast_selection.json")
        selections = cast.get("selections") if isinstance(cast, dict) else {}
        selections = selections if isinstance(selections, dict) else {}
        characters: dict[str, dict[str, str]] = {}
        for episode_path in sorted(
            (root / "production" / "episodes").glob("episode_*.json")
        ):
            episode = self._read_json(episode_path)
            for name, profile in (episode.get("character_profiles") or {}).items():
                characters.setdefault(
                    str(name),
                    {"name": str(name), "profile": str(profile), "file": ""},
                )
        copied = 0
        for name, configured in selections.items():
            source = self._safe_source(root, str(configured))
            if source is None:
                continue
            stem = f"{pinyin_slug(str(name), fallback='renwu')}_001"
            target = destination / f"{stem}{source.suffix.lower()}"
            self._link_or_copy(source, target)
            characters.setdefault(
                str(name),
                {"name": str(name), "profile": "", "file": ""},
            )["file"] = target.relative_to(root).as_posix()
            copied += 1
        atomic_write_json(
            destination / "renwu_qingdan.json",
            {
                "schema_version": "1.0",
                "updated_at": self._now(),
                "characters": list(characters.values()),
            },
        )
        return copied + 1

    def _archive_legacy_deliverables(
        self,
        root: Path,
        episode_number: int,
        deliverables_root: Path,
    ) -> int:
        legacy = (
            root
            / "production"
            / "videos"
            / f"episode_{episode_number:03d}"
        )
        if not legacy.is_dir():
            return 0
        package_name = f"{pinyin_slug(root.name)}_{episode_number:03d}"
        archive_root = deliverables_root / package_name / "guidang"
        rules = (
            ("*_dubbed_*.mp4", "shipin", "chengpian"),
            ("*_preview_*.mp4", "yulan", "yulan"),
            ("*_subtitles_*.srt", "zimu", "zimu"),
            ("*manifest*.json", "qingdan", "qingdan"),
            ("*.zip", "yasuo", "yasuo"),
            ("*.jpg", "zhijian", "zhijian"),
            ("*.png", "zhijian", "zhijian"),
        )
        copied = 0
        seen: set[Path] = set()
        sequence = 0
        for pattern, folder, kind in rules:
            for source in sorted(legacy.glob(pattern), key=lambda path: path.name):
                if source in seen or not source.is_file():
                    continue
                seen.add(source)
                sequence += 1
                stamp = self._stamp(source.name, None)
                destination = archive_root / folder / (
                    f"{package_name}_{sequence:03d}_{stamp}_{kind}"
                    f"{source.suffix.lower()}"
                )
                self._link_or_copy(source, destination)
                copied += 1
        return copied

    def _sync_locations(self, root: Path, destination: Path) -> int:
        locations: dict[str, dict[str, Any]] = {}
        for episode_path in sorted(
            (root / "production" / "episodes").glob("episode_*.json")
        ):
            episode = self._read_json(episode_path)
            episode_number = int(episode.get("episode_number") or 0)
            for index, shot in enumerate(episode.get("shots") or [], start=1):
                if not isinstance(shot, dict):
                    continue
                environment = shot.get("environment")
                environment = environment if isinstance(environment, dict) else {}
                layout = str(environment.get("layout") or "").strip()
                location = re.split(r"[；;]", layout, maxsplit=1)[0].strip()
                if not location:
                    location = f"changjing_{episode_number:03d}_{index:03d}"
                if location in locations:
                    continue
                source = (
                    root
                    / "production"
                    / "video_inputs"
                    / f"episode_{episode_number:03d}"
                    / f"shot_{int(shot.get('shot_number') or index):03d}.png"
                )
                target = destination / (
                    f"{pinyin_slug(location, fallback='changjing')}_001"
                    f"{source.suffix.lower()}"
                )
                file_value = ""
                if source.is_file():
                    self._link_or_copy(source, target)
                    file_value = target.relative_to(root).as_posix()
                locations[location] = {
                    "name": location,
                    "first_episode": episode_number,
                    "first_shot": int(shot.get("shot_number") or index),
                    "layout": layout,
                    "lighting": str(environment.get("lighting") or ""),
                    "atmosphere": str(environment.get("atmosphere") or ""),
                    "file": file_value,
                }
        atomic_write_json(
            destination / "changjing_qingdan.json",
            {
                "schema_version": "1.0",
                "updated_at": self._now(),
                "locations": list(locations.values()),
            },
        )
        return sum(bool(item["file"]) for item in locations.values()) + 1

    def _sync_voices(self, slug: str, root: Path, destination: Path) -> int:
        profiles = self.voice_library.load_profiles()
        assignments = self.voice_library.load_assignments(slug)
        assigned_to: dict[str, list[str]] = {}
        for character, assignment in assignments.items():
            assigned_to.setdefault(assignment.profile_id, []).append(character)
        preview_root = root / "production" / "audio" / "voice_previews"
        copied = 0
        values: list[dict[str, Any]] = []
        for profile in profiles:
            source = profile.reference_audio
            if source is None and preview_root.is_dir():
                candidates = sorted(
                    (
                        item
                        for item in preview_root.rglob(f"*{profile.profile_id}*")
                        if item.is_file()
                        and item.suffix.lower() in {".wav", ".mp3", ".flac"}
                        and item.stat().st_size > 0
                    ),
                    key=lambda item: item.stat().st_mtime,
                    reverse=True,
                )
                source = candidates[0] if candidates else None
            file_value = ""
            if source is not None and source.is_file():
                target = destination / (
                    f"{pinyin_slug(profile.name, fallback='rensheng')}_001"
                    f"{source.suffix.lower()}"
                )
                self._link_or_copy(source, target)
                file_value = target.relative_to(root).as_posix()
                copied += 1
            values.append(
                {
                    "profile_id": profile.profile_id,
                    "name": profile.name,
                    "engine": profile.engine,
                    "gender": profile.gender,
                    "age_group": profile.age_group,
                    "temperament": profile.temperament,
                    "assigned_characters": sorted(assigned_to.get(profile.profile_id, [])),
                    "source": profile.source_label,
                    "authorization": profile.authorization,
                    "file": file_value,
                }
            )
        atomic_write_json(
            destination / "rensheng_qingdan.json",
            {
                "schema_version": "1.0",
                "updated_at": self._now(),
                "voices": values,
            },
        )
        return copied + 1

    def _organize_episode(
        self,
        root: Path,
        episode_path: Path,
        deliverables_root: Path,
    ) -> int:
        episode = self._read_json(episode_path)
        episode_number = int(episode.get("episode_number") or 0)
        dubbing = episode.get("dubbing")
        dubbing = dict(dubbing) if isinstance(dubbing, dict) else {}
        source_video = self._safe_source(root, str(dubbing.get("output_file") or ""))
        if source_video is None:
            return 0
        package_name = f"{pinyin_slug(root.name)}_{episode_number:03d}"
        package_root = deliverables_root / package_name
        try:
            source_video.relative_to(package_root.resolve())
            return 0
        except ValueError:
            pass
        video_dir = package_root / "shipin"
        subtitle_dir = package_root / "zimu"
        line_subtitle_dir = subtitle_dir / "fenduan"
        audio_dir = package_root / "yinpin"
        manifest_dir = package_root / "qingdan"
        line_manifest_dir = manifest_dir / "fenduan"
        qc_dir = package_root / "zhijian"
        for path in (
            video_dir,
            subtitle_dir,
            line_subtitle_dir,
            audio_dir,
            manifest_dir,
            line_manifest_dir,
            qc_dir,
        ):
            path.mkdir(parents=True, exist_ok=True)

        version = len(list(video_dir.glob("*.mp4"))) + 1
        source_manifest = self._safe_source(
            root, str(dubbing.get("manifest_file") or "")
        )
        manifest = self._read_json(source_manifest) if source_manifest else {}
        stamp = self._stamp(source_video.name, manifest.get("generated_at"))
        prefix = f"{package_name}_{version:03d}_{stamp}"
        video_target = video_dir / f"{prefix}_chengpian.mp4"
        self._link_or_copy(source_video, video_target)
        copied = 1

        source_subtitle = self._safe_source(
            root, str(dubbing.get("subtitle_file") or "")
        )
        subtitle_target: Path | None = None
        if source_subtitle is not None:
            subtitle_target = subtitle_dir / f"{prefix}_zimu.srt"
            self._link_or_copy(source_subtitle, subtitle_target)
            copied += 1

        manifest_lines = {
            int(item.get("shot_number") or 0): item
            for item in manifest.get("lines") or []
            if isinstance(item, dict)
        }
        for index, shot in enumerate(episode.get("shots") or [], start=1):
            if not isinstance(shot, dict):
                continue
            shot_number = int(shot.get("shot_number") or index)
            audio = shot.get("audio_generation")
            audio = dict(audio) if isinstance(audio, dict) else {}
            speaker_slug = pinyin_slug(str(audio.get("speaker") or "pangbai"))
            line_prefix = (
                f"{speaker_slug}_{episode_number:03d}_{shot_number:03d}_"
                f"{version:03d}_{stamp}"
            )
            source_audio = self._safe_source(root, str(audio.get("audio_file") or ""))
            source_line_subtitle = self._safe_source(
                root, str(audio.get("subtitle_file") or "")
            )
            source_line_manifest = self._safe_source(
                root, str(audio.get("manifest_file") or "")
            )
            target_audio: Path | None = None
            target_line_subtitle: Path | None = None
            target_line_manifest: Path | None = None
            if source_audio is not None:
                target_audio = audio_dir / f"{line_prefix}{source_audio.suffix.lower()}"
                self._link_or_copy(source_audio, target_audio)
                copied += 1
            if source_line_subtitle is not None:
                target_line_subtitle = line_subtitle_dir / f"{line_prefix}.srt"
                self._link_or_copy(source_line_subtitle, target_line_subtitle)
                copied += 1
            if source_line_manifest is not None:
                target_line_manifest = line_manifest_dir / f"{line_prefix}.json"
                line_manifest = self._read_json(source_line_manifest)
                if target_audio is not None:
                    line_manifest["audio_file"] = target_audio.relative_to(root).as_posix()
                if target_line_subtitle is not None:
                    line_manifest["subtitle_file"] = target_line_subtitle.relative_to(root).as_posix()
                atomic_write_json(target_line_manifest, line_manifest)
                copied += 1
            if target_audio is not None:
                audio["audio_file"] = target_audio.relative_to(root).as_posix()
            if target_line_subtitle is not None:
                audio["subtitle_file"] = target_line_subtitle.relative_to(root).as_posix()
            if target_line_manifest is not None:
                audio["manifest_file"] = target_line_manifest.relative_to(root).as_posix()
            shot["audio_generation"] = audio
            line = manifest_lines.get(shot_number)
            if line is not None:
                if target_audio is not None:
                    line["audio_file"] = target_audio.relative_to(root).as_posix()
                if target_line_subtitle is not None:
                    line["subtitle_file"] = target_line_subtitle.relative_to(root).as_posix()

        manifest_target = manifest_dir / f"{prefix}_qingdan.json"
        manifest["output_file"] = video_target.relative_to(root).as_posix()
        manifest["subtitle_file"] = (
            subtitle_target.relative_to(root).as_posix()
            if subtitle_target is not None
            else ""
        )
        manifest["organized_at"] = self._now()
        atomic_write_json(manifest_target, manifest)
        copied += 1

        legacy_root = source_video.parent
        reviews = sorted(
            (
                path
                for path in legacy_root.glob("*")
                if path.is_file()
                and path.suffix.lower() in {".jpg", ".jpeg", ".png"}
            ),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        if reviews:
            review_target = qc_dir / f"{prefix}_zhijian{reviews[0].suffix.lower()}"
            self._link_or_copy(reviews[0], review_target)
            copied += 1

        episode["dubbing"] = {
            "output_file": video_target.relative_to(root).as_posix(),
            "subtitle_file": (
                subtitle_target.relative_to(root).as_posix()
                if subtitle_target is not None
                else ""
            ),
            "manifest_file": manifest_target.relative_to(root).as_posix(),
        }
        atomic_write_json(episode_path, episode)
        atomic_write_text(
            package_root / "shuoming.txt",
            (
                f"项目：{root.name}\n"
                f"剧集：{episode_number:03d}\n"
                f"版本：{version:03d}\n"
                f"整理时间：{self._now()}\n\n"
                "shipin：最终 MP4\n"
                "zimu：整集与分段 SRT\n"
                "yinpin：逐镜头配音\n"
                "qingdan：整集与分段生成清单\n"
                "zhijian：质检抽帧\n"
            ),
        )
        return copied + 1

    def _write_root_guide(self, root: Path, paths: dict[str, Path]) -> None:
        atomic_write_text(
            root / "outputs" / "ziyuan_bao_shuoming.txt",
            (
                "本目录由桌面应用自动整理。文件名仅使用拼音、数字和下划线。\n\n"
                f"人物资源：{paths['characters'].relative_to(root).as_posix()}\n"
                f"场景资源：{paths['locations'].relative_to(root).as_posix()}\n"
                f"人声资源：{paths['voices'].relative_to(root).as_posix()}\n"
                f"合成内容：{paths['deliverables'].relative_to(root).as_posix()}\n"
            ),
        )

    def _project_root(self, slug: str) -> Path:
        root = (self.projects_dir / slug).resolve()
        try:
            root.relative_to(self.projects_dir)
        except ValueError as exc:
            raise ValueError("项目路径无效。") from exc
        if not (root / "project.json").is_file():
            raise FileNotFoundError(f"项目不存在：{slug}")
        return root

    @staticmethod
    def _safe_source(root: Path, configured: str) -> Path | None:
        if not configured:
            return None
        candidate = (root / configured).resolve()
        try:
            candidate.relative_to(root.resolve())
        except ValueError:
            return None
        return candidate if candidate.is_file() else None

    @staticmethod
    def _link_or_copy(source: Path, destination: Path) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.is_file():
            if destination.stat().st_size == source.stat().st_size:
                return
            destination.unlink()
        try:
            os.link(source, destination)
        except OSError:
            shutil.copy2(source, destination)

    @staticmethod
    def _read_json(path: Path | None) -> dict[str, Any]:
        if path is None or not path.is_file():
            return {}
        try:
            value = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            return {}
        return value if isinstance(value, dict) else {}

    @staticmethod
    def _stamp(filename: str, generated_at: Any) -> str:
        match = _STAMP_RE.search(filename)
        if match:
            return f"{match.group(1)}_{match.group(2)}"
        if generated_at:
            try:
                return datetime.fromisoformat(str(generated_at)).strftime(
                    "%Y%m%d_%H%M%S"
                )
            except ValueError:
                pass
        return datetime.now().strftime("%Y%m%d_%H%M%S")

    @staticmethod
    def _now() -> str:
        return datetime.now().astimezone().isoformat(timespec="seconds")
