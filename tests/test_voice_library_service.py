import json
from pathlib import Path

from app.services.voice_library_service import VoiceLibraryService


def _write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def _project(tmp_path: Path) -> tuple[Path, VoiceLibraryService]:
    projects = tmp_path / "projects"
    root = projects / "demo"
    _write_json(root / "project.json", {"display_name": "Demo"})
    return root, VoiceLibraryService(projects)


def test_infers_traits_and_preserves_manual_voice_assignment(tmp_path: Path) -> None:
    root, service = _project(tmp_path)
    _write_json(
        root / "production/episodes/episode_001.json",
        {
            "episode_number": 1,
            "character_profiles": {
                "秦风": "18-year-old male hero, calm and cold",
                "林浪": "22-year-old male villain, commanding and sinister",
            },
            "shots": [
                {
                    "characters": [{"name": "秦风", "appearance": "young man"}],
                    "audio_generation": {"speaker": "秦风"},
                },
                {
                    "characters": [{"name": "林浪", "appearance": "young man"}],
                    "audio_generation": {"speaker": "林浪"},
                },
            ],
        },
    )

    traits = {item.character: item for item in service.infer_character_traits("demo")}

    assert traits["秦风"].gender == "男声"
    assert traits["秦风"].age_group == "青年"
    assert traits["林浪"].temperament in {"威严", "阴沉"}
    automatic = service.auto_match("demo")
    assert automatic["秦风"].profile_id
    assert automatic["秦风"].profile_id != automatic["林浪"].profile_id
    assert len(service.load_profiles()) >= 16
    service.save_manual_assignments("demo", {"林浪": "edge_heroic_male"})

    rerun = service.auto_match("demo", preserve_manual=True)

    assert rerun["林浪"].profile_id == "edge_heroic_male"
    assert rerun["林浪"].mode == "manual"


def test_applies_cloned_voice_and_resets_stale_lipsync(tmp_path: Path) -> None:
    root, service = _project(tmp_path)
    source_audio = tmp_path / "authorized.wav"
    source_audio.write_bytes(b"RIFF" + b"\0" * 4096)
    profile = service.add_cloned_voice(
        name="授权青年男声",
        source_audio=source_audio,
        reference_text="这是一段经过授权的参考台词。",
        gender="男声",
        age_group="青年",
        temperament="冷峻",
        pitch="中",
        pace="慢",
        authorization="licensed",
        consent_note="合同编号 TEST-001",
    )
    _write_json(
        root / "production/episodes/episode_001.json",
        {
            "episode_number": 1,
            "character_profiles": {"秦风": "18-year-old male hero"},
            "shots": [
                {
                    "shot_number": 1,
                    "audio_generation": {
                        "speaker": "秦风",
                        "engine": "edge_tts",
                        "voice_id": "zh-CN-YunxiNeural",
                    },
                    "video_generation": {"selected_video": "old_lipsync.mp4"},
                    "lip_sync": {
                        "enabled": True,
                        "status": "succeeded",
                        "source_video": "clean_wan.mp4",
                        "output_file": "old_lipsync.mp4",
                    },
                }
            ],
        },
    )
    service.save_manual_assignments("demo", {"秦风": profile.profile_id})

    result = service.apply_assignments("demo")

    episode = json.loads(
        (root / "production/episodes/episode_001.json").read_text(encoding="utf-8")
    )
    shot = episode["shots"][0]
    assert result.shots_updated == 1
    assert result.lip_sync_reset_shots == [(1, 1)]
    assert shot["audio_generation"]["engine"] == "cosyvoice"
    assert shot["audio_generation"]["voice_profile_id"] == profile.profile_id
    assert (root / shot["audio_generation"]["reference_audio"]).is_file()
    assert shot["lip_sync"]["status"] == "pending"
    assert shot["video_generation"]["selected_video"] == "clean_wan.mp4"
