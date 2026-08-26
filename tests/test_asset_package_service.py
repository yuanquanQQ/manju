import json
import re
from pathlib import Path

from app.core.naming import pinyin_slug
from app.services.asset_package_service import AssetPackageService


def _write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def test_pinyin_slug_uses_ascii_pinyin_and_digits() -> None:
    assert pinyin_slug("秦风 001") == "qin_feng_001"
    assert pinyin_slug("云影镇·秦家药圃") == "yun_ying_zhen_qin_jia_yao_pu"


def test_organizes_assets_and_deliverables_without_moving_sources(
    tmp_path: Path,
) -> None:
    projects = tmp_path / "projects"
    root = projects / "jueshi"
    _write_json(root / "project.json", {"display_name": "绝世"})
    cast_source = root / "outputs" / "cast" / "hero.png"
    cast_source.parent.mkdir(parents=True, exist_ok=True)
    cast_source.write_bytes(b"character")
    frame = root / "production/video_inputs/episode_001/shot_001.png"
    frame.parent.mkdir(parents=True, exist_ok=True)
    frame.write_bytes(b"scene")
    video = root / "production/videos/episode_001/final.mp4"
    subtitle = root / "production/videos/episode_001/final.srt"
    audio = root / "production/audio/episode_001/line.mp3"
    line_subtitle = root / "production/audio/episode_001/line.srt"
    line_manifest = root / "production/audio/episode_001/line.json"
    for path, value in (
        (video, b"video"),
        (subtitle, b"subtitle"),
        (audio, b"audio"),
        (line_subtitle, b"line subtitle"),
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(value)
    _write_json(
        line_manifest,
        {"audio_file": audio.relative_to(root).as_posix()},
    )
    episode_manifest = root / "production/videos/episode_001/manifest.json"
    _write_json(
        episode_manifest,
        {
            "generated_at": "2026-08-04T20:40:13+08:00",
            "lines": [
                {
                    "shot_number": 1,
                    "speaker": "秦风",
                    "text": "出发。",
                    "voice_id": "voice-a",
                    "engine": "edge_tts",
                    "audio_file": audio.relative_to(root).as_posix(),
                    "subtitle_file": line_subtitle.relative_to(root).as_posix(),
                }
            ],
        },
    )
    _write_json(
        root / "production/cast_selection.json",
        {"selections": {"秦风": cast_source.relative_to(root).as_posix()}},
    )
    episode_path = root / "production/episodes/episode_001.json"
    _write_json(
        episode_path,
        {
            "episode_number": 1,
            "character_profiles": {"秦风": "少年男主"},
            "shots": [
                {
                    "shot_number": 1,
                    "environment": {
                        "layout": "云影镇秦家药圃；清晨薄雾",
                        "lighting": "晨光",
                    },
                    "audio_generation": {
                        "speaker": "秦风",
                        "audio_file": audio.relative_to(root).as_posix(),
                        "subtitle_file": line_subtitle.relative_to(root).as_posix(),
                        "manifest_file": line_manifest.relative_to(root).as_posix(),
                    },
                }
            ],
            "dubbing": {
                "output_file": video.relative_to(root).as_posix(),
                "subtitle_file": subtitle.relative_to(root).as_posix(),
                "manifest_file": episode_manifest.relative_to(root).as_posix(),
            },
        },
    )

    result = AssetPackageService(projects).organize("jueshi")

    assert result.episodes_organized == 1
    assert cast_source.is_file()
    assert video.is_file()
    package = root / "outputs/episodes/jueshi_001"
    assert len(list((package / "shipin").glob("*.mp4"))) == 1
    assert len(list((package / "zimu").glob("*.srt"))) == 1
    assert len(list((package / "yinpin").glob("*.mp3"))) == 1
    assert len(list((package / "qingdan").glob("*.json"))) == 1
    all_names = [path.name for path in package.rglob("*")]
    assert all(not re.search(r"[\u3400-\u9fff]", name) for name in all_names)
    episode = json.loads(episode_path.read_text(encoding="utf-8"))
    assert episode["dubbing"]["output_file"].startswith("outputs/episodes/")
    assert episode["shots"][0]["audio_generation"]["audio_file"].startswith(
        "outputs/episodes/"
    )

