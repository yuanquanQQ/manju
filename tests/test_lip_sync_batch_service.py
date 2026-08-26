import json
from pathlib import Path

from app.services.lip_sync_batch_service import LipSyncBatchPlanner


def _write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def test_batch_plan_skips_completed_and_blocks_unsafe_targets(tmp_path: Path) -> None:
    root = tmp_path / "project"
    video = root / "production/videos/episode_001/shot_002/source.mp4"
    completed = root / "production/videos/episode_001/shot_003/done.mp4"
    reference = root / "production/cast/qinfeng.png"
    for path in (video, completed, reference):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"test")
    _write_json(
        root / "production/cast_selection.json",
        {"selections": {"秦风": "production/cast/qinfeng.png"}},
    )
    common_character = [{"name": "秦风"}]
    _write_json(
        root / "production/episodes/episode_001.json",
        {
            "shots": [
                {
                    "shot_number": 1,
                    "dialogue": "旁白：风吹过药圃。",
                    "characters": common_character,
                    "audio_generation": {"mode": "auto_narration", "speaker": "旁白"},
                    "lip_sync": {"enabled": False},
                },
                {
                    "shot_number": 2,
                    "dialogue": "秦风：原来如此。",
                    "characters": common_character,
                    "audio_generation": {
                        "mode": "dialogue",
                        "speaker": "秦风",
                        "text": "原来如此。",
                    },
                    "video_generation": {
                        "selected_video": "production/videos/episode_001/shot_002/source.mp4"
                    },
                    "lip_sync": {
                        "enabled": True,
                        "target_character": "秦风",
                        "mode": "speaker_tracking",
                        "status": "pending",
                    },
                },
                {
                    "shot_number": 3,
                    "dialogue": "秦风：已经完成。",
                    "characters": common_character,
                    "audio_generation": {
                        "mode": "dialogue",
                        "speaker": "秦风",
                        "text": "已经完成。",
                    },
                    "video_generation": {
                        "selected_video": "production/videos/episode_001/shot_003/done.mp4"
                    },
                    "lip_sync": {
                        "enabled": True,
                        "target_character": "秦风",
                        "mode": "speaker_tracking",
                        "status": "succeeded",
                        "source_video": "production/videos/episode_001/shot_002/source.mp4",
                        "output_file": "production/videos/episode_001/shot_003/done.mp4",
                    },
                },
                {
                    "shot_number": 4,
                    "dialogue": "护卫：门外有人。",
                    "characters": common_character,
                    "audio_generation": {
                        "mode": "dialogue",
                        "speaker": "护卫",
                        "text": "门外有人。",
                    },
                    "lip_sync": {
                        "enabled": True,
                        "target_character": "护卫",
                        "mode": "speaker_tracking",
                    },
                },
            ]
        },
    )

    plan = LipSyncBatchPlanner().plan(root, 1)

    assert plan.summary() == "可执行 1 · 已完成 1 · 受阻 1 · 无需口型 1"
    assert plan.ready[0].shot_number == 2
    assert plan.ready[0].face_reference == reference
    assert plan.completed[0].shot_number == 3
    assert "不在画面角色清单" in plan.blocked[0].reason


def test_batch_plan_regeneration_uses_clean_source_not_lipsync_output(
    tmp_path: Path,
) -> None:
    root = tmp_path / "project"
    clean = root / "production/videos/clean.mp4"
    synced = root / "production/videos/shot_lipsync_old.mp4"
    reference = root / "production/cast/qinfeng.png"
    for path in (clean, synced, reference):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"test")
    _write_json(
        root / "production/cast_selection.json",
        {"selections": {"秦风": "production/cast/qinfeng.png"}},
    )
    _write_json(
        root / "production/episodes/episode_001.json",
        {
            "shots": [
                {
                    "shot_number": 1,
                    "dialogue": "秦风：再来一次。",
                    "characters": [{"name": "秦风"}],
                    "audio_generation": {
                        "mode": "dialogue",
                        "speaker": "秦风",
                        "text": "再来一次。",
                    },
                    "video_generation": {
                        "selected_video": "production/videos/shot_lipsync_old.mp4"
                    },
                    "lip_sync": {
                        "enabled": True,
                        "target_character": "秦风",
                        "mode": "speaker_tracking",
                        "status": "succeeded",
                        "source_video": "production/videos/clean.mp4",
                        "output_file": "production/videos/shot_lipsync_old.mp4",
                    },
                }
            ]
        },
    )

    plan = LipSyncBatchPlanner().plan(root, 1, regenerate_completed=True)

    assert len(plan.ready) == 1
    assert plan.ready[0].source_video == clean


def test_batch_plan_prefers_shot_face_reference_over_cast_selection(
    tmp_path: Path,
) -> None:
    root = tmp_path / "project"
    video = root / "production/videos/source.mp4"
    cast_reference = root / "production/cast/hero.png"
    shot_reference = root / "production/face_anchors/shot_001.png"
    for path in (video, cast_reference, shot_reference):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"test")
    _write_json(
        root / "production/cast_selection.json",
        {"selections": {"Hero": "production/cast/hero.png"}},
    )
    _write_json(
        root / "production/episodes/episode_001.json",
        {
            "shots": [
                {
                    "shot_number": 1,
                    "characters": [{"name": "Hero"}],
                    "audio_generation": {
                        "mode": "dialogue",
                        "speaker": "Hero",
                        "text": "Try again.",
                    },
                    "video_generation": {
                        "selected_video": "production/videos/source.mp4"
                    },
                    "lip_sync": {
                        "enabled": True,
                        "target_character": "Hero",
                        "mode": "speaker_tracking",
                        "face_reference": "production/face_anchors/shot_001.png",
                    },
                }
            ]
        },
    )

    plan = LipSyncBatchPlanner().plan(root, 1)

    assert plan.ready[0].face_reference == shot_reference
