"""Speech synthesis, subtitle creation, and dubbed episode composition."""

from __future__ import annotations

import asyncio
import re
import subprocess
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import imageio_ffmpeg

from app.core.files import atomic_write_json, atomic_write_text, sha256_text
from app.core.naming import numeric_run_id, pinyin_slug
from app.database.db import init_db
from app.domain.audio import DubbingArtifactMetadata, DubbingLineSpec
from app.domain.jobs import JobStatus
from app.services.artifact_service import register_artifact
from app.services.job_service import create_job, heartbeat_job, transition_job


@dataclass(frozen=True, slots=True)
class VoicePreset:
    voice_id: str
    label: str
    description: str


VOICE_PRESETS: dict[str, VoicePreset] = {
    "zh-CN-YunyangNeural": VoicePreset(
        "zh-CN-YunyangNeural",
        "云扬·沉稳男旁白",
        "适合剧情解说、世界观和正式旁白",
    ),
    "zh-CN-YunxiNeural": VoicePreset(
        "zh-CN-YunxiNeural",
        "云希·阳光青年男声",
        "适合年轻男主和轻快对白",
    ),
    "zh-CN-YunjianNeural": VoicePreset(
        "zh-CN-YunjianNeural",
        "云健·有力青年男声",
        "适合强势男角、冲突和战斗对白",
    ),
    "zh-CN-YunxiaNeural": VoicePreset(
        "zh-CN-YunxiaNeural",
        "云夏·少年男声",
        "适合少年角色",
    ),
    "zh-CN-XiaoxiaoNeural": VoicePreset(
        "zh-CN-XiaoxiaoNeural",
        "晓晓·温暖女声",
        "适合女主、温柔对白和女旁白",
    ),
    "zh-CN-XiaoyiNeural": VoicePreset(
        "zh-CN-XiaoyiNeural",
        "晓伊·活泼女声",
        "适合年轻女性和轻快对白",
    ),
}
DEFAULT_VOICE_ID = "zh-CN-YunyangNeural"


@dataclass(slots=True)
class DubbingRuntimeStatus:
    available: bool
    engine: str = "edge_tts"
    ffmpeg_version: str = ""
    message: str = ""


@dataclass(slots=True)
class DubbingLineResult:
    episode_number: int
    shot_number: int
    audio_path: Path
    subtitle_path: Path
    manifest_path: Path
    source_video: Path
    text: str
    speaker: str
    voice_id: str
    engine: str
    fallback_reason: str
    audio_duration_seconds: float
    source_video_duration_seconds: float
    timeline_duration_seconds: float
    source_has_audio: bool = False


@dataclass(slots=True)
class DubbingComposeResult:
    episode_number: int
    video_path: Path
    subtitle_path: Path
    manifest_path: Path
    lines: list[DubbingLineResult] = field(default_factory=list)
    job_id: str = ""
    elapsed_seconds: float = 0.0


class DubbingService:
    """Create per-shot speech and a subtitle-burned episode with an AAC track."""

    def __init__(self, ffmpeg_executable: str | Path | None = None) -> None:
        self.ffmpeg_executable = Path(
            ffmpeg_executable or imageio_ffmpeg.get_ffmpeg_exe()
        )

    def check_status(self) -> DubbingRuntimeStatus:
        if not self.ffmpeg_executable.is_file():
            return DubbingRuntimeStatus(False, message="本地 FFmpeg 不可用")
        try:
            import edge_tts  # noqa: F401
        except ImportError:
            return DubbingRuntimeStatus(
                False,
                message="缺少 edge-tts，请重新安装应用依赖",
            )
        process = self._run(
            [str(self.ffmpeg_executable), "-version"],
            timeout=20,
            check=False,
        )
        version = (process.stdout or process.stderr).splitlines()[0]
        return DubbingRuntimeStatus(
            True,
            ffmpeg_version=version,
            message="在线中文配音与本地音画合成可用",
        )

    def synthesize_preview(
        self,
        spec: DubbingLineSpec,
        destination: Path,
        *,
        external_synthesizers: Mapping[
            str,
            Callable[[DubbingLineSpec, Path, Path], None],
        ]
        | None = None,
    ) -> Path:
        """Synthesize one editable line without composing a video."""

        destination = Path(destination).resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)
        subtitle_path = destination.with_suffix(".srt")
        synthesizers = dict(external_synthesizers or {})
        try:
            if spec.engine == "edge_tts":
                self._synthesize_edge(spec, destination, subtitle_path)
            elif spec.engine in synthesizers:
                synthesizers[spec.engine](spec, destination, subtitle_path)
            else:
                raise RuntimeError(f"配音引擎 {spec.engine} 没有可用的适配器")
        except Exception:
            if spec.engine == "edge_tts" or not spec.fallback_to_edge:
                raise
            destination.unlink(missing_ok=True)
            destination = destination.with_suffix(".mp3")
            subtitle_path = destination.with_suffix(".srt")
            self._synthesize_edge(spec, destination, subtitle_path)
        duration = self._media_duration(destination)
        if not subtitle_path.is_file() or not subtitle_path.stat().st_size:
            self._write_line_subtitle(subtitle_path, spec.text, duration)
        return destination

    def dub_episode(
        self,
        project_root: Path,
        episode_number: int,
        specs: list[DubbingLineSpec],
        *,
        progress_callback: Callable[[int, str], None] | None = None,
        external_synthesizers: Mapping[
            str,
            Callable[[DubbingLineSpec, Path, Path], None],
        ]
        | None = None,
        visible_ai_label: bool = False,
    ) -> DubbingComposeResult:
        if not specs:
            raise ValueError("当前剧集没有可配音的镜头")
        root = Path(project_root).resolve()
        init_db(root / "database" / "world.db")
        payload = [spec.model_dump(mode="json") for spec in specs]
        job = create_job(
            "episode_dubbing",
            payload={"episode_number": episode_number, "lines": payload},
            input_hash=sha256_text(str(payload)),
            reuse_existing=False,
        )
        transition_job(job.id, JobStatus.RUNNING)
        started = time.monotonic()
        run_id = numeric_run_id()
        package_name = f"{pinyin_slug(root.name)}_{episode_number:03d}"
        package_root = root / "outputs" / "episodes" / package_name
        audio_dir = package_root / "yinpin"
        subtitle_dir = package_root / "zimu"
        line_subtitle_dir = subtitle_dir / "fenduan"
        video_dir = package_root / "shipin"
        manifest_dir = package_root / "qingdan"
        line_manifest_dir = manifest_dir / "fenduan"
        temp_dir = root / "production" / "temp" / "dubbing" / run_id
        for path in (
            audio_dir,
            subtitle_dir,
            line_subtitle_dir,
            video_dir,
            manifest_dir,
            line_manifest_dir,
        ):
            path.mkdir(parents=True, exist_ok=True)
        temp_dir.mkdir(parents=True, exist_ok=True)
        results: list[DubbingLineResult] = []
        synthesizers = dict(external_synthesizers or {})

        def report(percent: int, message: str) -> None:
            if progress_callback:
                progress_callback(max(0, min(percent, 100)), message)

        try:
            status = self.check_status()
            if not status.available:
                raise RuntimeError(status.message)
            report(2, "配音引擎与 FFmpeg 已就绪")
            for index, spec in enumerate(specs, start=1):
                if (
                    spec.mode != "mute"
                    and spec.engine != "edge_tts"
                    and spec.engine not in synthesizers
                    and not spec.fallback_to_edge
                ):
                    raise RuntimeError(
                        f"配音引擎 {spec.engine} 没有可用的服务器适配器"
                    )
                if spec.mode != "mute" and not spec.text.strip():
                    raise ValueError(
                        f"镜头 {spec.shot_number:02d} 没有可生成的配音文案"
                    )
                source_video = self._project_file(
                    root,
                    spec.source_video,
                    "镜头视频",
                )
                speaker_slug = pinyin_slug(
                    spec.speaker or "旁白",
                    fallback="pangbai",
                )
                stem = (
                    f"{speaker_slug}_{episode_number:03d}_"
                    f"{spec.shot_number:03d}_{run_id}"
                )
                audio_path = audio_dir / (
                    f"{stem}.wav" if spec.engine == "cosyvoice" else f"{stem}.mp3"
                )
                subtitle_path = line_subtitle_dir / f"{stem}.srt"
                manifest_path = line_manifest_dir / f"{stem}.json"
                report(
                    3 + int((index - 1) / len(specs) * 55),
                    f"正在生成镜头 {spec.shot_number:02d} 配音",
                )
                actual_engine = spec.engine
                fallback_reason = ""
                if spec.mode == "mute":
                    self._synthesize_silence(
                        self._media_duration(source_video),
                        audio_path,
                        subtitle_path,
                    )
                elif spec.prepared_audio is not None:
                    audio_path = self._project_file(
                        root,
                        spec.prepared_audio,
                        "Prepared lip-sync audio",
                    )
                    prepared_subtitle = audio_path.with_suffix(".srt")
                    if prepared_subtitle.is_file():
                        subtitle_path = prepared_subtitle
                else:
                    try:
                        if spec.engine == "edge_tts":
                            self._synthesize_edge(
                                spec,
                                audio_path,
                                subtitle_path,
                            )
                        else:
                            synthesizers[spec.engine](
                                spec,
                                audio_path,
                                subtitle_path,
                            )
                    except Exception as exc:
                        if spec.engine == "edge_tts" or not spec.fallback_to_edge:
                            raise
                        fallback_reason = str(exc).splitlines()[0]
                        actual_engine = "edge_tts"
                        audio_path.unlink(missing_ok=True)
                        audio_path = audio_dir / f"{stem}.mp3"
                        report(
                            3 + int((index - 1) / len(specs) * 55),
                            (
                                f"镜头 {spec.shot_number:02d} 本地音色失败，"
                                "已自动回退 Edge TTS"
                            ),
                        )
                        self._synthesize_edge(
                            spec,
                            audio_path,
                            subtitle_path,
                        )
                audio_duration = self._media_duration(audio_path)
                if (
                    spec.mode != "mute"
                    and (
                        not subtitle_path.is_file()
                        or not subtitle_path.read_text(
                            encoding="utf-8",
                            errors="ignore",
                        ).strip()
                    )
                ):
                    self._write_line_subtitle(
                        subtitle_path,
                        spec.text,
                        audio_duration,
                    )
                video_duration = self._media_duration(source_video)
                source_has_audio = (
                    spec.preserve_source_audio
                    and self._has_audio_stream(source_video)
                )
                timeline_duration = (
                    video_duration
                    if spec.mode == "mute"
                    else max(
                        video_duration,
                        audio_duration
                        + spec.lead_seconds
                        + spec.tail_seconds,
                    )
                )
                metadata = DubbingArtifactMetadata(
                    kind="shot_speech",
                    engine=actual_engine,
                    episode_number=episode_number,
                    shot_number=spec.shot_number,
                    speaker=spec.speaker,
                    text=spec.text,
                    voice_id=spec.voice_id,
                    rate=spec.rate,
                    volume=spec.volume,
                    pitch=spec.pitch,
                    source_video=source_video.relative_to(root).as_posix(),
                    audio_file=audio_path.relative_to(root).as_posix(),
                    subtitle_file=subtitle_path.relative_to(root).as_posix(),
                    generated_at=datetime.now()
                    .astimezone()
                    .isoformat(timespec="seconds"),
                    duration_seconds=audio_duration,
                    job_id=job.id,
                    requested_engine=spec.engine,
                    fallback_reason=fallback_reason,
                    reference_audio=(
                        str(spec.reference_audio) if spec.reference_audio else ""
                    ),
                    reference_text=spec.reference_text,
                    instruct_text=spec.instruct_text,
                    prepared_audio=(
                        audio_path.relative_to(root).as_posix()
                        if spec.prepared_audio is not None
                        else ""
                    ),
                    source_audio_preserved=source_has_audio,
                    source_audio_gain_db=spec.source_audio_gain_db,
                    ducking_gain_db=spec.ducking_gain_db,
                ).model_dump(mode="json")
                atomic_write_json(manifest_path, metadata)
                register_artifact(
                    root,
                    audio_path,
                    kind="shot_speech",
                    job_id=job.id,
                    metadata=metadata,
                )
                result = DubbingLineResult(
                    episode_number=episode_number,
                    shot_number=spec.shot_number,
                    audio_path=audio_path,
                    subtitle_path=subtitle_path,
                    manifest_path=manifest_path,
                    source_video=source_video,
                    text=spec.text,
                    speaker=spec.speaker,
                    voice_id=spec.voice_id,
                    engine=actual_engine,
                    fallback_reason=fallback_reason,
                    audio_duration_seconds=audio_duration,
                    source_video_duration_seconds=video_duration,
                    timeline_duration_seconds=timeline_duration,
                    source_has_audio=source_has_audio,
                )
                results.append(result)
                heartbeat_job(job.id, index / len(specs) * 0.6)

            report(62, "正在按语音长度适配镜头")
            segment_paths: list[Path] = []
            for index, (spec, result) in enumerate(
                zip(specs, results, strict=True),
                start=1,
            ):
                segment_path = temp_dir / f"segment_{index:03d}.mp4"
                self._render_segment(spec, result, segment_path)
                segment_paths.append(segment_path)
                report(
                    62 + int(index / len(results) * 18),
                    f"已适配 {index}/{len(results)} 个镜头",
                )
                heartbeat_job(job.id, 0.6 + index / len(results) * 0.2)

            episode_subtitle = (
                subtitle_dir / f"{package_name}_{run_id}_zimu.srt"
            )
            self._write_episode_subtitles(results, specs, episode_subtitle)
            base_video = temp_dir / "episode_with_audio.mp4"
            self._concat_segments(segment_paths, temp_dir, base_video)
            destination = (
                video_dir / f"{package_name}_{run_id}_chengpian.mp4"
            )
            report(84, "正在烧录字幕并输出带声成片")
            subtitles_present = any(
                spec.subtitle_enabled
                and spec.mode != "mute"
                and bool(spec.text.strip())
                for spec in specs
            )
            if subtitles_present:
                self._burn_subtitles(
                    base_video,
                    episode_subtitle,
                    destination,
                    visible_ai_label=visible_ai_label,
                )
            elif visible_ai_label:
                self._burn_ai_label(base_video, destination)
            else:
                base_video.replace(destination)

            elapsed = time.monotonic() - started
            manifest_path = (
                manifest_dir / f"{package_name}_{run_id}_qingdan.json"
            )
            manifest = DubbingArtifactMetadata(
                kind="episode_dubbed_video",
                engine=(
                    results[0].engine
                    if results
                    and all(item.engine == results[0].engine for item in results)
                    else "mixed"
                ),
                episode_number=episode_number,
                output_file=destination.relative_to(root).as_posix(),
                subtitle_file=episode_subtitle.relative_to(root).as_posix(),
                generated_at=datetime.now()
                .astimezone()
                .isoformat(timespec="seconds"),
                duration_seconds=sum(
                    item.timeline_duration_seconds for item in results
                ),
                job_id=job.id,
                lines=[
                    {
                        "shot_number": item.shot_number,
                        "speaker": item.speaker,
                        "text": item.text,
                        "voice_id": item.voice_id,
                        "engine": item.engine,
                        "rate": spec.rate,
                        "volume": spec.volume,
                        "pitch": spec.pitch,
                        "fallback_reason": item.fallback_reason,
                        "audio_file": item.audio_path.relative_to(root).as_posix(),
                        "subtitle_file": item.subtitle_path.relative_to(
                            root
                        ).as_posix(),
                        "audio_duration_seconds": round(
                            item.audio_duration_seconds,
                            3,
                        ),
                        "source_video_duration_seconds": round(
                            item.source_video_duration_seconds,
                            3,
                        ),
                        "video_time_scale": round(
                            item.timeline_duration_seconds
                            / max(item.source_video_duration_seconds, 0.001),
                            3,
                        ),
                        "timeline_duration_seconds": round(
                            item.timeline_duration_seconds,
                            3,
                        ),
                        "source_audio_preserved": item.source_has_audio,
                        "source_audio_gain_db": spec.source_audio_gain_db,
                        "ducking_gain_db": spec.ducking_gain_db,
                    }
                    for item, spec in zip(results, specs, strict=True)
                ],
                audio=True,
                subtitles_burned=subtitles_present,
                ai_generated=True,
                ai_content_label="AI生成内容" if visible_ai_label else "",
                visible_ai_label=visible_ai_label,
                elapsed_seconds=round(elapsed, 3),
            ).model_dump(mode="json")
            atomic_write_json(manifest_path, manifest)
            register_artifact(
                root,
                destination,
                kind="episode_dubbed_video",
                job_id=job.id,
                metadata=manifest,
            )
            transition_job(
                job.id,
                JobStatus.SUCCEEDED,
                result={"video": destination.relative_to(root).as_posix()},
            )
            report(100, "配音、字幕与带声成片已完成")
            return DubbingComposeResult(
                episode_number=episode_number,
                video_path=destination,
                subtitle_path=episode_subtitle,
                manifest_path=manifest_path,
                lines=results,
                job_id=job.id,
                elapsed_seconds=elapsed,
            )
        except Exception as exc:
            transition_job(
                job.id,
                JobStatus.FAILED,
                error_code="episode_dubbing_failed",
                error_message=str(exc),
            )
            raise
        finally:
            for path in temp_dir.glob("*"):
                if path.is_file():
                    path.unlink(missing_ok=True)
            temp_dir.rmdir()

    @staticmethod
    def _synthesize_edge(
        spec: DubbingLineSpec,
        audio_path: Path,
        subtitle_path: Path,
    ) -> None:
        import edge_tts

        async def synthesize() -> None:
            last_error: Exception | None = None
            for attempt in range(4):
                communicator = edge_tts.Communicate(
                    spec.text,
                    spec.voice_id,
                    rate=spec.rate,
                    volume=spec.volume,
                    pitch=spec.pitch,
                    connect_timeout=10,
                    receive_timeout=20,
                )
                subtitles = edge_tts.SubMaker()
                audio_path.unlink(missing_ok=True)
                subtitle_path.unlink(missing_ok=True)
                try:
                    async with asyncio.timeout(30):
                        with audio_path.open("wb") as audio_file:
                            async for chunk in communicator.stream():
                                if chunk["type"] == "audio":
                                    audio_file.write(chunk["data"])
                                elif chunk["type"] in (
                                    "WordBoundary",
                                    "SentenceBoundary",
                                ):
                                    subtitles.feed(chunk)
                    if audio_path.stat().st_size == 0:
                        raise RuntimeError("在线配音返回了空音频")
                    atomic_write_text(subtitle_path, subtitles.get_srt())
                    return
                except Exception as exc:
                    last_error = exc
                    audio_path.unlink(missing_ok=True)
                    subtitle_path.unlink(missing_ok=True)
                    if attempt < 3:
                        await asyncio.sleep(1.5 * (attempt + 1))
            if last_error is not None:
                raise last_error

        asyncio.run(synthesize())
        if not audio_path.is_file() or audio_path.stat().st_size == 0:
            raise RuntimeError("配音服务没有返回音频")

    def create_reference_seed(
        self,
        destination: Path,
        *,
        voice_id: str,
        text: str,
        rate: str = "+0%",
    ) -> Path:
        """Create a clean reference clip used to bootstrap local voice cloning."""

        destination = Path(destination).resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)
        subtitle_path = destination.with_suffix(".srt")
        spec = DubbingLineSpec(
            episode_number=1,
            shot_number=1,
            source_video=destination,
            text=text,
            voice_id=voice_id,
            rate=rate,
        )
        self._synthesize_edge(spec, destination, subtitle_path)
        return destination

    @classmethod
    def _write_line_subtitle(
        cls,
        destination: Path,
        text: str,
        duration_seconds: float,
    ) -> None:
        atomic_write_text(
            destination,
            (
                "1\n"
                f"{cls._srt_time(0)} --> "
                f"{cls._srt_time(max(0.2, duration_seconds))}\n"
                f"{text.strip()}\n"
            ),
        )

    def _render_segment(
        self,
        spec: DubbingLineSpec,
        result: DubbingLineResult,
        destination: Path,
    ) -> None:
        target = result.timeline_duration_seconds
        lead_ms = (
            0 if spec.mode == "mute" else round(spec.lead_seconds * 1000)
        )
        voice_filter = (
            "[1:a]"
            f"adelay={lead_ms}:all=1,"
            f"apad=pad_dur={target:.3f},atrim=duration={target:.3f},"
            "aresample=48000"
        )
        if spec.mode != "mute":
            voice_filter += ",loudnorm=I=-16:TP=-1.5:LRA=11"
        voice_filter += "[voice]"
        video_filter = self._video_timing_filter(
            result.source_video_duration_seconds,
            target,
        )
        if result.source_has_audio and spec.mode == "mute":
            audio_filter = (
                "[0:a]aresample=48000,"
                f"volume={spec.source_audio_gain_db:.2f}dB,"
                f"apad=pad_dur={target:.3f},atrim=duration={target:.3f},"
                "loudnorm=I=-18:TP=-1.5:LRA=14[a]"
            )
        elif result.source_has_audio:
            duck_ratio = max(
                2.0,
                min(20.0, abs(spec.ducking_gain_db) / 1.5),
            )
            audio_filter = (
                f"{voice_filter};"
                "[voice]asplit=2[voice_key][voice_mix];"
                "[0:a]aresample=48000,loudnorm=I=-24:TP=-3:LRA=14,"
                f"volume={spec.source_audio_gain_db:.2f}dB,"
                f"apad=pad_dur={target:.3f},atrim=duration={target:.3f}[bed];"
                "[bed][voice_key]sidechaincompress="
                f"threshold=0.025:ratio={duck_ratio:.2f}:"
                "attack=20:release=350[ducked];"
                "[ducked][voice_mix]amix=inputs=2:duration=longest:"
                "dropout_transition=0:normalize=0,"
                "alimiter=limit=0.95,loudnorm=I=-16:TP=-1.5:LRA=11[a]"
            )
        else:
            audio_filter = f"{voice_filter};[voice]anull[a]"
        filter_graph = f"[0:v]{video_filter}[v];{audio_filter}"
        self._run(
            [
                str(self.ffmpeg_executable),
                "-y",
                "-loglevel",
                "error",
                "-i",
                str(result.source_video),
                "-i",
                str(result.audio_path),
                "-filter_complex",
                filter_graph,
                "-map",
                "[v]",
                "-map",
                "[a]",
                "-t",
                f"{target:.3f}",
                "-c:v",
                "libx264",
                "-preset",
                "medium",
                "-crf",
                "18",
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
                str(destination),
            ],
            timeout=900,
        )

    @staticmethod
    def _video_timing_filter(
        source_duration: float,
        target_duration: float,
    ) -> str:
        """Stretch motion across the spoken line instead of freezing the tail."""

        scale = max(1.0, target_duration / max(source_duration, 0.001))
        filters = [
            "scale=1280:720:force_original_aspect_ratio=decrease",
            "pad=1280:720:(ow-iw)/2:(oh-ih)/2:black",
            "fps=24",
            "setpts=PTS-STARTPTS",
        ]
        if scale > 1.001:
            filters.append(f"setpts={scale:.6f}*PTS")
            if scale <= 1.8:
                filters.append(
                    "minterpolate=fps=24:mi_mode=mci:mc_mode=aobmc:"
                    "me_mode=bidir:vsbmc=1"
                )
            else:
                filters.append("fps=24")
        filters.extend(
            [
                f"trim=duration={target_duration:.3f}",
                "setpts=PTS-STARTPTS",
                "format=yuv420p",
            ]
        )
        return ",".join(filters)

    def _concat_segments(
        self,
        segments: list[Path],
        temp_dir: Path,
        destination: Path,
    ) -> None:
        concat_path = temp_dir / "concat.txt"
        lines = [
            f"file '{path.as_posix().replace(chr(39), chr(39) * 2)}'"
            for path in segments
        ]
        atomic_write_text(concat_path, "\n".join(lines) + "\n")
        self._run(
            [
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
                "-c",
                "copy",
                "-movflags",
                "+faststart",
                str(destination),
            ],
            timeout=900,
        )

    def _burn_subtitles(
        self,
        source: Path,
        subtitle_path: Path,
        destination: Path,
        *,
        visible_ai_label: bool = False,
    ) -> None:
        escaped = (
            subtitle_path.resolve()
            .as_posix()
            .replace("\\", "/")
            .replace(":", r"\:")
            .replace("'", r"\'")
        )
        subtitle_filter = (
            f"subtitles='{escaped}':"
            "force_style='FontName=Microsoft YaHei,FontSize=22,"
            "PrimaryColour=&H00FFFFFF,OutlineColour=&H80000000,"
            "BorderStyle=1,Outline=2,Shadow=0,Alignment=2,MarginV=38'"
        )
        video_filter = subtitle_filter
        if visible_ai_label:
            video_filter = f"{video_filter},{self._ai_label_filter()}"
        self._run(
            [
                str(self.ffmpeg_executable),
                "-y",
                "-loglevel",
                "error",
                "-i",
                str(source),
                "-vf",
                video_filter,
                "-c:v",
                "libx264",
                "-preset",
                "medium",
                "-crf",
                "18",
                "-c:a",
                "copy",
                "-movflags",
                "+faststart",
                str(destination),
            ],
            timeout=1800,
        )

    def _burn_ai_label(self, source: Path, destination: Path) -> None:
        self._run(
            [
                str(self.ffmpeg_executable),
                "-y",
                "-loglevel",
                "error",
                "-i",
                str(source),
                "-vf",
                self._ai_label_filter(),
                "-c:v",
                "libx264",
                "-preset",
                "medium",
                "-crf",
                "18",
                "-c:a",
                "copy",
                "-movflags",
                "+faststart",
                str(destination),
            ],
            timeout=1800,
        )

    @staticmethod
    def _ai_label_filter() -> str:
        font = Path("C:/Windows/Fonts/msyh.ttc")
        font_option = ""
        if font.is_file():
            escaped_font = font.as_posix().replace(":", r"\:")
            font_option = f"fontfile='{escaped_font}':"
        return (
            "drawtext="
            f"{font_option}"
            "text='AI生成内容':fontcolor=white@0.88:fontsize=18:"
            "box=1:boxcolor=black@0.38:boxborderw=6:"
            "x=w-tw-18:y=16"
        )

    @classmethod
    def _write_episode_subtitles(
        cls,
        results: list[DubbingLineResult],
        specs: list[DubbingLineSpec],
        destination: Path,
    ) -> None:
        cue_values: list[tuple[float, float, str]] = []
        offset = 0.0
        for result, spec in zip(results, specs, strict=True):
            if spec.subtitle_enabled and spec.mode != "mute" and result.text:
                local_cues = cls._read_srt_cues(result.subtitle_path)
                if not local_cues:
                    local_cues = [
                        (0.0, result.audio_duration_seconds, result.text)
                    ]
                for local_start, local_end, local_text in local_cues:
                    start = offset + spec.lead_seconds + local_start
                    end = min(
                        offset + result.timeline_duration_seconds - 0.05,
                        offset + spec.lead_seconds + local_end,
                    )
                    if end <= start:
                        continue
                    text = cls._strip_speaker_prefix(
                        local_text or result.text,
                        result.speaker,
                    )
                    cue_values.append(
                        (
                            start,
                            max(end, start + 0.2),
                            cls._wrap_subtitle_text(text),
                        )
                    )
            offset += result.timeline_duration_seconds
        cues: list[str] = []
        for index, (start, end, text) in enumerate(cue_values):
            if index + 1 < len(cue_values):
                next_start = cue_values[index + 1][0]
                if end >= next_start:
                    end = max(start + 0.2, next_start - 0.02)
            cues.append(
                f"{index + 1}\n"
                f"{cls._srt_time(start)} --> {cls._srt_time(end)}\n"
                f"{text}\n"
            )
        atomic_write_text(destination, "\n".join(cues))

    @classmethod
    def _read_srt_cues(cls, path: Path) -> list[tuple[float, float, str]]:
        if not path.is_file():
            return []
        content = path.read_text(encoding="utf-8-sig", errors="ignore")
        pattern = re.compile(
            r"(?:^|\n)\s*\d+\s*\n"
            r"(\d{2}:\d{2}:\d{2},\d{3})\s*-->\s*"
            r"(\d{2}:\d{2}:\d{2},\d{3})\s*\n"
            r"(.*?)(?=\n\s*\n|\Z)",
            re.DOTALL,
        )
        cues: list[tuple[float, float, str]] = []
        for match in pattern.finditer(content.replace("\r\n", "\n")):
            text = "".join(line.strip() for line in match.group(3).splitlines())
            if text:
                cues.append(
                    (
                        cls._srt_seconds(match.group(1)),
                        cls._srt_seconds(match.group(2)),
                        text,
                    )
                )
        return cues

    @staticmethod
    def _srt_seconds(value: str) -> float:
        hours, minutes, remainder = value.split(":", 2)
        seconds, millis = remainder.split(",", 1)
        return (
            int(hours) * 3600
            + int(minutes) * 60
            + int(seconds)
            + int(millis) / 1000
        )

    @staticmethod
    def _strip_speaker_prefix(text: str, speaker: str) -> str:
        value = " ".join(text.strip().split())
        if speaker:
            for separator in ("：", ":"):
                prefix = f"{speaker}{separator}"
                if value.startswith(prefix):
                    return value[len(prefix) :].lstrip()
        return value

    @staticmethod
    def _wrap_subtitle_text(text: str, max_chars: int = 22) -> str:
        value = " ".join(text.strip().split())
        if len(value) <= max_chars:
            return value
        lines: list[str] = []
        remaining = value
        punctuation = "，。！？；、,.!?;"
        while len(remaining) > max_chars:
            minimum = max(1, max_chars // 2)
            split_at = 0
            for index in range(minimum, min(len(remaining), max_chars) + 1):
                if remaining[index - 1] in punctuation:
                    split_at = index
            if split_at == 0:
                split_at = max_chars
            lines.append(remaining[:split_at].strip())
            remaining = remaining[split_at:].strip()
        if remaining:
            lines.append(remaining)
        return "\n".join(lines)

    def _synthesize_silence(
        self,
        duration_seconds: float,
        audio_path: Path,
        subtitle_path: Path,
    ) -> None:
        self._run(
            [
                str(self.ffmpeg_executable),
                "-y",
                "-loglevel",
                "error",
                "-f",
                "lavfi",
                "-i",
                "anullsrc=r=48000:cl=stereo",
                "-t",
                f"{max(0.1, duration_seconds):.3f}",
                "-c:a",
                "libmp3lame",
                str(audio_path),
            ],
            timeout=60,
        )
        atomic_write_text(subtitle_path, "")

    @staticmethod
    def _srt_time(seconds: float) -> str:
        total_ms = max(0, round(seconds * 1000))
        hours, remainder = divmod(total_ms, 3_600_000)
        minutes, remainder = divmod(remainder, 60_000)
        secs, millis = divmod(remainder, 1000)
        return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"

    def _media_duration(self, path: Path) -> float:
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
        detail = f"{process.stdout}\n{process.stderr}"
        match = re.search(
            r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)",
            detail,
        )
        if not match:
            raise RuntimeError(f"无法读取媒体时长：{path}")
        return (
            int(match.group(1)) * 3600
            + int(match.group(2)) * 60
            + float(match.group(3))
        )

    def _has_audio_stream(self, path: Path) -> bool:
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
        output = process.stderr or process.stdout
        return bool(re.search(r"Stream #\S+.*Audio:", output))

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
    def _run(
        command: list[str],
        *,
        timeout: int,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=check,
            creationflags=(
                subprocess.CREATE_NO_WINDOW
                if hasattr(subprocess, "CREATE_NO_WINDOW")
                else 0
            ),
        )
