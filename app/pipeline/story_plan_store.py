"""Validated persistence for standalone and episode-embedded story plans."""

from __future__ import annotations

import json
from pathlib import Path

from app.core.files import atomic_write_json
from app.domain.narrative import EpisodePlan


def story_plan_path(project_root: str | Path, episode_number: int) -> Path:
    return (
        Path(project_root)
        / "production"
        / "story_plans"
        / f"episode_{episode_number:03d}.json"
    )


def load_episode_plan(project_root: str | Path, episode_number: int) -> EpisodePlan:
    root = Path(project_root)
    plan_path = story_plan_path(root, episode_number)
    if plan_path.is_file():
        return EpisodePlan.model_validate_json(plan_path.read_text(encoding="utf-8-sig"))

    episode_path = (
        root / "production" / "episodes" / f"episode_{episode_number:03d}.json"
    )
    if episode_path.is_file():
        episode = json.loads(episode_path.read_text(encoding="utf-8-sig"))
        if isinstance(episode, dict) and isinstance(episode.get("narrative_plan"), dict):
            return EpisodePlan.model_validate(episode["narrative_plan"])
    raise FileNotFoundError(f"本集尚无编剧计划: {plan_path}")


def save_episode_plan(
    project_root: str | Path,
    episode_number: int,
    plan: EpisodePlan | dict[str, object],
) -> Path:
    root = Path(project_root)
    candidate = plan.model_dump(mode="python") if isinstance(plan, EpisodePlan) else plan
    validated = EpisodePlan.model_validate(candidate)
    if validated.episode_number != episode_number:
        raise ValueError(
            f"编剧计划集号 {validated.episode_number} 与目标集号 {episode_number} 不一致"
        )

    payload = validated.model_dump(mode="json")
    plan_path = story_plan_path(root, episode_number)
    plan_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(plan_path, payload)

    episode_path = (
        root / "production" / "episodes" / f"episode_{episode_number:03d}.json"
    )
    if episode_path.is_file():
        episode = json.loads(episode_path.read_text(encoding="utf-8-sig"))
        if not isinstance(episode, dict):
            raise ValueError(f"剧集文件不是 JSON 对象: {episode_path}")
        episode["narrative_plan"] = payload
        atomic_write_json(episode_path, episode)
    return plan_path
