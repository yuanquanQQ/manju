from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import app.services.desktop_service as desktop_module
from app.services.desktop_service import DesktopProjectService


def _create_database(path: Path) -> None:
    path.parent.mkdir(parents=True)
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE compiled_chapters (id TEXT, chapter_order INTEGER);
            CREATE TABLE chapter_analysis_runs (
                id TEXT,
                chapter_id TEXT,
                status TEXT
            );
            CREATE TABLE entities (id TEXT);
            CREATE TABLE narrative_events (id TEXT);
            CREATE TABLE jobs (
                id TEXT,
                job_type TEXT,
                status TEXT,
                progress REAL,
                error_message TEXT,
                updated_at TEXT
            );
            INSERT INTO compiled_chapters VALUES ('ch_1', 1);
            INSERT INTO compiled_chapters VALUES ('ch_2', 2);
            INSERT INTO chapter_analysis_runs VALUES ('run_1', 'ch_1', 'SUCCEEDED');
            INSERT INTO chapter_analysis_runs VALUES ('run_2', 'ch_1', 'SUCCEEDED');
            INSERT INTO entities VALUES ('entity_1');
            INSERT INTO narrative_events VALUES ('event_1');
            INSERT INTO jobs VALUES (
                'job_1',
                'novel.compile',
                'SUCCEEDED',
                1.0,
                '',
                '2026-07-27'
            );
            """
        )


def test_desktop_project_snapshot_and_character_gallery(tmp_path: Path) -> None:
    projects = tmp_path / "projects"
    project = projects / "demo"
    (project / "production" / "episodes").mkdir(parents=True)
    (project / "novel" / "chapters").mkdir(parents=True)
    (project / "outputs" / "run_1").mkdir(parents=True)
    (project / "project.json").write_text(
        json.dumps({"display_name": "演示项目"}),
        encoding="utf-8",
    )
    _create_database(project / "database" / "world.db")
    (project / "production" / "episodes" / "episode_001.json").write_text(
        json.dumps(
            {
                "episode_number": 1,
                "episode_title": "第一集",
                "character_profiles": {"秦风": "十八岁青年"},
                "shots": [
                    {
                        "shot_number": 1,
                        "scene_description": "药圃清晨",
                        "image_prompt": "live action",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (project / "novel" / "chapters" / "ch_000001.json").write_text(
        json.dumps(
            {
                "order": 1,
                "title": "第一章 初入药园",
                "content": "秦风清晨进入药园，遇见林浪。",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    image = project / "outputs" / "run_1" / "candidate.png"
    image.write_bytes(b"png")
    (image.parent / "manifest.json").write_text(
        json.dumps(
            {
                "model": "flux1-krea-dev_fp8_scaled.safetensors",
                "generated_at": "2026-07-27T18:42:54+08:00",
                "layout_label": "三视图·无背景",
                "images": [
                    {
                        "character": "秦风",
                        "file": image.name,
                        "model_id": "flux_krea",
                        "model_label": "FLUX.1 Krea Dev FP8",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    service = DesktopProjectService(projects)
    assert service.list_projects() == ["demo"]

    snapshot = service.load_project("demo")
    assert snapshot.display_name == "演示项目"
    assert snapshot.chapter_count == 2
    assert snapshot.analysis_count == 1
    assert snapshot.entity_count == 1
    assert snapshot.event_count == 1
    assert snapshot.episode_count == 1
    assert snapshot.cast_character_count == 1
    assert snapshot.cast_selected_count == 0
    assert snapshot.job_counts == {"SUCCEEDED": 1}

    episodes = service.load_episodes("demo")
    assert len(episodes) == 1
    assert episodes[0].title == "第一集"
    assert episodes[0].shots[0].description == "药圃清晨"
    character_image = episodes[0].characters[0].images[0]
    assert character_image.path == image
    assert character_image.model_label == "FLUX.1 Krea Dev FP8"
    assert character_image.generated_at == "2026-07-27 18:42:54"
    assert character_image.layout_label == "三视图·无背景"
    assert episodes[0].characters[0].style == "真人电影"
    assert episodes[0].characters[0].generation_preset == "portrait"
    assert episodes[0].shots[0].style == "真人电影"

    chapters = service.load_chapters("demo")
    assert chapters[0].title == "第一章 初入药园"
    assert chapters[0].character_count == 14

    service.save_character_prompt(
        "demo",
        1,
        "秦风",
        "十八岁俊美少年，白衣黑发",
        "中国水墨",
        "turnaround_no_bg",
    )
    service.save_shot_prompt(
        "demo",
        1,
        1,
        "moonlit herb garden, wide shot",
        "油画",
    )
    episodes = service.load_episodes("demo")
    assert episodes[0].characters[0].profile == "十八岁俊美少年，白衣黑发"
    assert episodes[0].characters[0].style == "中国水墨"
    assert episodes[0].characters[0].generation_preset == "turnaround_no_bg"
    assert episodes[0].shots[0].prompt == "moonlit herb garden, wide shot"
    assert episodes[0].shots[0].style == "油画"

    selection_file = service.select_character_image("demo", "秦风", image)
    assert selection_file.exists()
    episodes = service.load_episodes("demo")
    assert episodes[0].characters[0].selected_image == image
    assert service.load_project("demo").cast_selected_count == 1

    selection_file = service.clear_character_selection("demo", "秦风")
    assert selection_file.exists()
    episodes = service.load_episodes("demo")
    assert episodes[0].characters[0].selected_image is None
    assert episodes[0].characters[0].images[0].path == image
    assert service.load_project("demo").cast_selected_count == 0

    external_frame = tmp_path / "shot_frame.png"
    external_frame.write_bytes(b"png")
    stored_frame = service.set_shot_source_image(
        "demo",
        1,
        1,
        external_frame,
    )
    archived_frame = service.archive_shot_source_candidate(
        "demo",
        1,
        1,
        stored_frame,
    )
    assert archived_frame != stored_frame
    assert archived_frame.read_bytes() == b"png"
    archived_again = service.archive_shot_source_candidate(
        "demo",
        1,
        1,
        stored_frame,
    )
    assert archived_again == archived_frame
    revised_frame = (
        project
        / "production"
        / "shots"
        / "episode_001"
        / "revisions"
        / "revision_flux_kontext_candidate_01.png"
    )
    revised_manifest = revised_frame.with_name("manifest.json")
    revised_frame.parent.mkdir(parents=True, exist_ok=True)
    revised_frame.write_bytes(b"revised-png")
    revised_manifest.write_text("{}", encoding="utf-8")
    service.save_shot_image_result(
        "demo",
        1,
        1,
        revised_frame,
        revised_manifest,
        {
            "model_id": "flux_kontext",
            "model_label": "FLUX.1 Kontext Dev FP8",
            "generated_at": "2026-08-08T18:20:30+08:00",
            "seed": 42,
            "prompt": "repair the face",
        },
        select=False,
    )
    shot_with_history = service.load_episodes("demo")[0].shots[0]
    assert revised_frame in [item.path for item in shot_with_history.image_candidates]
    revision = next(
        item
        for item in shot_with_history.image_candidates
        if item.path == revised_frame
    )
    assert revision.model_label == "FLUX.1 Kontext Dev FP8"
    assert revision.generated_at == "2026-08-08 18:20:30"

    selected_candidate = service.select_shot_image_candidate(
        "demo",
        1,
        1,
        revised_frame,
    )
    assert selected_candidate == stored_frame
    assert selected_candidate.read_bytes() == b"revised-png"
    assert archived_frame.read_bytes() == b"png"
    episode_value = json.loads(
        (project / "production" / "episodes" / "episode_001.json").read_text(
            encoding="utf-8"
        )
    )
    generation = episode_value["shots"][0]["image_generation"]
    assert generation["selected_source"].endswith(
        "revision_flux_kontext_candidate_01.png"
    )
    assert generation["manifest"].endswith("revisions/manifest.json")
    end_frame = tmp_path / "shot_end.png"
    end_frame.write_bytes(b"png")
    stored_end_frame = service.set_shot_end_image(
        "demo",
        1,
        1,
        end_frame,
    )
    service.save_video_settings(
        "demo",
        1,
        1,
        engine_profile="wan22_flf2v",
        subject_motion="秦风缓慢抬头",
        environment_motion="晨雾从左向右流动",
        continuity_constraints="保持脸型、服装和药圃布局一致",
        negative_prompt="face morphing, extra limbs",
        motion_prompt="秦风抬头，晨雾流动",
        camera_movement="slow_push",
        motion_strength="low",
        screen_direction="left_to_right",
        transition_out="dissolve",
        transition_frames=6,
        handle_frames=10,
        candidate_count=3,
        duration_seconds=4.5,
    )
    video = (
        project
        / "production"
        / "videos"
        / "episode_001"
        / "shot_001"
        / "shot_001.mp4"
    )
    manifest = video.with_name("manifest.json")
    video.parent.mkdir(parents=True)
    video.write_bytes(b"mp4")
    manifest.write_text("{}", encoding="utf-8")
    service.save_shot_video_result("demo", 1, 1, video, manifest)

    shot = service.load_episodes("demo")[0].shots[0]
    assert shot.source_image == stored_frame
    assert shot.end_image == stored_end_frame
    assert shot.video_path == video
    assert shot.engine_profile == "wan22_flf2v"
    assert shot.subject_motion == "秦风缓慢抬头"
    assert shot.environment_motion == "晨雾从左向右流动"
    assert shot.continuity_constraints == "保持脸型、服装和药圃布局一致"
    assert shot.negative_prompt == "face morphing, extra limbs"
    assert shot.motion_prompt == "秦风抬头，晨雾流动"
    assert shot.camera_movement == "slow_push"
    assert shot.motion_strength == "low"
    assert shot.screen_direction == "left_to_right"
    assert shot.transition_out == "dissolve"
    assert shot.transition_frames == 6
    assert shot.handle_frames == 10
    assert shot.candidate_count == 3
    assert shot.duration_seconds == 4.5
    assert shot.image_qc_status == "pending"

    service.set_shot_image_qc(
        "demo",
        1,
        1,
        "approved",
        "face and composition accepted",
    )
    approved = service.load_episodes("demo")[0].shots[0]
    assert approved.image_qc_status == "approved"
    assert approved.image_qc_note == "face and composition accepted"
    assert approved.image_qc_checked_at

    replacement_frame = tmp_path / "replacement_frame.png"
    replacement_frame.write_bytes(b"new png")
    service.set_shot_source_image("demo", 1, 1, replacement_frame)
    replaced = service.load_episodes("demo")[0].shots[0]
    assert replaced.image_qc_status == "pending"
    assert replaced.image_qc_note == ""
    assert service.episode_video_paths("demo", 1) == [video]

    jobs = service.load_jobs("demo")
    assert jobs[0].job_type == "novel.compile"
    assert jobs[0].progress == 1.0


def test_reprocess_existing_content_forces_analysis_and_backs_up_episodes(
    tmp_path: Path,
    monkeypatch,
) -> None:
    projects = tmp_path / "projects"
    project = projects / "demo"
    chapters = project / "novel" / "chapters"
    episodes = project / "production" / "episodes"
    chapters.mkdir(parents=True)
    episodes.mkdir(parents=True)
    (chapters / "ch_000001.json").write_text("{}", encoding="utf-8")
    episode = episodes / "episode_001.json"
    episode.write_text('{"episode_number": 1}', encoding="utf-8")

    compile_calls: list[dict] = []
    storyboard_calls: list[dict] = []
    monkeypatch.setattr(desktop_module, "init_db", lambda _path: None)

    def fake_compile(**kwargs):
        compile_calls.append(kwargs)
        kwargs["progress_callback"](1, 1, "done")
        return {"analyzed": 1}

    def fake_storyboard(root, **kwargs):
        storyboard_calls.append({"root": root, **kwargs})
        kwargs["progress_callback"](1, 1, "done")
        return [SimpleNamespace(shots=[object(), object()])]

    monkeypatch.setattr(desktop_module, "run_compile_novel", fake_compile)
    monkeypatch.setattr(desktop_module, "generate_storyboard", fake_storyboard)

    progress: list[tuple[int, str]] = []
    result = DesktopProjectService(projects).reprocess_novel(
        "demo",
        analysis_limit=1,
        progress_callback=lambda percent, message: progress.append((percent, message)),
    )

    assert compile_calls[0]["force"] is True
    assert compile_calls[0]["limit"] == 1
    assert storyboard_calls[0]["limit"] == 1
    assert result["chapters"] == 1
    assert result["episodes"] == 1
    assert result["shots"] == 2
    assert (result["backup_dir"] / episode.name).read_text(encoding="utf-8") == (
        episode.read_text(encoding="utf-8")
    )
    assert progress[-1][0] == 100


def test_prepare_shot_automation_backfills_prompts_and_prefers_h3(
    tmp_path: Path,
) -> None:
    projects = tmp_path / "projects"
    episode_dir = projects / "demo" / "production" / "episodes"
    episode_dir.mkdir(parents=True)
    episode_path = episode_dir / "episode_001.json"
    episode_path.write_text(
        json.dumps(
            {
                "episode_number": 1,
                "shots": [
                    {
                        "shot_number": 1,
                        "scene_description": "少年缓慢抬头，晨雾掠过药圃",
                        "camera_movement": "dolly",
                        "transition": "dissolve",
                        "duration_seconds": 4,
                        "video_generation": {
                            "engine_profile": "comic_motion",
                            "routing_version": 3,
                            "subject_motion": "",
                            "continuity_constraints": "",
                            "negative_prompt": "",
                            "selected_video": "production/videos/old_wan.mp4",
                            "manifest_file": "production/videos/old_wan.json",
                            "candidates": [
                                {"file": "production/videos/old_wan.mp4"}
                            ],
                        },
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    stats = DesktopProjectService(projects).prepare_shot_automation("demo")
    value = json.loads(episode_path.read_text(encoding="utf-8"))
    shot = value["shots"][0]
    video = shot["video_generation"]

    assert stats["shots"] == 1
    assert stats["video_selections_cleared"] == 1
    assert stats["image_prompts_added"] == 1
    assert shot["image_prompt"].startswith("masterpiece, best quality")
    assert video["engine_profile"] == "minimax_h3_fl2va"
    assert video["selected_video"] == ""
    assert video["manifest_file"] == ""
    assert video["candidates"] == [
        {"file": "production/videos/old_wan.mp4"}
    ]
    assert video["subject_motion"] == "少年缓慢抬头，晨雾掠过药圃"
    assert video["camera_movement"] == "slow_push"
    assert video["transition_out"] == "dissolve"
    assert video["duration_seconds"] == 4
    assert "face morphing" in video["negative_prompt"]
    assert "保持人物" in video["continuity_constraints"]


def test_prepare_shot_automation_adds_motion_ready_full_body_framing(
    tmp_path: Path,
) -> None:
    projects = tmp_path / "projects"
    episode_dir = projects / "demo" / "production" / "episodes"
    episode_dir.mkdir(parents=True)
    episode_path = episode_dir / "episode_001.json"
    episode_path.write_text(
        json.dumps(
            {
                "episode_number": 1,
                "shots": [
                    {
                        "shot_number": 1,
                        "scene_description": "少年沿土路向前行走",
                        "image_prompt": "photorealistic young cultivator on a path",
                        "video_generation": {
                            "subject_motion": "向前走两步后停下",
                        },
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    service = DesktopProjectService(projects)
    first = service.prepare_shot_automation("demo")
    second = service.prepare_shot_automation("demo")
    prompt = json.loads(episode_path.read_text(encoding="utf-8"))["shots"][0][
        "image_prompt"
    ]

    assert first["motion_framing_prompts_updated"] == 1
    assert second["motion_framing_prompts_updated"] == 0
    assert "both feet fully visible" in prompt
    assert prompt.count("both feet fully visible") == 1


def test_prepare_shot_automation_keeps_close_up_locomotion_framing(
    tmp_path: Path,
) -> None:
    projects = tmp_path / "projects"
    episode_dir = projects / "demo" / "production" / "episodes"
    episode_dir.mkdir(parents=True)
    episode_path = episode_dir / "episode_001.json"
    episode_path.write_text(
        json.dumps(
            {
                "episode_number": 1,
                "shots": [
                    {
                        "shot_number": 1,
                        "scene_description": "人物中近景，背景随从向左走出画面",
                        "image_prompt": "photorealistic medium close shot",
                        "video_generation": {
                            "subject_motion": "背景随从向左走出数步",
                        },
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    stats = DesktopProjectService(projects).prepare_shot_automation("demo")
    prompt = json.loads(episode_path.read_text(encoding="utf-8"))["shots"][0][
        "image_prompt"
    ]

    assert stats["motion_framing_prompts_updated"] == 0
    assert "both feet fully visible" not in prompt


def test_prepare_shot_automation_routes_real_motion_but_ignores_negated_action(
    tmp_path: Path,
) -> None:
    projects = tmp_path / "projects"
    episode_dir = projects / "demo" / "production" / "episodes"
    episode_dir.mkdir(parents=True)
    episode_path = episode_dir / "episode_001.json"
    episode_path.write_text(
        json.dumps(
            {
                "episode_number": 1,
                "shots": [
                    {
                        "shot_number": 1,
                        "scene_description": "少年在土路上",
                        "characters": [{"name": "少年"}],
                        "video_generation": {"subject_motion": "向前走两步后停下"},
                    },
                    {
                        "shot_number": 2,
                        "scene_description": "护卫保持戒备",
                        "characters": [{"name": "护卫"}],
                        "video_generation": {"subject_motion": "保持原位，不挥剑，只缓慢呼吸"},
                    },
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    DesktopProjectService(projects).prepare_shot_automation("demo")
    shots = json.loads(episode_path.read_text(encoding="utf-8"))["shots"]

    assert shots[0]["video_generation"]["engine_profile"] == "minimax_h3_fl2va"
    assert shots[0]["video_generation"]["motion_strength"] == "high"
    assert shots[1]["video_generation"]["engine_profile"] == "minimax_h3_fl2va"
    assert "END KEYFRAME" in shots[0]["video_generation"]["end_frame_prompt"]


def test_audio_timing_and_lip_sync_settings_are_persisted(
    tmp_path: Path,
) -> None:
    projects = tmp_path / "projects"
    episode_dir = projects / "demo" / "production" / "episodes"
    episode_dir.mkdir(parents=True)
    episode_path = episode_dir / "episode_001.json"
    episode_path.write_text(
        json.dumps(
            {
                "episode_number": 1,
                "shots": [
                    {
                        "shot_number": 1,
                        "scene_description": "秦风在院中向林浪解释眼前的危险。",
                        "duration_seconds": 3.0,
                        "video_generation": {"duration_seconds": 3.0},
                        "audio_generation": {
                            "mode": "dialogue",
                            "speaker": "秦风",
                            "text": (
                                "现在立刻离开这里，追兵很快就会封锁山门，"
                                "我们不能继续耽搁。"
                            ),
                        },
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    service = DesktopProjectService(projects)

    summary = service.optimize_audio_timeline("demo", 1)
    service.save_lip_sync_settings(
        "demo",
        1,
        1,
        enabled=True,
        target_character="秦风",
        mode="speaker_tracking",
    )
    shot = service.load_episodes("demo")[0].shots[0]

    assert summary.estimated_speech_seconds > 0
    assert shot.planned_timeline_duration_seconds > 3.0
    assert shot.timing_status in {"needs_regeneration", "needs_split"}
    assert shot.lip_sync_enabled is True
    assert shot.lip_sync_target_character == "秦风"
    assert shot.lip_sync_status == "pending"


def test_lip_sync_result_becomes_selected_shot_video(tmp_path: Path) -> None:
    projects = tmp_path / "projects"
    project = projects / "demo"
    episode_dir = project / "production" / "episodes"
    shot_dir = project / "production" / "videos" / "episode_001" / "shot_001"
    audio_dir = project / "production" / "audio" / "episode_001"
    episode_dir.mkdir(parents=True)
    shot_dir.mkdir(parents=True)
    audio_dir.mkdir(parents=True)
    episode_path = episode_dir / "episode_001.json"
    episode_path.write_text(
        json.dumps(
            {
                "episode_number": 1,
                "shots": [
                    {
                        "shot_number": 1,
                        "scene_description": "秦风开口说明药圃异状。",
                        "video_generation": {},
                        "audio_generation": {
                            "mode": "dialogue",
                            "speaker": "秦风",
                            "text": "草根外露。",
                        },
                        "lip_sync": {
                            "enabled": True,
                            "status": "pending",
                        },
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    source = shot_dir / "source.mp4"
    output = shot_dir / "lipsync.mp4"
    audio = audio_dir / "line.wav"
    manifest = shot_dir / "manifest_lipsync.json"
    for path in (source, output, audio, manifest):
        path.write_bytes(b"artifact")

    service = DesktopProjectService(projects)
    service.save_lip_sync_result(
        "demo",
        1,
        1,
        output,
        audio,
        source,
        manifest,
        elapsed_seconds=12.5,
        face_match_similarity=0.2752,
    )
    value = json.loads(episode_path.read_text(encoding="utf-8"))
    shot_value = value["shots"][0]
    snapshot = service.load_episodes("demo")[0].shots[0]

    assert shot_value["lip_sync"]["status"] == "succeeded"
    assert shot_value["video_generation"]["selected_video"].endswith(
        "lipsync.mp4"
    )
    assert shot_value["audio_generation"]["audio_file"].endswith("line.wav")
    assert snapshot.video_path == output
    assert snapshot.lip_sync_output_path == output
    assert snapshot.lip_sync_score == 0.2752
