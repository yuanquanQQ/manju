"""Shared consent-aware voice library and per-project cast assignment."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from app.core.config import settings
from app.core.files import atomic_write_json

SUPPORTED_AUDIO_SUFFIXES = {".wav", ".mp3", ".flac", ".m4a", ".ogg"}
AUTHORIZED_SOURCES = {"self", "licensed", "synthetic"}


@dataclass(slots=True)
class VoiceProfile:
    profile_id: str
    name: str
    engine: str = "cosyvoice"
    reference_audio: Path | None = None
    reference_text: str = ""
    edge_voice_id: str = "zh-CN-YunyangNeural"
    gender: str = "中性"
    age_group: str = "青年"
    temperament: str = "沉稳"
    pitch: str = "中"
    pace: str = "中"
    speech_rate: str = "+0%"
    speech_volume: str = "+0%"
    speech_pitch: str = "-5Hz"
    tags: list[str] = field(default_factory=list)
    default_instruction: str = "自然、克制、像真人表演，避免播音腔"
    source_label: str = ""
    authorization: str = "synthetic"
    consent_note: str = ""
    created_at: str = ""
    builtin: bool = False


@dataclass(slots=True)
class CharacterVoiceTraits:
    character: str
    gender: str = "中性"
    age_group: str = "青年"
    temperament: str = "沉稳"
    role: str = "配角"
    pitch: str = "中"
    pace: str = "中"
    evidence: str = ""
    dialogue_count: int = 0

    def summary(self) -> str:
        return " · ".join(
            (self.gender, self.age_group, self.temperament, self.role)
        )


@dataclass(slots=True)
class VoiceAssignment:
    character: str
    profile_id: str
    mode: str = "auto"
    confidence: float = 0.0
    reasons: list[str] = field(default_factory=list)
    updated_at: str = ""


@dataclass(slots=True)
class VoiceLibraryState:
    profiles: list[VoiceProfile]
    traits: list[CharacterVoiceTraits]
    assignments: dict[str, VoiceAssignment]


@dataclass(slots=True)
class VoiceApplyResult:
    episodes_updated: int = 0
    shots_updated: int = 0
    lip_sync_reset_shots: list[tuple[int, int]] = field(default_factory=list)


class VoiceLibraryService:
    """Manage reusable voices and deterministic character-to-voice matching."""

    schema_version = "1.1"

    def __init__(self, projects_dir: Path | None = None) -> None:
        self.projects_dir = Path(projects_dir or settings.projects_dir).resolve()
        self.library_root = self.projects_dir / "_voice_library"
        self.library_path = self.library_root / "library.json"
        self.references_dir = self.library_root / "references"

    def load_state(self, slug: str) -> VoiceLibraryState:
        profiles = self.load_profiles()
        traits = self.infer_character_traits(slug)
        assignments = self.load_assignments(slug)
        return VoiceLibraryState(profiles, traits, assignments)

    def load_profiles(self) -> list[VoiceProfile]:
        value = self._read_json(self.library_path)
        raw_profiles = value.get("profiles") if isinstance(value, dict) else None
        profiles = [
            self._profile_from_json(item)
            for item in raw_profiles or []
            if isinstance(item, dict)
        ]
        changed = False
        for default in self._default_profiles():
            existing = next(
                (
                    item
                    for item in profiles
                    if item.profile_id == default.profile_id
                ),
                None,
            )
            if existing is None:
                profiles.append(default)
                changed = True
            elif existing.builtin and existing != default:
                profiles[profiles.index(existing)] = default
                changed = True
        if changed or not self.library_path.is_file():
            self._save_profiles(profiles)
        return sorted(profiles, key=lambda item: (not item.builtin, item.name))

    def add_cloned_voice(
        self,
        *,
        name: str,
        source_audio: Path,
        reference_text: str,
        gender: str,
        age_group: str,
        temperament: str,
        pitch: str,
        pace: str,
        tags: list[str] | None = None,
        default_instruction: str = "自然、克制、像真人表演，避免播音腔",
        source_label: str = "",
        authorization: str,
        consent_note: str = "",
    ) -> VoiceProfile:
        clean_name = name.strip()
        if not clean_name:
            raise ValueError("请输入声音名称。")
        audio = Path(source_audio).resolve()
        if not audio.is_file() or audio.suffix.lower() not in SUPPORTED_AUDIO_SUFFIXES:
            raise ValueError("参考音频不存在或格式不受支持。")
        if audio.stat().st_size < 1024:
            raise ValueError("参考音频文件过小，无法作为克隆样本。")
        if not reference_text.strip():
            raise ValueError("请填写与参考音频逐字一致的台词。")
        if authorization not in AUTHORIZED_SOURCES:
            raise ValueError("必须确认声音来自本人、已获授权或合成音色。")

        profile_id = f"voice_{uuid4().hex[:12]}"
        self.references_dir.mkdir(parents=True, exist_ok=True)
        destination = self.references_dir / f"{profile_id}{audio.suffix.lower()}"
        shutil.copy2(audio, destination)
        profile = VoiceProfile(
            profile_id=profile_id,
            name=clean_name,
            engine="cosyvoice",
            reference_audio=destination,
            reference_text=reference_text.strip(),
            gender=gender,
            age_group=age_group,
            temperament=temperament,
            pitch=pitch,
            pace=pace,
            tags=sorted({item.strip() for item in tags or [] if item.strip()}),
            default_instruction=default_instruction.strip(),
            source_label=source_label.strip() or audio.name,
            authorization=authorization,
            consent_note=consent_note.strip(),
            created_at=datetime.now().astimezone().isoformat(timespec="seconds"),
        )
        profiles = self.load_profiles()
        profiles.append(profile)
        self._save_profiles(profiles)
        return profile

    def delete_profile(self, profile_id: str) -> None:
        profiles = self.load_profiles()
        target = next(
            (item for item in profiles if item.profile_id == profile_id),
            None,
        )
        if target is None:
            raise KeyError(f"声音不存在：{profile_id}")
        if target.builtin:
            raise ValueError("内置 Edge 音色不能删除。")
        kept = [item for item in profiles if item.profile_id != profile_id]
        self._save_profiles(kept)
        if target.reference_audio is not None:
            target.reference_audio.unlink(missing_ok=True)

    def load_assignments(self, slug: str) -> dict[str, VoiceAssignment]:
        value = self._read_json(self._assignment_path(slug))
        raw = value.get("assignments") if isinstance(value, dict) else None
        result: dict[str, VoiceAssignment] = {}
        for character, item in (raw or {}).items():
            if not isinstance(item, dict):
                continue
            result[str(character)] = VoiceAssignment(
                character=str(character),
                profile_id=str(item.get("profile_id") or ""),
                mode=str(item.get("mode") or "auto"),
                confidence=float(item.get("confidence") or 0.0),
                reasons=[str(value) for value in item.get("reasons") or []],
                updated_at=str(item.get("updated_at") or ""),
            )
        return result

    def save_manual_assignments(
        self,
        slug: str,
        selections: dict[str, str],
    ) -> dict[str, VoiceAssignment]:
        valid_ids = {item.profile_id for item in self.load_profiles()}
        assignments = self.load_assignments(slug)
        now = datetime.now().astimezone().isoformat(timespec="seconds")
        for character, profile_id in selections.items():
            if not profile_id:
                assignments.pop(character, None)
                continue
            if profile_id not in valid_ids:
                raise ValueError(f"人物“{character}”选择了不存在的声音。")
            assignments[character] = VoiceAssignment(
                character=character,
                profile_id=profile_id,
                mode="manual",
                confidence=1.0,
                reasons=["用户手动指定"],
                updated_at=now,
            )
        self._save_assignments(slug, assignments)
        return assignments

    def auto_match(
        self,
        slug: str,
        *,
        preserve_manual: bool = True,
    ) -> dict[str, VoiceAssignment]:
        profiles = self.load_profiles()
        traits = self.infer_character_traits(slug)
        current = self.load_assignments(slug)
        now = datetime.now().astimezone().isoformat(timespec="seconds")
        used_profiles: dict[str, int] = {}
        if preserve_manual:
            for assignment in current.values():
                if assignment.mode == "manual" and assignment.profile_id:
                    used_profiles[assignment.profile_id] = (
                        used_profiles.get(assignment.profile_id, 0) + 1
                    )
        for trait in traits:
            existing = current.get(trait.character)
            if preserve_manual and existing and existing.mode == "manual":
                continue
            ranked = sorted(
                (
                    (
                        self._match_score(trait, profile),
                        used_profiles.get(profile.profile_id, 0),
                        profile,
                    )
                    for profile in profiles
                ),
                key=lambda item: item[0][0] - item[1] * 24,
                reverse=True,
            )
            if not ranked:
                continue
            (base_score, reasons), reused, profile = ranked[0]
            score = base_score - reused * 24
            if reused == 0 and used_profiles:
                reasons = [*reasons, "避免与其他人物重复音色"]
            current[trait.character] = VoiceAssignment(
                character=trait.character,
                profile_id=profile.profile_id,
                mode="auto",
                confidence=max(0.0, min(score / 100.0, 1.0)),
                reasons=reasons,
                updated_at=now,
            )
            used_profiles[profile.profile_id] = reused + 1
        self._save_assignments(slug, current)
        return current

    def infer_character_traits(self, slug: str) -> list[CharacterVoiceTraits]:
        root = self._project_root(slug)
        evidence: dict[str, list[str]] = {}
        dialogue_counts: dict[str, int] = {}
        first_seen: list[str] = []
        episodes_dir = root / "production" / "episodes"
        for path in sorted(episodes_dir.glob("episode_*.json")):
            episode = self._read_json(path)
            profiles = episode.get("character_profiles")
            if isinstance(profiles, dict):
                for name, detail in profiles.items():
                    self._append_evidence(evidence, first_seen, str(name), str(detail))
            for shot in episode.get("shots") or []:
                if not isinstance(shot, dict):
                    continue
                for character in shot.get("characters") or []:
                    if not isinstance(character, dict):
                        continue
                    name = str(character.get("name") or "").strip()
                    detail = " ".join(
                        str(character.get(key) or "")
                        for key in ("appearance", "clothing", "expression")
                    )
                    self._append_evidence(evidence, first_seen, name, detail)
                audio = shot.get("audio_generation")
                if not isinstance(audio, dict):
                    audio = {}
                speaker = str(audio.get("speaker") or "").strip()
                if speaker:
                    self._append_evidence(evidence, first_seen, speaker, "")
                    dialogue_counts[speaker] = dialogue_counts.get(speaker, 0) + 1

        if "旁白" not in evidence:
            self._append_evidence(evidence, first_seen, "旁白", "旁白 解说")
        main_character = max(
            (name for name in first_seen if name != "旁白"),
            key=lambda name: dialogue_counts.get(name, 0),
            default="",
        )
        result: list[CharacterVoiceTraits] = []
        for name in first_seen:
            text = " ".join(evidence.get(name, []))
            result.append(
                self._traits_from_text(
                    name,
                    text,
                    dialogue_counts.get(name, 0),
                    main_character,
                )
            )
        return result

    def apply_assignments(self, slug: str) -> VoiceApplyResult:
        root = self._project_root(slug)
        profiles = {item.profile_id: item for item in self.load_profiles()}
        assignments = self.load_assignments(slug)
        result = VoiceApplyResult()
        references = root / "production" / "voice_library"
        references.mkdir(parents=True, exist_ok=True)
        for episode_path in sorted(
            (root / "production" / "episodes").glob("episode_*.json")
        ):
            episode = self._read_json(episode_path)
            changed = False
            episode_number = int(episode.get("episode_number") or 0)
            for index, shot in enumerate(episode.get("shots") or [], start=1):
                if not isinstance(shot, dict):
                    continue
                audio = shot.get("audio_generation")
                audio = dict(audio) if isinstance(audio, dict) else {}
                speaker = str(audio.get("speaker") or "").strip()
                assignment = assignments.get(speaker)
                profile = profiles.get(assignment.profile_id) if assignment else None
                if profile is None:
                    continue
                reference_value = ""
                if profile.reference_audio is not None:
                    suffix = profile.reference_audio.suffix.lower()
                    project_reference = references / f"{profile.profile_id}{suffix}"
                    if (
                        not project_reference.is_file()
                        or self._file_hash(project_reference)
                        != self._file_hash(profile.reference_audio)
                    ):
                        shutil.copy2(profile.reference_audio, project_reference)
                    reference_value = project_reference.relative_to(root).as_posix()
                previous_signature = (
                    str(audio.get("engine") or ""),
                    str(audio.get("voice_id") or ""),
                    str(audio.get("reference_audio") or ""),
                    str(audio.get("reference_text") or ""),
                    str(audio.get("instruct_text") or ""),
                    str(audio.get("rate") or ""),
                    str(audio.get("volume") or ""),
                    str(audio.get("pitch") or ""),
                )
                audio.update(
                    {
                        "engine": profile.engine,
                        "voice_id": profile.edge_voice_id,
                        "reference_audio": reference_value,
                        "reference_text": profile.reference_text,
                        "instruct_text": profile.default_instruction,
                        "rate": profile.speech_rate,
                        "volume": profile.speech_volume,
                        "pitch": profile.speech_pitch,
                        "fallback_to_edge": True,
                        "voice_profile_id": profile.profile_id,
                        "voice_assignment_mode": assignment.mode,
                        "audio_file": "",
                        "subtitle_file": "",
                        "manifest_file": "",
                    }
                )
                new_signature = (
                    audio["engine"],
                    audio["voice_id"],
                    audio["reference_audio"],
                    audio["reference_text"],
                    audio["instruct_text"],
                    audio["rate"],
                    audio["volume"],
                    audio["pitch"],
                )
                if new_signature == previous_signature:
                    continue
                shot["audio_generation"] = audio
                changed = True
                result.shots_updated += 1
                lip_sync = shot.get("lip_sync")
                lip_sync = dict(lip_sync) if isinstance(lip_sync, dict) else {}
                if bool(lip_sync.get("enabled", False)):
                    clean_source = str(lip_sync.get("source_video") or "")
                    old_output = str(lip_sync.get("output_file") or "")
                    if old_output:
                        lip_sync["previous_output_file"] = old_output
                    lip_sync.update(
                        {
                            "status": "pending",
                            "output_file": "",
                            "audio_file": "",
                            "manifest_file": "",
                            "error": "声音分配已变化，需要重新生成口型。",
                        }
                    )
                    shot["lip_sync"] = lip_sync
                    if clean_source:
                        video = shot.get("video_generation")
                        video = dict(video) if isinstance(video, dict) else {}
                        video["selected_video"] = clean_source
                        shot["video_generation"] = video
                    result.lip_sync_reset_shots.append(
                        (episode_number, int(shot.get("shot_number") or index))
                    )
            if changed:
                atomic_write_json(episode_path, episode)
                result.episodes_updated += 1
        return result

    def _save_profiles(self, profiles: list[VoiceProfile]) -> None:
        self.library_root.mkdir(parents=True, exist_ok=True)
        payload = []
        for profile in profiles:
            item = asdict(profile)
            reference = profile.reference_audio
            item["reference_audio"] = (
                reference.relative_to(self.library_root).as_posix()
                if reference is not None
                else ""
            )
            payload.append(item)
        atomic_write_json(
            self.library_path,
            {"schema_version": self.schema_version, "profiles": payload},
        )

    def _save_assignments(
        self,
        slug: str,
        assignments: dict[str, VoiceAssignment],
    ) -> None:
        atomic_write_json(
            self._assignment_path(slug),
            {
                "schema_version": self.schema_version,
                "assignments": {
                    character: asdict(item)
                    for character, item in assignments.items()
                },
            },
        )

    def _profile_from_json(self, item: dict[str, Any]) -> VoiceProfile:
        configured = str(item.get("reference_audio") or "")
        reference = (self.library_root / configured).resolve() if configured else None
        if reference is not None:
            try:
                reference.relative_to(self.library_root.resolve())
            except ValueError:
                reference = None
        return VoiceProfile(
            profile_id=str(item.get("profile_id") or ""),
            name=str(item.get("name") or "未命名声音"),
            engine=str(item.get("engine") or "cosyvoice"),
            reference_audio=reference if reference and reference.is_file() else None,
            reference_text=str(item.get("reference_text") or ""),
            edge_voice_id=str(item.get("edge_voice_id") or "zh-CN-YunyangNeural"),
            gender=str(item.get("gender") or "中性"),
            age_group=str(item.get("age_group") or "青年"),
            temperament=str(item.get("temperament") or "沉稳"),
            pitch=str(item.get("pitch") or "中"),
            pace=str(item.get("pace") or "中"),
            speech_rate=str(item.get("speech_rate") or "+0%"),
            speech_volume=str(item.get("speech_volume") or "+0%"),
            speech_pitch=str(item.get("speech_pitch") or "-5Hz"),
            tags=[str(value) for value in item.get("tags") or []],
            default_instruction=str(item.get("default_instruction") or ""),
            source_label=str(item.get("source_label") or ""),
            authorization=str(item.get("authorization") or "synthetic"),
            consent_note=str(item.get("consent_note") or ""),
            created_at=str(item.get("created_at") or ""),
            builtin=bool(item.get("builtin", False)),
        )

    @staticmethod
    def _match_score(
        traits: CharacterVoiceTraits,
        profile: VoiceProfile,
    ) -> tuple[float, list[str]]:
        score = 20.0
        reasons: list[str] = []
        if traits.gender == profile.gender:
            score += 28
            reasons.append("性别气质一致")
        elif "中性" in {traits.gender, profile.gender}:
            score += 8
        else:
            score -= 25
        if traits.age_group == profile.age_group:
            score += 20
            reasons.append("年龄感一致")
        elif {traits.age_group, profile.age_group} <= {"少年", "青年"}:
            score += 10
        else:
            score -= 5
        if traits.temperament == profile.temperament:
            score += 18
            reasons.append("性格表演匹配")
        if traits.pitch == profile.pitch:
            score += 8
            reasons.append("音高匹配")
        if traits.pace == profile.pace:
            score += 6
        if traits.role == "旁白" and "旁白" in profile.tags:
            score += 25
            reasons.append("旁白专用音色")
        if traits.role == "主角" and "主角" in profile.tags:
            score += 12
            reasons.append("主角音色")
        if traits.role == "反派" and "反派" in profile.tags:
            score += 18
            reasons.append("反派音色")
        if not reasons:
            reasons.append("使用最接近的可用音色")
        return score, reasons

    @staticmethod
    def _traits_from_text(
        name: str,
        text: str,
        dialogue_count: int,
        main_character: str,
    ) -> CharacterVoiceTraits:
        lowered = f"{name} {text}".lower()
        if name == "旁白":
            return CharacterVoiceTraits(
                character=name,
                gender="中性",
                age_group="中年",
                temperament="沉稳",
                role="旁白",
                pitch="中低",
                pace="中",
                evidence="旁白/解说",
                dialogue_count=dialogue_count,
            )
        female = bool(
            re.search(
                r"\b(female|woman|women|girl|heroine|swordswoman|princess)\b",
                lowered,
            )
        ) or any(
            marker in lowered
            for marker in ("女子", "少女", "美女", "小姐", "公主", "母亲")
        )
        male = bool(
            re.search(
                r"\b(male|man|men|boy|hero|nobleman|pretty-boy)\b",
                lowered,
            )
        ) or any(
            marker in lowered
            for marker in (
                "young guard",
                "young warrior",
                "男子",
                "少年",
                "公子",
                "少爷",
                "护卫",
                "父亲",
            )
        )
        gender = "女声" if female and not male else "男声" if male else "中性"
        age_group = "青年"
        age_match = re.search(r"\b(\d{1,2})\s*[- ]?year", lowered)
        if any(marker in lowered for marker in ("child", "孩童", "儿童", "幼年")):
            age_group = "儿童"
        elif any(marker in lowered for marker in ("teen", "少年", "少女")):
            age_group = "少年"
        elif age_match:
            age = int(age_match.group(1))
            age_group = "少年" if age < 18 else "青年" if age < 35 else "中年" if age < 55 else "老年"
        elif any(marker in lowered for marker in ("middle-aged", "中年", "大叔")):
            age_group = "中年"
        elif any(marker in lowered for marker in ("elder", "old man", "老者", "老年")):
            age_group = "老年"
        temperament = "沉稳"
        groups = (
            ("冷峻", ("冷峻", "冷漠", "克制", "cold", "stern", "aloof")),
            ("温柔", ("温柔", "温和", "柔和", "gentle", "serene")),
            ("威严", ("威严", "霸气", "强势", "commanding", "powerful", "arrogant", "proud")),
            ("活泼", ("活泼", "开朗", "俏皮", "lively", "playful")),
            ("阴沉", ("阴沉", "反派", "狡猾", "sinister", "villain")),
            ("热血", ("热血", "忠诚", "英勇", "heroic", "loyal")),
        )
        for label, markers in groups:
            if any(marker in lowered for marker in markers):
                temperament = label
                break
        role = "主角" if name == main_character else "配角"
        if any(
            marker in lowered
            for marker in ("反派", "villain", "敌人", "rival", "antagonist")
        ):
            role = "反派"
        pitch = "中"
        if age_group in {"儿童", "少年"}:
            pitch = "中高"
        elif age_group in {"中年", "老年"} or temperament in {"威严", "阴沉"}:
            pitch = "中低"
        pace = "快" if temperament == "活泼" else "慢" if temperament in {"冷峻", "威严"} else "中"
        evidence_text = re.sub(r"\s+", " ", text).strip()[:180]
        return CharacterVoiceTraits(
            character=name,
            gender=gender,
            age_group=age_group,
            temperament=temperament,
            role=role,
            pitch=pitch,
            pace=pace,
            evidence=evidence_text,
            dialogue_count=dialogue_count,
        )

    @staticmethod
    def _append_evidence(
        evidence: dict[str, list[str]],
        first_seen: list[str],
        name: str,
        detail: str,
    ) -> None:
        clean = name.strip()
        if not clean:
            return
        if clean not in evidence:
            evidence[clean] = []
            first_seen.append(clean)
        if detail.strip():
            evidence[clean].append(detail.strip())

    def _project_root(self, slug: str) -> Path:
        root = (self.projects_dir / slug).resolve()
        try:
            root.relative_to(self.projects_dir)
        except ValueError as exc:
            raise ValueError("项目路径无效。") from exc
        if not (root / "project.json").is_file():
            raise FileNotFoundError(f"项目不存在：{slug}")
        return root

    def _assignment_path(self, slug: str) -> Path:
        return self._project_root(slug) / "production" / "voice_assignments.json"

    @staticmethod
    def _read_json(path: Path) -> dict[str, Any]:
        if not path.is_file():
            return {}
        try:
            value = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            return {}
        return value if isinstance(value, dict) else {}

    @staticmethod
    def _file_hash(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    @staticmethod
    def _default_profiles() -> list[VoiceProfile]:
        created = "builtin"
        common = {
            "engine": "edge_tts",
            "authorization": "synthetic",
            "source_label": "Microsoft Edge TTS（发布前核对服务条款）",
            "consent_note": "平台预置合成音色；商业发布前需核对服务条款并标注 AI 生成内容",
            "created_at": created,
            "builtin": True,
        }
        return [
            VoiceProfile(
                "edge_narrator",
                "旁白·沉稳纪录片",
                edge_voice_id="zh-CN-YunyangNeural",
                gender="中性",
                age_group="中年",
                temperament="沉稳",
                pitch="中低",
                pace="中",
                speech_rate="-6%",
                speech_pitch="-10Hz",
                tags=["旁白"],
                **common,
            ),
            VoiceProfile(
                "edge_young_male",
                "青年男主·清朗",
                edge_voice_id="zh-CN-YunxiNeural",
                gender="男声",
                age_group="青年",
                temperament="温柔",
                pitch="中",
                pace="中",
                speech_pitch="+2Hz",
                tags=["主角"],
                **common,
            ),
            VoiceProfile(
                "edge_young_hero_cool",
                "少年男主·清冷克制",
                edge_voice_id="zh-CN-YunxiNeural",
                gender="男声",
                age_group="少年",
                temperament="沉稳",
                pitch="中高",
                pace="中",
                speech_rate="-2%",
                speech_pitch="+3Hz",
                tags=["主角", "少年"],
                **common,
            ),
            VoiceProfile(
                "edge_heroic_male",
                "青年男声·有力",
                edge_voice_id="zh-CN-YunjianNeural",
                gender="男声",
                age_group="青年",
                temperament="热血",
                pitch="中低",
                pace="中",
                speech_rate="+3%",
                speech_pitch="-8Hz",
                tags=["护卫", "主角"],
                **common,
            ),
            VoiceProfile(
                "edge_teen_male",
                "少年男声·明亮",
                edge_voice_id="zh-CN-YunxiaNeural",
                gender="男声",
                age_group="少年",
                temperament="活泼",
                pitch="中高",
                pace="快",
                speech_rate="+7%",
                speech_pitch="+8Hz",
                tags=["少年"],
                **common,
            ),
            VoiceProfile(
                "edge_warm_female",
                "青年女声·温暖",
                edge_voice_id="zh-CN-XiaoxiaoNeural",
                gender="女声",
                age_group="青年",
                temperament="温柔",
                pitch="中",
                pace="中",
                speech_rate="-2%",
                speech_pitch="+0Hz",
                tags=["女主"],
                **common,
            ),
            VoiceProfile(
                "edge_lively_female",
                "少女女声·灵动",
                edge_voice_id="zh-CN-XiaoyiNeural",
                gender="女声",
                age_group="少年",
                temperament="活泼",
                pitch="中高",
                pace="快",
                speech_rate="+8%",
                speech_pitch="+7Hz",
                tags=["少女"],
                **common,
            ),
            VoiceProfile(
                "edge_villain_male",
                "青年反派·低沉冷峻",
                edge_voice_id="zh-CN-YunjianNeural",
                gender="男声",
                age_group="青年",
                temperament="阴沉",
                pitch="中低",
                pace="慢",
                speech_rate="-12%",
                speech_pitch="-18Hz",
                tags=["反派"],
                **common,
            ),
            VoiceProfile(
                "edge_authority_male",
                "男长辈·威严",
                edge_voice_id="zh-CN-YunyangNeural",
                gender="男声",
                age_group="中年",
                temperament="威严",
                pitch="低",
                pace="慢",
                speech_rate="-9%",
                speech_pitch="-16Hz",
                tags=["长辈", "掌门"],
                **common,
            ),
            VoiceProfile(
                "edge_calm_male",
                "青年男配·沉静",
                edge_voice_id="zh-CN-YunxiNeural",
                gender="男声",
                age_group="青年",
                temperament="沉稳",
                pitch="中低",
                pace="慢",
                speech_rate="-5%",
                speech_pitch="-10Hz",
                tags=["配角"],
                **common,
            ),
            VoiceProfile(
                "edge_guard_male",
                "青年侍卫·果断",
                edge_voice_id="zh-CN-YunxiaNeural",
                gender="男声",
                age_group="青年",
                temperament="热血",
                pitch="中",
                pace="快",
                speech_rate="+9%",
                speech_pitch="-5Hz",
                tags=["侍卫", "护卫"],
                **common,
            ),
            VoiceProfile(
                "edge_cold_female",
                "青年女声·清冷",
                edge_voice_id="zh-CN-XiaoyiNeural",
                gender="女声",
                age_group="青年",
                temperament="冷峻",
                pitch="中",
                pace="慢",
                speech_rate="-7%",
                speech_pitch="-5Hz",
                tags=["女配"],
                **common,
            ),
            VoiceProfile(
                "edge_villain_female",
                "青年女反派·冷艳",
                edge_voice_id="zh-CN-XiaoxiaoNeural",
                gender="女声",
                age_group="青年",
                temperament="阴沉",
                pitch="中低",
                pace="慢",
                speech_rate="-10%",
                speech_pitch="-12Hz",
                tags=["反派"],
                **common,
            ),
            VoiceProfile(
                "edge_mature_female",
                "女长辈·端庄",
                edge_voice_id="zh-CN-XiaoxiaoNeural",
                gender="女声",
                age_group="中年",
                temperament="威严",
                pitch="中低",
                pace="慢",
                speech_rate="-8%",
                speech_pitch="-8Hz",
                tags=["长辈"],
                **common,
            ),
            VoiceProfile(
                "edge_liaoning_female",
                "东北女声·爽朗",
                edge_voice_id="zh-CN-liaoning-XiaobeiNeural",
                gender="女声",
                age_group="青年",
                temperament="活泼",
                pitch="中",
                pace="快",
                speech_rate="+6%",
                speech_pitch="+2Hz",
                tags=["方言", "喜剧"],
                **common,
            ),
            VoiceProfile(
                "edge_shaanxi_female",
                "陕西女声·质朴",
                edge_voice_id="zh-CN-shaanxi-XiaoniNeural",
                gender="女声",
                age_group="青年",
                temperament="沉稳",
                pitch="中",
                pace="中",
                speech_rate="-3%",
                speech_pitch="-2Hz",
                tags=["方言", "乡土"],
                **common,
            ),
        ]
