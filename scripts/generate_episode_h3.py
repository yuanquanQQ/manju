"""Resume-safe MiniMax H3 generation for one complete episode."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from dotenv import dotenv_values

from app.domain.video import EpisodeClipSpec, VideoRenderSpec
from app.services.desktop_service import DesktopProjectService
from app.services.gpu_service import GpuConnection, GpuServerService
from app.services.video_service import VideoRenderService
from workflows.minimax_h3.generate_video import H3_MODEL, H3_TEXT_ENCODER

H3_GENERATION_REVISION = "h3_t8_native_v1"

VOICE_CONTINUITY = {
    "旁白": "成熟中国男声，低沉温厚、冷静克制，近距离影视旁白，不朗诵、不拖腔",
    "秦风": "十八岁中国青年男声，清朗偏低、沉静克制，少年声线中有超越年龄的威压",
    "林浪": "二十一岁中国青年男声，贵气偏冷、语速从容，轻蔑时不尖细、不脸谱化",
    "秦三秋": "二十八岁中国男性，厚实果断、忠诚警觉，武将气质但不粗吼",
    "林淑婉": "十八岁中国青年女声，清冷通透、克制疏离，柔和但不甜腻",
    "护卫": "中国青年男声，沉稳简洁，服从命令但不机械",
}


def _ssh_password(path: Path) -> str:
    lines = [
        line.strip() for line in path.read_text(encoding="utf-8-sig").splitlines() if line.strip()
    ]
    candidates = [line for line in lines if "ssh " not in line.lower()]
    if not candidates:
        raise RuntimeError(f"No password entry found in {path}")
    password = candidates[-1]
    for separator in (":", "="):
        if separator not in password:
            continue
        label, value = password.split(separator, 1)
        if label.strip().lower() in {"password", "pass", "pwd"}:
            password = value.strip()
            break
    return password


def _read_episode(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _valid_h3_candidate(project_root: Path, shot: dict) -> tuple[Path, Path] | None:
    video = shot.get("video_generation") or {}
    selected_value = str(video.get("selected_video") or "")
    if not selected_value:
        return None
    selected = (project_root / selected_value).resolve()
    valid: list[tuple[Path, Path]] = []
    for item in video.get("candidates") or []:
        if not isinstance(item, dict):
            continue
        clip = project_root / str(item.get("file") or "")
        manifest = project_root / str(item.get("manifest") or "")
        if clip.resolve() != selected:
            continue
        if not clip.is_file() or not manifest.is_file():
            continue
        try:
            metadata = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            continue
        if (
            metadata.get("engine_profile") == "minimax_h3_fl2va"
            and metadata.get("model_name") == H3_MODEL
            and metadata.get("text_encoder") == H3_TEXT_ENCODER
            and metadata.get("generation_revision") == H3_GENERATION_REVISION
            and metadata.get("native_audio_mode") == "native_full"
            and bool(str(metadata.get("dialogue_prompt") or "").strip())
            and (metadata.get("technical_qc") or {}).get("checks", {}).get("has_audio") is True
        ):
            valid.append((clip.resolve(), manifest.resolve()))
    return max(valid, key=lambda item: item[0].stat().st_mtime) if valid else None


def _pending_h3_candidates(project_root: Path, shot: dict) -> list[Path]:
    """Return technically valid candidates that still need human review."""

    video = shot.get("video_generation") or {}
    pending: list[Path] = []
    for item in video.get("candidates") or []:
        if not isinstance(item, dict):
            continue
        clip = project_root / str(item.get("file") or "")
        manifest = project_root / str(item.get("manifest") or "")
        if not clip.is_file() or not manifest.is_file():
            continue
        try:
            metadata = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            continue
        if (
            metadata.get("engine_profile") == "minimax_h3_fl2va"
            and metadata.get("model_name") == H3_MODEL
            and metadata.get("text_encoder") == H3_TEXT_ENCODER
            and metadata.get("generation_revision") == H3_GENERATION_REVISION
            and metadata.get("approval_status") == "pending_visual_motion_audio_review"
            and (metadata.get("technical_qc") or {}).get("technical_pass") is True
            and metadata.get("native_audio_mode") == "native_full"
            and bool(str(metadata.get("dialogue_prompt") or "").strip())
        ):
            pending.append(clip.resolve())
    return pending


def _native_dialogue_prompt(shot: dict, *, compact: bool = False) -> str:
    audio = shot.get("audio_generation") or {}
    speaker = str(audio.get("speaker") or "旁白").strip() or "旁白"
    text = str(audio.get("text") or "").strip()
    if not text:
        raw_dialogue = str(shot.get("dialogue") or "").strip()
        _, separator, spoken = raw_dialogue.partition("：")
        text = spoken.strip() if separator else raw_dialogue
    if not text:
        raise RuntimeError(
            f"Shot {int(shot['shot_number']):03d} has no exact line for H3 native audio"
        )
    voice = VOICE_CONTINUITY.get(speaker, "自然的中国普通话真人声线，影视表演感，克制且清晰")
    performance = str(audio.get("instruct_text") or "").strip()
    mode = str(audio.get("mode") or "dialogue")
    placement = (
        "画外旁白，画面人物不得对口型"
        if mode == "auto_narration" or speaker == "旁白"
        else f"由画面中的{speaker}本人说出，嘴唇与每个汉字自然同步，其他人物不得开口"
    )
    prompt = (
        f"语言：标准中国普通话。说话人：{speaker}。固定声线：{voice}。"
        f"发声位置：{placement}。必须逐字、只说一次：『{text}』。"
        "禁止改词、漏词、加词、重复、翻译、唱诵、电子音和额外人声。"
        f"表演要求：{performance or '自然影视对白，停连随语义，不使用播音腔。'}"
    )[:1600]
    if compact:
        return (
            f"说话人：{speaker}。"
            f"发声位置：{placement}。"
            f"必须逐字、只说一次：『{text}』。"
        )
    return prompt


def _video_spec(project_root: Path, episode_number: int, shot: dict) -> VideoRenderSpec:
    video = shot.get("video_generation") or {}
    source = (project_root / str(video.get("source_image") or "")).resolve()
    end_value = str(video.get("end_image") or "")
    return VideoRenderSpec(
        episode_number=episode_number,
        shot_number=int(shot["shot_number"]),
        source_image=source,
        end_image=(project_root / end_value).resolve() if end_value else None,
        scene_description=str(shot.get("scene_description") or ""),
        subject_motion=str(video.get("subject_motion") or ""),
        environment_motion=str(video.get("environment_motion") or ""),
        continuity_constraints=str(video.get("continuity_constraints") or ""),
        negative_prompt=str(video.get("negative_prompt") or ""),
        motion_prompt=str(video.get("motion_prompt") or ""),
        native_audio_mode="native_full",
        dialogue_prompt=_native_dialogue_prompt(shot, compact=True),
        sound_effect_prompt=str(video.get("sound_effect_prompt") or ""),
        music_prompt=str(video.get("music_prompt") or ""),
        camera_movement=str(video.get("camera_movement") or "auto"),
        motion_strength=str(video.get("motion_strength") or "low"),
        screen_direction=str(video.get("screen_direction") or "auto"),
        transition_out=str(video.get("transition_out") or "cut"),
        transition_frames=int(video.get("transition_frames") or 0),
        handle_frames=int(video.get("handle_frames") or 0),
        # Start with one candidate; the run loop requests a second only when
        # the first candidate fails objective technical QC.
        candidate_count=1,
        duration_seconds=float(
            video.get("duration_seconds") or shot.get("duration_seconds") or 3.0
        ),
        fps=24,
        width=832,
        height=480,
        engine_profile="minimax_h3_fl2va",
        audio_mode_override=str(video.get("audio_mode_override") or ""),
        reference_audio=(
            (project_root / str(video["reference_audio"])).resolve()
            if video.get("reference_audio")
            else None
        ),
    )


def run(
    project_slug: str,
    episode_number: int,
    workspace: Path,
    *,
    max_shots: int | None = 20,
) -> Path:
    projects_dir = workspace / "projects"
    project_root = (projects_dir / project_slug).resolve()
    episode_path = project_root / "production" / "episodes" / f"episode_{episode_number:03d}.json"
    project_service = DesktopProjectService(projects_dir)
    gpu_service = GpuServerService()

    env = dotenv_values(workspace / ".env")
    config = GpuConnection(
        host=str(env.get("GPU_SSH_HOST") or ""),
        port=int(env.get("GPU_SSH_PORT") or 22),
        username=str(env.get("GPU_SSH_USER") or "root"),
        password=_ssh_password(workspace / "ssh.txt"),
    )

    episode = _read_episode(episode_path)
    shots = sorted(episode.get("shots") or [], key=lambda item: item["shot_number"])
    if max_shots is not None:
        if max_shots < 1:
            raise ValueError("max_shots must be at least 1")
        shots = shots[:max_shots]
    if not shots:
        raise RuntimeError(f"Episode has no shots: {episode_path}")

    # Fail before spending GPU time when the selected test slice is incomplete.
    cast_path = project_root / "production" / "cast_selection.json"
    cast = _read_episode(cast_path) if cast_path.is_file() else {}
    selections = cast.get("selections") or {}
    for shot in shots:
        number = int(shot["shot_number"])
        video = shot.get("video_generation") or {}
        source = project_root / str(video.get("source_image") or "")
        if not source.is_file():
            raise RuntimeError(f"Shot {number:03d} source image is missing: {source}")
        audio = shot.get("audio_generation") or {}
        spoken = str(audio.get("text") or shot.get("dialogue") or "").strip()
        if not spoken:
            raise RuntimeError(f"Shot {number:03d} has no exact dialogue/narration text")
        for character in shot.get("characters") or []:
            name = character if isinstance(character, str) else character.get("name", "")
            if name and not str(selections.get(name) or "").strip():
                raise RuntimeError(f"Shot {number:03d} character {name!r} has no approved cast reference")

    for shot in shots:
        shot_number = int(shot["shot_number"])
        existing = _valid_h3_candidate(project_root, shot)
        if existing:
            project_service.save_shot_video_result(
                project_slug,
                episode_number,
                shot_number,
                existing[0],
                existing[1],
                select=True,
            )
            print(f"[SKIP] shot={shot_number:03d} valid_h3_candidate", flush=True)
            continue

        pending = _pending_h3_candidates(project_root, shot)
        if pending:
            print(
                f"[REVIEW_REQUIRED] shot={shot_number:03d} candidates={len(pending)}",
                flush=True,
            )
            continue

        qc_status = str((shot.get("image_generation") or {}).get("qc_status") or "")
        if qc_status != "approved":
            raise RuntimeError(
                f"Shot {shot_number:03d} has no H3 candidate and keyframe QC is {qc_status!r}"
            )

        spec = _video_spec(project_root, episode_number, shot)

        def progress(percent: int, _message: str, number: int = shot_number) -> None:
            print(f"[PROGRESS] shot={number:03d} percent={percent}", flush=True)

        def persist(clip) -> None:
            project_service.save_shot_video_result(
                project_slug,
                clip.episode_number,
                clip.shot_number,
                clip.video_path,
                clip.manifest_path,
                select=False,
            )
            print(f"[SAVED] shot={clip.shot_number:03d} file={clip.video_path}", flush=True)

        print(f"[START] shot={shot_number:03d}", flush=True)
        for attempt in range(1, 3):
            batch = gpu_service.generate_h3_videos(
                config,
                project_root,
                [spec],
                progress_callback=progress,
                clip_callback=persist,
            )
            technical_ok = any(
                (json.loads(clip.manifest_path.read_text(encoding="utf-8")).get("technical_qc") or {}).get(
                    "technical_pass"
                ) is True
                for clip in batch.clips
            )
            if technical_ok or attempt == 2:
                break
            print(
                f"[RETRY] shot={shot_number:03d} technical QC failed; generating candidate 2",
                flush=True,
            )
        episode = _read_episode(episode_path)
        shots = sorted(episode.get("shots") or [], key=lambda item: item["shot_number"])
        if max_shots is not None:
            shots = shots[:max_shots]

    episode = _read_episode(episode_path)
    timeline: list[EpisodeClipSpec] = []
    missing: list[int] = []
    final_shots = sorted(episode.get("shots") or [], key=lambda item: item["shot_number"])
    if max_shots is not None:
        final_shots = final_shots[:max_shots]
    for shot in final_shots:
        video = shot.get("video_generation") or {}
        selected = project_root / str(video.get("selected_video") or "")
        if not selected.is_file():
            missing.append(int(shot["shot_number"]))
            continue
        timeline.append(
            EpisodeClipSpec(
                path=selected.resolve(),
                shot_number=int(shot["shot_number"]),
                duration_seconds=float(
                    video.get("duration_seconds") or shot.get("duration_seconds") or 3.0
                ),
                transition_out=str(video.get("transition_out") or "cut"),
                transition_frames=int(video.get("transition_frames") or 0),
            )
        )
    if missing:
        raise RuntimeError(
            "H3 candidates are generated but cannot be composed before human "
            f"motion/identity/audio review; unselected shots: {missing}"
        )

    print(f"[COMPOSE] clips={len(timeline)}", flush=True)
    result = VideoRenderService().compose_episode(
        project_root,
        episode_number,
        timeline,
        progress_callback=lambda percent, _message: print(
            f"[COMPOSE_PROGRESS] percent={percent}", flush=True
        ),
    )
    print(f"[DONE] episode={result.video_path}", flush=True)
    print(f"[MANIFEST] episode={result.manifest_path}", flush=True)
    return result.video_path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", default="jueshi")
    parser.add_argument("--episode", type=int, default=1)
    parser.add_argument("--workspace", type=Path, default=Path.cwd())
    parser.add_argument(
        "--max-shots",
        type=int,
        default=20,
        help="仅生成前 N 个镜头；传 0 表示整集",
    )
    args = parser.parse_args()
    run(
        args.project,
        args.episode,
        args.workspace.resolve(),
        max_shots=args.max_shots or None,
    )


if __name__ == "__main__":
    main()
