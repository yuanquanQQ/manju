"""Build production-ready, time-coded prompts for one storyboard shot."""

from __future__ import annotations


def _timestamp(value: float) -> str:
    rounded = round(value, 2)
    return f"{rounded:.2f}".rstrip("0").rstrip(".")


def beat_boundaries(duration_seconds: float) -> tuple[float, ...]:
    """Return five contiguous beats using the approved 12-second rhythm."""

    duration = max(1.0, float(duration_seconds))
    establish_end = min(0.5, duration * 0.125)
    remaining = duration - establish_end
    boundaries = (
        0.0,
        establish_end,
        establish_end + remaining * 0.261,
        establish_end + remaining * 0.487,
        establish_end + remaining * 0.739,
        duration,
    )
    return tuple(round(value, 2) for value in boundaries)


def build_storyboard_motion_prompt(
    *,
    shot_number: int,
    duration_seconds: float,
    scene_description: str,
    subject_motion: str,
    environment_motion: str,
    entry_state: str,
    exit_state: str,
    dialogue: str = "",
    sound_effect: str = "",
    dramatic_point: str = "",
) -> str:
    """Format a shot as a strict five-beat audiovisual execution contract."""

    times = beat_boundaries(duration_seconds)
    spans = [
        f"{_timestamp(times[index])}-{_timestamp(times[index + 1])}秒"
        for index in range(5)
    ]
    entry = entry_state.strip() or "本集初始构图与既定人物位置"
    scene = scene_description.strip() or subject_motion.strip() or exit_state.strip()
    action = subject_motion.strip() or scene
    exit_pose = exit_state.strip() or action
    environment = environment_motion.strip() or "仅保留人物呼吸和现场环境的轻微连续运动"
    line = dialogue.strip()
    if line:
        dialogue_sync = f"台词同步：{line}"
    else:
        dialogue_sync = "本镜无台词，不得擅自添加旁白、对白或额外人声"
    sounds = sound_effect.strip() or "现场环境底噪、人物呼吸、衣料与动作接触声"
    point = dramatic_point.strip() or "本镜情绪与人物关系的可见变化"
    frame_id = f"C{int(shot_number):02d}"

    return "\n".join(
        [
            "节拍划分",
            f"拍1｜本镜{spans[0]}｜建立拍：完整复现首帧承接状态“{entry}”；在建立拍内用{environment}启动画面，不做静止停顿",
            f"拍2｜本镜{spans[1]}｜起势拍：沿既定位置、视线和运动路径开始动作准备；{scene}；手部先预备，身体重心先转移，不提前完成动作",
            f"拍3｜本镜{spans[2]}｜推进拍：连续完成本镜主要动作“{action}”；{dialogue_sync}；动作、口型、视线和道具接触必须处于同一时间线",
            f"拍4｜本镜{spans[3]}｜反应拍：主要动作完成后保留真实惯性，人物以眼神、呼吸、肩线和重心变化回应；情绪落点为“{point}”，不得追加无关动作",
            f"拍5｜本镜{spans[4]}｜落点拍：动作减速并落到既定尾帧“{exit_pose}”，随后保留短暂余势与呼吸，确保下一镜可直接承接",
            "执行锁定：人物均从画面内既有入口或上一镜尾帧明确位置进入；动作先准备再完成，重心、视线和手部跟随动作变化，完成后保留短暂余势与呼吸；禁止凭空出现、瞬移、突然消失、机械连招和肢体僵硬。",
            line,
            "在新动作开始前，先完整复现上一镜尾帧的人物位置、姿态、视线、手持道具和运动趋势；人物不得凭空出现或无路径换位。",
            f"声音与产品点：{sounds}；以“{point}”形成清晰的声画落点",
            f"首帧承接：{entry}",
            f"尾帧定格：{frame_id}，{exit_pose}",
        ]
    )


def build_bridge_prompt(*, entry_state: str, exit_state: str) -> str:
    """Build the shorter first/last-frame continuity instruction."""

    entry = entry_state.strip() or "本集初始构图与既定人物位置"
    exit_pose = exit_state.strip() or "本镜动作完成后的稳定姿态"
    return (
        f"首帧完整复现“{entry}”中的人物位置、姿态、视线、手持道具和运动趋势；"
        "新动作必须先准备再完成，人物只沿画面内可见路径移动；"
        f"尾帧减速落到“{exit_pose}”，保留短暂余势与呼吸。"
        "禁止凭空出现、瞬移、突然消失、无路径换位、机械连招和肢体僵硬。"
    )


def refresh_episode_motion_prompts(episode: dict[str, object]) -> int:
    """Rebuild time-coded prompts after duration or audio planning changes."""

    updated = 0
    shots = episode.get("shots")
    if not isinstance(shots, list):
        return updated
    for fallback_number, shot in enumerate(shots, start=1):
        if not isinstance(shot, dict):
            continue
        video = shot.get("video_generation")
        video = dict(video) if isinstance(video, dict) else {}
        continuity = shot.get("continuity_plan")
        continuity = continuity if isinstance(continuity, dict) else {}
        duration = float(
            video.get("duration_seconds")
            or shot.get("duration_seconds")
            or 3.0
        )
        prompt = build_storyboard_motion_prompt(
            shot_number=int(shot.get("shot_number") or fallback_number),
            duration_seconds=duration,
            scene_description=str(shot.get("scene_description") or ""),
            subject_motion=str(video.get("subject_motion") or ""),
            environment_motion=str(video.get("environment_motion") or ""),
            entry_state=str(continuity.get("entry_state") or ""),
            exit_state=str(continuity.get("exit_state") or ""),
            dialogue=str(shot.get("dialogue") or ""),
            sound_effect=str(
                video.get("sound_effect_prompt")
                or shot.get("sound_effect")
                or ""
            ),
            dramatic_point=str(shot.get("emotion") or ""),
        )
        if video.get("motion_prompt") != prompt:
            video["motion_prompt"] = prompt
            shot["video_generation"] = video
            updated += 1
    return updated
