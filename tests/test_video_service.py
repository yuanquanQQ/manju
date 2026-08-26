from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.database.db import get_session
from app.database.models import Artifact, Job
from app.domain.jobs import JobStatus
from app.domain.video import EpisodeClipSpec, VideoRenderSpec
from app.services.video_service import VideoRenderService


def _ppm_image(path: Path, width: int = 96, height: int = 64) -> Path:
    pixels = bytearray()
    for y in range(height):
        for x in range(width):
            pixels.extend((30 + x % 180, 60 + y % 160, 120))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(f"P6\n{width} {height}\n255\n".encode() + pixels)
    return path


def test_video_spec_rejects_odd_dimensions(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="必须为偶数"):
        VideoRenderSpec(
            episode_number=1,
            shot_number=1,
            source_image=tmp_path / "frame.ppm",
            width=321,
            height=240,
        )


def test_first_last_frame_engine_requires_end_image(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="必须指定结束帧"):
        VideoRenderSpec(
            episode_number=1,
            shot_number=1,
            source_image=tmp_path / "start.ppm",
            engine_profile="wan22_flf2v",
        )


def test_keyframe_layout_score_accepts_same_composition(tmp_path: Path) -> None:
    start = _ppm_image(tmp_path / "start.ppm", 96, 64)
    end = _ppm_image(tmp_path / "end.ppm", 96, 64)

    score = VideoRenderService().keyframe_layout_score(start, end)

    assert score > 0.99


def test_comic_motion_clip_and_episode_preview_are_real_mp4_artifacts(
    tmp_path: Path,
) -> None:
    root = tmp_path / "project"
    source = _ppm_image(root / "production" / "video_inputs" / "shot_001.ppm")
    service = VideoRenderService()
    spec = VideoRenderSpec(
        episode_number=1,
        shot_number=1,
        source_image=source,
        scene_description="测试镜头",
        motion_prompt="轻微推近",
        camera_movement="slow_push",
        duration_seconds=1.0,
        fps=12,
        width=320,
        height=240,
    )
    progress: list[int] = []

    batch = service.generate_clips(
        root,
        [spec],
        progress_callback=lambda percent, _message: progress.append(percent),
    )

    assert progress[-1] == 100
    assert len(batch.clips) == 1
    clip = batch.clips[0]
    assert clip.video_path.read_bytes()[4:8] == b"ftyp"
    manifest = json.loads(clip.manifest_path.read_text(encoding="utf-8"))
    assert manifest["engine_profile"] == "comic_motion"
    assert manifest["shot_number"] == 1
    assert manifest["width"] == 320

    preview = service.compose_episode(root, 1, [clip.video_path])
    assert preview.video_path.read_bytes()[4:8] == b"ftyp"
    assert preview.clip_count == 1

    with get_session() as session:
        jobs = session.query(Job).all()
        artifacts = session.query(Artifact).all()
    assert {job.status for job in jobs} == {JobStatus.SUCCEEDED.value}
    assert {artifact.kind for artifact in artifacts} == {
        "shot_video",
        "episode_preview_video",
    }


def test_episode_preview_supports_cross_dissolve_timeline(tmp_path: Path) -> None:
    root = tmp_path / "project"
    first = _ppm_image(root / "production" / "video_inputs" / "first.ppm")
    second = _ppm_image(root / "production" / "video_inputs" / "second.ppm")
    service = VideoRenderService()
    batch = service.generate_clips(
        root,
        [
            VideoRenderSpec(
                episode_number=1,
                shot_number=1,
                source_image=first,
                duration_seconds=1.0,
                fps=12,
                width=320,
                height=240,
            ),
            VideoRenderSpec(
                episode_number=1,
                shot_number=2,
                source_image=second,
                duration_seconds=1.0,
                fps=12,
                width=320,
                height=240,
            ),
        ],
    )

    preview = service.compose_episode(
        root,
        1,
        [
            EpisodeClipSpec(
                path=batch.clips[0].video_path,
                shot_number=1,
                duration_seconds=1.0,
                transition_out="dissolve",
                transition_frames=6,
            ),
            EpisodeClipSpec(
                path=batch.clips[1].video_path,
                shot_number=2,
                duration_seconds=1.0,
            ),
        ],
    )

    assert preview.video_path.read_bytes()[4:8] == b"ftyp"
    manifest = json.loads(preview.manifest_path.read_text(encoding="utf-8"))
    assert manifest["engine_profile"] == "timeline_composer"
    assert manifest["transitions"][0]["transition_out"] == "dissolve"
    assert manifest["transitions"][0]["transition_frames"] == 6


def test_episode_preview_normalizes_timebase_before_chained_transition(
    tmp_path: Path,
) -> None:
    root = tmp_path / "project"
    sources = [
        _ppm_image(
            root / "production" / "video_inputs" / f"shot_{index}.ppm"
        )
        for index in range(1, 4)
    ]
    service = VideoRenderService()
    batch = service.generate_clips(
        root,
        [
            VideoRenderSpec(
                episode_number=1,
                shot_number=index,
                source_image=source,
                duration_seconds=1.0,
                fps=12,
                width=320,
                height=240,
            )
            for index, source in enumerate(sources, start=1)
        ],
    )

    preview = service.compose_episode(
        root,
        1,
        [
            EpisodeClipSpec(
                path=batch.clips[0].video_path,
                shot_number=1,
                duration_seconds=1.0,
                transition_out="cut",
            ),
            EpisodeClipSpec(
                path=batch.clips[1].video_path,
                shot_number=2,
                duration_seconds=1.0,
                transition_out="dissolve",
                transition_frames=6,
            ),
            EpisodeClipSpec(
                path=batch.clips[2].video_path,
                shot_number=3,
                duration_seconds=1.0,
            ),
        ],
    )

    assert preview.video_path.read_bytes()[4:8] == b"ftyp"
    duration = service._probe_duration(preview.video_path)
    assert duration is not None
    assert 2.5 <= duration <= 2.9


def test_episode_preview_supports_short_match_cut_blend(tmp_path: Path) -> None:
    root = tmp_path / "project"
    sources = [
        _ppm_image(root / "production" / "video_inputs" / f"match_{index}.ppm")
        for index in range(1, 3)
    ]
    service = VideoRenderService()
    batch = service.generate_clips(
        root,
        [
            VideoRenderSpec(
                episode_number=1,
                shot_number=index,
                source_image=source,
                duration_seconds=1.0,
                fps=12,
                width=320,
                height=240,
            )
            for index, source in enumerate(sources, start=1)
        ],
    )

    preview = service.compose_episode(
        root,
        1,
        [
            EpisodeClipSpec(
                path=batch.clips[0].video_path,
                shot_number=1,
                duration_seconds=1.0,
                transition_out="match_cut",
                transition_frames=4,
            ),
            EpisodeClipSpec(
                path=batch.clips[1].video_path,
                shot_number=2,
                duration_seconds=1.0,
            ),
        ],
    )

    duration = service._probe_duration(preview.video_path)
    assert duration is not None
    assert 1.6 <= duration <= 1.95


def test_episode_preview_preserves_native_audio_across_transition(
    tmp_path: Path,
) -> None:
    root = tmp_path / "project"
    service = VideoRenderService()
    clips: list[Path] = []
    for index, frequency in enumerate((440, 660), start=1):
        clip = root / "production" / "videos" / f"h3_{index}.mp4"
        clip.parent.mkdir(parents=True, exist_ok=True)
        service._run(
            [
                str(service.ffmpeg_executable),
                "-y",
                "-loglevel",
                "error",
                "-f",
                "lavfi",
                "-i",
                "color=c=black:s=320x240:r=24:d=1",
                "-f",
                "lavfi",
                "-i",
                f"sine=frequency={frequency}:sample_rate=48000:duration=1",
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "aac",
                "-shortest",
                str(clip),
            ],
            timeout=30,
        )
        clips.append(clip)

    preview = service.compose_episode(
        root,
        1,
        [
            EpisodeClipSpec(
                path=clips[0],
                shot_number=1,
                duration_seconds=1.0,
                transition_out="dissolve",
                transition_frames=6,
            ),
            EpisodeClipSpec(
                path=clips[1],
                shot_number=2,
                duration_seconds=1.0,
            ),
        ],
    )

    manifest = json.loads(preview.manifest_path.read_text(encoding="utf-8"))
    assert manifest["audio"] is True
    assert service._has_audio_stream(preview.video_path)


def test_native_audio_normalization_keeps_video_and_raises_quiet_track(
    tmp_path: Path,
) -> None:
    service = VideoRenderService()
    clip = tmp_path / "quiet_h3.mp4"
    service._run(
        [
            str(service.ffmpeg_executable),
            "-y",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=black:s=320x240:r=24:d=1",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:sample_rate=32000:duration=1",
            "-filter:a",
            "volume=0.01",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-shortest",
            str(clip),
        ],
        timeout=30,
    )

    assert service.normalize_native_audio(clip, target_lufs=-24.0)
    assert clip.read_bytes()[4:8] == b"ftyp"
    assert service._has_audio_stream(clip)
    assert 0.9 <= (service._probe_duration(clip) or 0) <= 1.1
