"""Shot-video rendering and episode preview composition.

The first production profile is a deterministic FFmpeg-based comic-motion
renderer.  It produces real, standardized MP4 artifacts now while keeping the
render contract independent from the server-side MiniMax H3 FL2VA adapter.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from uuid import uuid4

import imageio_ffmpeg

from app.core.files import atomic_write_json, atomic_write_text, sha256_text
from app.database.db import init_db
from app.domain.jobs import JobStatus
from app.domain.video import EpisodeClipSpec, VideoArtifactMetadata, VideoRenderSpec
from app.services.artifact_service import register_artifact
from app.services.job_service import (
    create_job,
    heartbeat_job,
    transition_job,
)


@dataclass(frozen=True, slots=True)
class MotionPreset:
    preset_id: str
    label: str
    description: str


MOTION_PRESETS: dict[str, MotionPreset] = {
    "auto": MotionPreset(
        "auto",
        "自动运镜",
        "根据分镜运镜字段选择；缺省使用轻微推近。",
    ),
    "slow_push": MotionPreset(
        "slow_push",
        "缓慢推近",
        "稳定向主体缓慢推进，适合人物、情绪和对白镜头。",
    ),
    "slow_pull": MotionPreset(
        "slow_pull",
        "缓慢拉远",
        "从人物或细节缓慢后退，适合揭示环境和关系。",
    ),
    "pan_left": MotionPreset(
        "pan_left",
        "向左平移",
        "画面从右向左平稳移动，适合展示场景和人物关系。",
    ),
    "pan_right": MotionPreset(
        "pan_right",
        "向右平移",
        "画面从左向右平稳移动，适合环境揭示。",
    ),
    "tilt_up": MotionPreset(
        "tilt_up",
        "向上摇镜",
        "从画面下部平稳移动到上部，适合人物登场。",
    ),
    "tilt_down": MotionPreset(
        "tilt_down",
        "向下摇镜",
        "从画面上部平稳移动到下部，适合揭示动作或物件。",
    ),
    "still": MotionPreset(
        "still",
        "稳定静帧",
        "保持构图，仅进行极轻微呼吸式缩放。",
    ),
}


@dataclass(slots=True)
class VideoRuntimeStatus:
    available: bool
    ffmpeg_path: Path | None = None
    ffmpeg_version: str = ""
    message: str = ""


@dataclass(slots=True)
class VideoClipResult:
    episode_number: int
    shot_number: int
    video_path: Path
    manifest_path: Path
    source_image: Path
    elapsed_seconds: float
    candidate_index: int = 1


@dataclass(slots=True)
class VideoBatchResult:
    clips: list[VideoClipResult] = field(default_factory=list)
    job_id: str = ""
    elapsed_seconds: float = 0


@dataclass(slots=True)
class EpisodeComposeResult:
    episode_number: int
    video_path: Path
    manifest_path: Path
    clip_count: int
    job_id: str
    elapsed_seconds: float


class VideoRenderService:
    """Generate standardized MP4 clips from reviewed storyboard keyframes."""

    def __init__(self, ffmpeg_executable: str | Path | None = None) -> None:
        self.ffmpeg_executable = Path(ffmpeg_executable or imageio_ffmpeg.get_ffmpeg_exe())
        self._version = ""

    def check_status(self) -> VideoRuntimeStatus:
        if not self.ffmpeg_executable.is_file():
            return VideoRuntimeStatus(
                available=False,
                message="本地视频编码器未安装",
            )
        try:
            process = self._run(
                [str(self.ffmpeg_executable), "-version"],
                timeout=20,
                check=False,
            )
            version = (process.stdout or process.stderr).splitlines()[0]
        except Exception as exc:
            return VideoRuntimeStatus(
                available=False,
                ffmpeg_path=self.ffmpeg_executable,
                message=f"本地视频编码器不可用：{exc}",
            )
        self._version = version
        return VideoRuntimeStatus(
            available=True,
            ffmpeg_path=self.ffmpeg_executable,
            ffmpeg_version=version,
            message="漫画动效视频引擎已就绪",
        )

    def keyframe_continuity_score(self, start: Path, end: Path) -> float:
        """Return FFmpeg SSIM for two keyframes, or zero when comparison fails."""

        start_path = Path(start).resolve()
        end_path = Path(end).resolve()
        if not start_path.is_file() or not end_path.is_file():
            return 0.0
        process = subprocess.run(
            [
                str(self.ffmpeg_executable),
                "-hide_banner",
                "-i",
                str(start_path),
                "-i",
                str(end_path),
                "-lavfi",
                "[0:v][1:v]ssim",
                "-f",
                "null",
                "-",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
            check=False,
        )
        match = re.search(r"\bAll:([0-9.]+)", process.stderr)
        return float(match.group(1)) if match else 0.0

    def keyframe_layout_score(self, start: Path, end: Path) -> float:
        """Compare coarse composition while ignoring generative texture changes."""

        start_path = Path(start).resolve()
        end_path = Path(end).resolve()
        if not start_path.is_file() or not end_path.is_file():
            return 0.0
        process = subprocess.run(
            [
                str(self.ffmpeg_executable),
                "-hide_banner",
                "-i",
                str(start_path),
                "-i",
                str(end_path),
                "-lavfi",
                (
                    "[0:v]scale=208:120,gblur=sigma=3[start];"
                    "[1:v]scale=208:120,gblur=sigma=3[end];"
                    "[start][end]ssim"
                ),
                "-f",
                "null",
                "-",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
            check=False,
        )
        match = re.search(r"\bAll:([0-9.]+)", process.stderr)
        return float(match.group(1)) if match else 0.0

    def generate_clips(
        self,
        project_root: Path,
        specs: list[VideoRenderSpec],
        *,
        progress_callback: Callable[[int, str], None] | None = None,
    ) -> VideoBatchResult:
        if not specs:
            raise ValueError("请至少选择一个有首帧的镜头")
        root = Path(project_root).resolve()
        init_db(root / "database" / "world.db")
        serialized = json.dumps(
            [spec.model_dump(mode="json") for spec in specs],
            ensure_ascii=False,
            sort_keys=True,
        )
        job = create_job(
            "video_generate",
            payload={"specs": [spec.model_dump(mode="json") for spec in specs]},
            input_hash=sha256_text(serialized),
            reuse_existing=False,
        )
        transition_job(job.id, JobStatus.RUNNING)
        started = time.monotonic()
        results: list[VideoClipResult] = []

        def report(percent: int, message: str) -> None:
            if progress_callback:
                progress_callback(max(0, min(percent, 100)), message)

        try:
            status = self.check_status()
            if not status.available:
                raise RuntimeError(status.message)
            unsupported = {
                spec.engine_profile for spec in specs if spec.engine_profile != "comic_motion"
            }
            if unsupported:
                labels = "、".join(sorted(unsupported))
                raise RuntimeError(
                    f"视频引擎 {labels} 需通过对应的本地适配器生成；"
                    "本地支持 minimax_h3_fl2va（以及 comic_motion 漫画动效）。"
                )
            report(2, "视频编码器已就绪")
            total = len(specs)
            for index, spec in enumerate(specs, start=1):
                source = self._project_file(root, spec.source_image, "首帧")
                report(
                    4 + int((index - 1) / total * 90),
                    f"正在生成镜头 {spec.shot_number:02d}（{index}/{total}）",
                )
                result = self._render_spec(root, source, spec, job.id)
                results.append(result)
                heartbeat_job(job.id, 0.04 + index / total * 0.91)
            elapsed = time.monotonic() - started
            payload = {
                "clips": [item.video_path.relative_to(root).as_posix() for item in results],
                "elapsed_seconds": elapsed,
            }
            transition_job(job.id, JobStatus.SUCCEEDED, result=payload)
            report(100, f"已生成 {len(results)} 个镜头视频")
            return VideoBatchResult(
                clips=results,
                job_id=job.id,
                elapsed_seconds=elapsed,
            )
        except Exception as exc:
            transition_job(
                job.id,
                JobStatus.FAILED,
                error_code="video_render_failed",
                error_message=str(exc),
            )
            raise

    def compose_episode(
        self,
        project_root: Path,
        episode_number: int,
        clips: list[Path | EpisodeClipSpec],
        *,
        progress_callback: Callable[[int, str], None] | None = None,
    ) -> EpisodeComposeResult:
        if not clips:
            raise ValueError("当前剧集还没有可合成的镜头视频")
        root = Path(project_root).resolve()
        timeline = self._resolve_timeline(root, clips)
        resolved = [item.path for item in timeline]
        init_db(root / "database" / "world.db")
        job = create_job(
            "episode_compose",
            payload={
                "episode_number": episode_number,
                "clips": [path.relative_to(root).as_posix() for path in resolved],
                "transitions": [
                    {
                        "shot_number": item.shot_number,
                        "transition_out": item.transition_out,
                        "transition_frames": item.transition_frames,
                    }
                    for item in timeline
                ],
            },
            input_hash=sha256_text(
                "|".join(f"{path}:{path.stat().st_mtime_ns}" for path in resolved)
            ),
            reuse_existing=False,
        )
        transition_job(job.id, JobStatus.RUNNING)
        started = time.monotonic()
        output_dir = root / "production" / "videos" / f"episode_{episode_number:03d}"
        output_dir.mkdir(parents=True, exist_ok=True)
        run_id = self._run_id()
        destination = output_dir / f"episode_{episode_number:03d}_preview_{run_id}.mp4"
        temporary = destination.with_suffix(".rendering.mp4")
        concat_path = output_dir / f".concat_{job.id}.txt"
        lines = []
        for path in resolved:
            absolute = path.as_posix()
            lines.append(f"file '{absolute.replace(chr(39), chr(39) * 2)}'")
        atomic_write_text(concat_path, "\n".join(lines) + "\n")

        def report(percent: int, message: str) -> None:
            if progress_callback:
                progress_callback(max(0, min(percent, 100)), message)

        try:
            report(8, f"正在合成 {len(resolved)} 个镜头")
            include_audio = all(self._has_audio_stream(path) for path in resolved)
            if self._timeline_has_visual_transitions(timeline):
                command = self._transition_compose_command(
                    timeline,
                    temporary,
                    include_audio=include_audio,
                )
            else:
                command = [
                    str(self.ffmpeg_executable),
                    "-y",
                    "-loglevel",
                    "error",
                    "-f",
                    "concat",
                    "-safe",
                    "0",
                    "-i",
                    str(concat_path),
                    "-vf",
                    (
                        "scale=1280:720:force_original_aspect_ratio=decrease,"
                        "pad=1280:720:(ow-iw)/2:(oh-ih)/2:black,format=yuv420p"
                    ),
                    "-r",
                    "24",
                    "-c:v",
                    "libx264",
                    "-preset",
                    "medium",
                    "-crf",
                    "18",
                ]
                if include_audio:
                    command.extend(
                        [
                            "-c:a",
                            "aac",
                            "-b:a",
                            "192k",
                            "-ar",
                            "48000",
                            "-ac",
                            "2",
                        ]
                    )
                else:
                    command.append("-an")
                command.extend(
                    [
                        "-movflags",
                        "+faststart",
                        str(temporary),
                    ]
                )
            self._run(command, timeout=1800)
            temporary.replace(destination)
            elapsed = time.monotonic() - started
            manifest_path = output_dir / f"episode_manifest_{run_id}.json"
            manifest = {
                "schema_version": "1.1",
                "kind": "episode_preview",
                "episode_number": episode_number,
                "engine_profile": "timeline_composer",
                "clips": [path.relative_to(root).as_posix() for path in resolved],
                "transitions": [
                    {
                        "shot_number": item.shot_number,
                        "transition_out": item.transition_out,
                        "transition_frames": item.transition_frames,
                    }
                    for item in timeline
                ],
                "output_file": destination.relative_to(root).as_posix(),
                "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                "elapsed_seconds": round(elapsed, 3),
                "job_id": job.id,
                "width": 1280,
                "height": 720,
                "fps": 24,
                "audio": include_audio,
            }
            atomic_write_json(manifest_path, manifest)
            register_artifact(
                root,
                destination,
                kind="episode_preview_video",
                job_id=job.id,
                metadata=manifest,
            )
            transition_job(
                job.id,
                JobStatus.SUCCEEDED,
                result={"video": destination.relative_to(root).as_posix()},
            )
            report(100, "整集原生音视频预览已合成")
            return EpisodeComposeResult(
                episode_number=episode_number,
                video_path=destination,
                manifest_path=manifest_path,
                clip_count=len(resolved),
                job_id=job.id,
                elapsed_seconds=elapsed,
            )
        except Exception as exc:
            temporary.unlink(missing_ok=True)
            transition_job(
                job.id,
                JobStatus.FAILED,
                error_code="episode_compose_failed",
                error_message=str(exc),
            )
            raise
        finally:
            concat_path.unlink(missing_ok=True)

    def _render_spec(
        self,
        root: Path,
        source: Path,
        spec: VideoRenderSpec,
        job_id: str,
    ) -> VideoClipResult:
        started = time.monotonic()
        output_dir = (
            root
            / "production"
            / "videos"
            / f"episode_{spec.episode_number:03d}"
            / f"shot_{spec.shot_number:03d}"
        )
        output_dir.mkdir(parents=True, exist_ok=True)
        run_id = self._run_id()
        destination = output_dir / f"shot_{spec.shot_number:03d}_{run_id}.mp4"
        temporary = destination.with_suffix(".rendering.mp4")
        frame_count = max(1, round(spec.duration_seconds * spec.fps))
        preset = self._resolve_motion_preset(spec.camera_movement)
        filter_graph = self._motion_filter(
            preset,
            spec.width,
            spec.height,
            spec.fps,
            frame_count,
        )
        command = [
            str(self.ffmpeg_executable),
            "-y",
            "-loglevel",
            "error",
            "-loop",
            "1",
            "-framerate",
            str(spec.fps),
            "-i",
            str(source),
            "-vf",
            filter_graph,
            "-frames:v",
            str(frame_count),
            "-an",
            "-c:v",
            "libx264",
            "-preset",
            "medium",
            "-crf",
            "18",
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "+faststart",
            str(temporary),
        ]
        try:
            self._run(command, timeout=900)
            temporary.replace(destination)
        except Exception:
            temporary.unlink(missing_ok=True)
            raise
        elapsed = time.monotonic() - started
        manifest_path = output_dir / f"manifest_{run_id}.json"
        metadata = VideoArtifactMetadata(
            engine_profile=spec.engine_profile,
            episode_number=spec.episode_number,
            shot_number=spec.shot_number,
            source_image=source.relative_to(root).as_posix(),
            end_image=(
                self._project_file(root, spec.end_image, "结束帧").relative_to(root).as_posix()
                if spec.end_image
                else ""
            ),
            output_file=destination.relative_to(root).as_posix(),
            subject_motion=spec.subject_motion,
            environment_motion=spec.environment_motion,
            continuity_constraints=spec.continuity_constraints,
            negative_prompt=spec.negative_prompt,
            motion_prompt=spec.motion_prompt,
            camera_movement=preset,
            motion_strength=spec.motion_strength,
            screen_direction=spec.screen_direction,
            transition_out=spec.transition_out,
            transition_frames=spec.transition_frames,
            handle_frames=spec.handle_frames,
            candidate_count=spec.candidate_count,
            duration_seconds=spec.duration_seconds,
            fps=spec.fps,
            width=spec.width,
            height=spec.height,
            generated_at=datetime.now().astimezone().isoformat(timespec="seconds"),
            elapsed_seconds=round(elapsed, 3),
            job_id=job_id,
            ffmpeg_version=self._version,
        )
        manifest = metadata.model_dump(mode="json")
        atomic_write_json(manifest_path, manifest)
        register_artifact(
            root,
            destination,
            kind="shot_video",
            job_id=job_id,
            metadata=manifest,
        )
        return VideoClipResult(
            episode_number=spec.episode_number,
            shot_number=spec.shot_number,
            video_path=destination,
            manifest_path=manifest_path,
            source_image=source,
            elapsed_seconds=elapsed,
        )

    def _resolve_timeline(
        self,
        root: Path,
        clips: list[Path | EpisodeClipSpec],
    ) -> list[EpisodeClipSpec]:
        timeline: list[EpisodeClipSpec] = []
        for index, value in enumerate(clips, start=1):
            if isinstance(value, EpisodeClipSpec):
                path = self._project_file(root, value.path, "镜头视频")
                probed_duration = self._probe_duration(path)
                duration = (
                    min(value.duration_seconds, probed_duration)
                    if probed_duration
                    else value.duration_seconds
                )
                timeline.append(
                    value.model_copy(
                        update={
                            "path": path,
                            "duration_seconds": duration,
                        }
                    )
                )
            else:
                path = self._project_file(root, value, "镜头视频")
                timeline.append(
                    EpisodeClipSpec(
                        path=path,
                        shot_number=index,
                        duration_seconds=self._probe_duration(path) or 3.0,
                        transition_out="dissolve" if index < len(clips) else "cut",
                        transition_frames=8 if index < len(clips) else 0,
                    )
                )
        return timeline

    @staticmethod
    def _timeline_has_visual_transitions(timeline: list[EpisodeClipSpec]) -> bool:
        return any(
            item.transition_frames > 0
            and item.transition_out in {"match_cut", "dissolve", "fade_black"}
            for item in timeline[:-1]
        )

    def _transition_compose_command(
        self,
        timeline: list[EpisodeClipSpec],
        destination: Path,
        *,
        include_audio: bool = False,
    ) -> list[str]:
        command = [
            str(self.ffmpeg_executable),
            "-y",
            "-loglevel",
            "error",
        ]
        for item in timeline:
            command.extend(["-i", str(item.path)])

        filters: list[str] = []
        for index, item in enumerate(timeline):
            filters.append(
                f"[{index}:v]"
                "scale=1280:720:force_original_aspect_ratio=decrease,"
                "pad=1280:720:(ow-iw)/2:(oh-ih)/2:black,"
                f"trim=duration={item.duration_seconds:.6f},"
                "setpts=PTS-STARTPTS,fps=24,settb=1/24,format=yuv420p"
                f"[v{index}]"
            )
            if include_audio:
                filters.append(
                    f"[{index}:a]aresample=48000,"
                    f"atrim=duration={item.duration_seconds:.6f},"
                    f"asetpts=PTS-STARTPTS[a{index}]"
                )

        current_label = "v0"
        current_audio_label = "a0"
        current_duration = timeline[0].duration_seconds
        for index in range(1, len(timeline)):
            previous = timeline[index - 1]
            output_label = f"timeline{index}"
            transition_seconds = min(
                previous.transition_frames / 24,
                current_duration / 2,
                timeline[index].duration_seconds / 2,
            )
            if (
                previous.transition_out in {"match_cut", "dissolve", "fade_black"}
                and transition_seconds > 0
            ):
                effect = (
                    "fadeblack"
                    if previous.transition_out == "fade_black"
                    else "fadefast"
                    if previous.transition_out == "match_cut"
                    else "fade"
                )
                offset = max(0.0, current_duration - transition_seconds)
                filters.append(
                    f"[{current_label}][v{index}]"
                    f"xfade=transition={effect}:"
                    f"duration={transition_seconds:.6f}:offset={offset:.6f}"
                    ",fps=24,settb=1/24,format=yuv420p"
                    f"[{output_label}]"
                )
                if include_audio:
                    audio_output_label = f"audio{index}"
                    filters.append(
                        f"[{current_audio_label}][a{index}]"
                        f"acrossfade=d={transition_seconds:.6f}:"
                        "c1=tri:c2=tri"
                        f"[{audio_output_label}]"
                    )
                    current_audio_label = audio_output_label
                current_duration += timeline[index].duration_seconds - transition_seconds
            else:
                filters.append(
                    f"[{current_label}][v{index}]"
                    "concat=n=2:v=1:a=0,"
                    "fps=24,settb=1/24,format=yuv420p"
                    f"[{output_label}]"
                )
                if include_audio:
                    audio_output_label = f"audio{index}"
                    filters.append(
                        f"[{current_audio_label}][a{index}]concat=n=2:v=0:a=1[{audio_output_label}]"
                    )
                    current_audio_label = audio_output_label
                current_duration += timeline[index].duration_seconds
            current_label = output_label

        command.extend(
            [
                "-filter_complex",
                ";".join(filters),
                "-map",
                f"[{current_label}]",
                "-c:v",
                "libx264",
                "-preset",
                "medium",
                "-crf",
                "18",
                "-pix_fmt",
                "yuv420p",
            ]
        )
        if include_audio:
            command.extend(
                [
                    "-map",
                    f"[{current_audio_label}]",
                    "-c:a",
                    "aac",
                    "-b:a",
                    "192k",
                    "-ar",
                    "48000",
                    "-ac",
                    "2",
                ]
            )
        else:
            command.append("-an")
        command.extend(
            [
                "-movflags",
                "+faststart",
                str(destination),
            ]
        )
        return command

    def _probe_duration(self, path: Path) -> float | None:
        process = self._run(
            [
                str(self.ffmpeg_executable),
                "-hide_banner",
                "-i",
                str(path),
            ],
            timeout=30,
            check=False,
        )
        match = re.search(
            r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)",
            process.stderr or process.stdout,
        )
        if not match:
            return None
        hours, minutes, seconds = match.groups()
        return int(hours) * 3600 + int(minutes) * 60 + float(seconds)

    def _has_audio_stream(self, path: Path) -> bool:
        process = self._run(
            [str(self.ffmpeg_executable), "-hide_banner", "-i", str(path)],
            timeout=30,
            check=False,
        )
        output = process.stderr or process.stdout
        return bool(re.search(r"Stream #\S+.*Audio:", output))

    def normalize_native_audio(
        self,
        path: Path,
        *,
        target_lufs: float = -24.0,
    ) -> bool:
        """Raise quiet generated ambience/music without re-encoding video."""

        source = Path(path).resolve()
        if not source.is_file() or not self._has_audio_stream(source):
            return False
        target = max(-36.0, min(float(target_lufs), -14.0))
        temporary = source.with_name(f".{source.stem}.audio_normalizing{source.suffix}")
        try:
            self._run(
                [
                    str(self.ffmpeg_executable),
                    "-y",
                    "-loglevel",
                    "error",
                    "-i",
                    str(source),
                    "-map",
                    "0:v:0",
                    "-map",
                    "0:a:0",
                    "-map_metadata",
                    "0",
                    "-c:v",
                    "copy",
                    "-af",
                    f"loudnorm=I={target:.1f}:TP=-2:LRA=14",
                    "-c:a",
                    "aac",
                    "-b:a",
                    "192k",
                    "-ar",
                    "48000",
                    "-ac",
                    "2",
                    "-movflags",
                    "+faststart",
                    str(temporary),
                ],
                timeout=300,
            )
            temporary.replace(source)
            return True
        finally:
            temporary.unlink(missing_ok=True)

    @staticmethod
    def _motion_filter(
        preset: str,
        width: int,
        height: int,
        fps: int,
        frame_count: int,
    ) -> str:
        base = f"scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height},"
        last = max(frame_count - 1, 1)
        if preset == "pan_left":
            zoom = f"z=1.08:x='(iw-iw/zoom)*on/{last}':y='ih/2-(ih/zoom/2)'"
        elif preset == "pan_right":
            zoom = f"z=1.08:x='(iw-iw/zoom)*(1-on/{last})':y='ih/2-(ih/zoom/2)'"
        elif preset == "tilt_up":
            zoom = f"z=1.08:x='iw/2-(iw/zoom/2)':y='(ih-ih/zoom)*(1-on/{last})'"
        elif preset == "tilt_down":
            zoom = f"z=1.08:x='iw/2-(iw/zoom/2)':y='(ih-ih/zoom)*on/{last}'"
        elif preset == "slow_pull":
            zoom = f"z='1.08-0.07*on/{last}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
        elif preset == "still":
            zoom = "z='min(zoom+0.00008,1.015)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
        else:
            zoom = "z='min(zoom+0.00065,1.08)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
        return f"{base}zoompan={zoom}:d=1:s={width}x{height}:fps={fps},format=yuv420p"

    @staticmethod
    def _resolve_motion_preset(value: str) -> str:
        normalized = (value or "").strip().lower()
        if normalized in MOTION_PRESETS and normalized != "auto":
            return normalized
        if any(marker in normalized for marker in ("pan", "平移", "横移")):
            return "pan_right"
        if any(marker in normalized for marker in ("tilt", "摇", "crane", "升")):
            return "tilt_up"
        if any(marker in normalized for marker in ("pull", "拉远", "后退")):
            return "slow_pull"
        if any(marker in normalized for marker in ("static", "固定", "静止")):
            return "still"
        return "slow_push"

    @staticmethod
    def _project_file(root: Path, path: Path, label: str) -> Path:
        candidate = Path(path).resolve()
        try:
            candidate.relative_to(root)
        except ValueError as exc:
            raise ValueError(f"{label}必须位于当前项目目录内") from exc
        if not candidate.is_file():
            raise FileNotFoundError(f"{label}不存在：{candidate}")
        return candidate

    @staticmethod
    def _run_id() -> str:
        return f"{datetime.now():%Y%m%d_%H%M%S}_{uuid4().hex[:6]}"

    @staticmethod
    def _run(
        command: list[str],
        *,
        timeout: int,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        creationflags = 0
        if sys.platform == "win32":
            creationflags = subprocess.CREATE_NO_WINDOW
        process = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            creationflags=creationflags,
            check=False,
        )
        if check and process.returncode:
            detail = (process.stderr or process.stdout).strip()
            raise RuntimeError(detail[-2000:] or f"FFmpeg 退出码 {process.returncode}")
        return process
