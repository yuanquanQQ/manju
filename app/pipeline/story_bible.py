"""Persist long-form character arcs and unresolved story threads."""

from __future__ import annotations

from collections.abc import Iterable

from app.domain.narrative import (
    CharacterStoryState,
    EpisodePlan,
    StoryBible,
    StoryThreadState,
)


def evolve_story_bible(
    current: StoryBible | None,
    plan: EpisodePlan,
) -> StoryBible:
    bible = current.model_copy(deep=True) if current else StoryBible()
    bible.last_episode_number = plan.episode_number
    bible.source_chapter_ids = list(
        dict.fromkeys([*bible.source_chapter_ids, *plan.source_chapter_ids])
    )
    for arc in plan.character_arcs:
        previous = bible.characters.get(arc.character)
        knowledge = list(previous.knowledge) if previous else []
        bible.characters[arc.character] = CharacterStoryState(
            name=arc.character,
            external_goal=arc.external_goal,
            inner_need=arc.inner_need,
            last_pressure=arc.pressure,
            last_choice=arc.choice,
            arc_state=arc.change,
            knowledge=knowledge,
            last_episode_number=plan.episode_number,
        )
    for update in plan.thread_updates:
        bible.threads[update.thread] = StoryThreadState(
            thread=update.thread,
            state=update.new_state,
            status=update.status,
            last_episode_number=plan.episode_number,
        )
    for thread in plan.unresolved_threads:
        if thread not in bible.threads:
            bible.threads[thread] = StoryThreadState(
                thread=thread,
                state=thread,
                status="held",
                last_episode_number=plan.episode_number,
            )
    return bible


def rebuild_story_bible(
    plans: Iterable[EpisodePlan],
    *,
    world_rules: Iterable[str] = (),
) -> StoryBible:
    bible = StoryBible(world_rules=list(dict.fromkeys(world_rules)))
    ordered = sorted(plans, key=lambda plan: plan.episode_number)
    for plan in ordered:
        if plan.episode_number <= bible.last_episode_number:
            raise ValueError("故事圣经只能由集号严格递增的计划重建")
        bible = evolve_story_bible(bible, plan)
    return bible
