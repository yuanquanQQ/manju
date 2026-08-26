from __future__ import annotations

import json
from pathlib import Path

from app.domain.audio import DubbingLineSpec
from app.domain.video import VideoRenderSpec
from app.services.audio_service import DubbingService
from app.services.video_service import VideoRenderService


def _ppm_image(path: Path, width: int = 96, height: int = 64) -> Path:
    pixels = bytearray()
    for y in range(height):
        for x in range(width):
            pixels.extend((30 + x % 180, 60 + y % 160, 120))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(f"P6\n{width} {height}\n255\n".encode() + pixels)
    return path


def _video_with_audio(path: Path, *, frequency: int = 660) -> Path:
    service = DubbingService()
    path.parent.mkdir(parents=True, exist_ok=True)
    service._run(
        [
            str(service.ffmpeg_executable),
            "-y",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=navy:s=320x240:r=24:d=1",
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
            str(path),
        ],
        timeout=30,
    )
    return path


def test_dubbing_service_creates_audio_subtitles_and_dubbed_video(
    tmp_path: Path,
    monkeypatch,
) -> None:
    root = tmp_path / "project"
    source = _ppm_image(root / "production" / "video_inputs" / "shot.ppm")
    clip = VideoRenderService().generate_clips(
        root,
        [
            VideoRenderSpec(
                episode_number=1,
                shot_number=1,
                source_image=source,
                duration_seconds=1.0,
                fps=12,
                width=320,
                height=240,
            )
        ],
    ).clips[0]
    service = DubbingService()

    def fake_synthesize(spec, audio_path, subtitle_path) -> None:
        service._run(
            [
                str(service.ffmpeg_executable),
                "-y",
                "-loglevel",
                "error",
                "-f",
                "lavfi",
                "-i",
                "sine=frequency=440:duration=0.6",
                "-c:a",
                "libmp3lame",
                str(audio_path),
            ],
            timeout=30,
        )
        subtitle_path.write_text(
            "1\n00:00:00,000 --> 00:00:00,600\n测试旁白\n",
            encoding="utf-8",
        )

    monkeypatch.setattr(service, "_synthesize_edge", fake_synthesize)
    progress: list[int] = []
    result = service.dub_episode(
        root,
        1,
        [
            DubbingLineSpec(
                episode_number=1,
                shot_number=1,
                source_video=clip.video_path,
                text="测试旁白",
            )
        ],
        progress_callback=lambda percent, _message: progress.append(percent),
        visible_ai_label=True,
    )

    assert progress[-1] == 100
    assert result.video_path.read_bytes()[4:8] == b"ftyp"
    assert result.subtitle_path.read_text(encoding="utf-8").endswith(
        "测试旁白\n"
    )
    assert result.lines[0].audio_path.is_file()
    assert result.video_path.parent.name == "shipin"
    assert result.subtitle_path.parent.name == "zimu"
    assert result.lines[0].audio_path.parent.name == "yinpin"
    assert result.lines[0].subtitle_path.parent.name == "fenduan"
    assert all(ord(character) < 128 for character in result.video_path.name)
    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    assert manifest["audio"] is True
    assert manifest["subtitles_burned"] is True
    assert manifest["visible_ai_label"] is True
    assert manifest["ai_content_label"] == "AI生成内容"
    assert manifest["lines"][0]["voice_id"] == "zh-CN-YunyangNeural"


def test_dubbing_service_preserves_muted_shot_in_timeline(
    tmp_path: Path,
) -> None:
    root = tmp_path / "project"
    source = _ppm_image(root / "production" / "video_inputs" / "shot.ppm")
    clip = VideoRenderService().generate_clips(
        root,
        [
            VideoRenderSpec(
                episode_number=1,
                shot_number=1,
                source_image=source,
                duration_seconds=1.0,
                fps=12,
                width=320,
                height=240,
            )
        ],
    ).clips[0]

    result = DubbingService().dub_episode(
        root,
        1,
        [
            DubbingLineSpec(
                episode_number=1,
                shot_number=1,
                source_video=clip.video_path,
                mode="mute",
                subtitle_enabled=False,
            )
        ],
    )

    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    assert result.video_path.read_bytes()[4:8] == b"ftyp"
    assert manifest["audio"] is True
    assert manifest["subtitles_burned"] is False
    assert manifest["visible_ai_label"] is False
    assert len(manifest["lines"]) == 1


def test_dubbing_service_keeps_h3_native_audio_bed(
    tmp_path: Path,
) -> None:
    root = tmp_path / "project"
    source_video = _video_with_audio(
        root / "production" / "videos" / "h3_native_audio.mp4"
    )
    service = DubbingService()

    result = service.dub_episode(
        root,
        1,
        [
            DubbingLineSpec(
                episode_number=1,
                shot_number=1,
                source_video=source_video,
                mode="mute",
                subtitle_enabled=False,
                preserve_source_audio=True,
                source_audio_gain_db=-3.0,
            )
        ],
    )

    line_manifest = json.loads(
        result.lines[0].manifest_path.read_text(encoding="utf-8")
    )
    episode_manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    assert line_manifest["source_audio_preserved"] is True
    assert episode_manifest["lines"][0]["source_audio_preserved"] is True
    assert service._has_audio_stream(result.video_path)


def test_dubbing_service_reuses_prepared_lipsync_audio(
    tmp_path: Path,
    monkeypatch,
) -> None:
    root = tmp_path / "project"
    source = _ppm_image(root / "production" / "video_inputs" / "shot.ppm")
    clip = VideoRenderService().generate_clips(
        root,
        [
            VideoRenderSpec(
                episode_number=1,
                shot_number=1,
                source_image=source,
                duration_seconds=1.0,
                fps=12,
                width=320,
                height=240,
            )
        ],
    ).clips[0]
    service = DubbingService()
    prepared = root / "production" / "audio" / "lipsync.mp3"
    prepared.parent.mkdir(parents=True, exist_ok=True)
    service._run(
        [
            str(service.ffmpeg_executable),
            "-y",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=0.6",
            "-c:a",
            "libmp3lame",
            str(prepared),
        ],
        timeout=30,
    )
    prepared.with_suffix(".srt").write_text(
        (
            "1\n"
            "00:00:00,100 --> 00:00:00,550\n"
            "林浪：你若完成今年灵药收缴，本公子便把林家铁矿矿脉拱手相让！\n"
        ),
        encoding="utf-8",
    )

    def unexpected_synthesis(*_args, **_kwargs) -> None:
        raise AssertionError("prepared lip-sync audio must not be synthesized again")

    monkeypatch.setattr(service, "_synthesize_edge", unexpected_synthesis)
    result = service.dub_episode(
        root,
        1,
        [
            DubbingLineSpec(
                episode_number=1,
                shot_number=1,
                source_video=clip.video_path,
                prepared_audio=prepared,
                mode="dialogue",
                text="你若完成今年灵药收缴，本公子便把林家铁矿矿脉拱手相让！",
                speaker="林浪",
                lead_seconds=0.0,
            )
        ],
    )

    assert result.lines[0].audio_path == prepared
    assert result.lines[0].subtitle_path == prepared.with_suffix(".srt")
    manifest = json.loads(result.lines[0].manifest_path.read_text(encoding="utf-8"))
    assert manifest["prepared_audio"] == "production/audio/lipsync.mp3"
    episode_subtitles = result.subtitle_path.read_text(encoding="utf-8")
    assert "00:00:00,100 --> 00:00:00,550" in episode_subtitles
    assert "林浪：" not in episode_subtitles
    assert (
        "你若完成今年灵药收缴，\n本公子便把林家铁矿矿脉拱手相让！"
        in episode_subtitles
    )


def test_cosyvoice_preview_uses_external_synthesizer_and_writes_subtitle(
    tmp_path: Path,
) -> None:
    service = DubbingService()
    destination = tmp_path / "preview.wav"

    def fake_cosyvoice(spec, audio_path, _subtitle_path) -> None:
        assert spec.reference_text == "参考台词"
        service._run(
            [
                str(service.ffmpeg_executable),
                "-y",
                "-loglevel",
                "error",
                "-f",
                "lavfi",
                "-i",
                "sine=frequency=330:duration=0.4",
                str(audio_path),
            ],
            timeout=30,
        )

    result = service.synthesize_preview(
        DubbingLineSpec(
            episode_number=1,
            shot_number=1,
            source_video=tmp_path,
            text="本地模型试听",
            engine="cosyvoice",
            reference_audio=tmp_path / "reference.wav",
            reference_text="参考台词",
        ),
        destination,
        external_synthesizers={"cosyvoice": fake_cosyvoice},
    )

    assert result == destination.resolve()
    assert result.read_bytes()[:4] == b"RIFF"
    assert "本地模型试听" in result.with_suffix(".srt").read_text(
        encoding="utf-8"
    )


def test_cosyvoice_preview_falls_back_to_edge(
    tmp_path: Path,
    monkeypatch,
) -> None:
    service = DubbingService()

    def failed_cosyvoice(_spec, _audio_path, _subtitle_path) -> None:
        raise RuntimeError("GPU service unavailable")

    def fake_edge(_spec, audio_path, subtitle_path) -> None:
        service._run(
            [
                str(service.ffmpeg_executable),
                "-y",
                "-loglevel",
                "error",
                "-f",
                "lavfi",
                "-i",
                "sine=frequency=220:duration=0.3",
                "-c:a",
                "libmp3lame",
                str(audio_path),
            ],
            timeout=30,
        )
        subtitle_path.write_text("", encoding="utf-8")

    monkeypatch.setattr(service, "_synthesize_edge", fake_edge)
    result = service.synthesize_preview(
        DubbingLineSpec(
            episode_number=1,
            shot_number=1,
            source_video=tmp_path,
            text="自动回退",
            engine="cosyvoice",
        ),
        tmp_path / "preview.wav",
        external_synthesizers={"cosyvoice": failed_cosyvoice},
    )

    assert result.suffix == ".mp3"
    assert result.is_file()


def test_dubbing_video_timing_filter_stretches_motion_without_freeze() -> None:
    value = DubbingService._video_timing_filter(3.0, 4.2)

    assert "setpts=1.400000*PTS" in value
    assert "minterpolate=fps=24" in value
    assert "tpad=stop_mode=clone" not in value
