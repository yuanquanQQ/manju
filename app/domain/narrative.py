"""Dramatic planning contracts between novel analysis and shot direction."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class NarrativePlanProvenance(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model: str
    prompt_version: str
    input_hash: str


class EpisodeSourceGroup(BaseModel):
    model_config = ConfigDict(extra="forbid")

    episode_number: int = Field(ge=1)
    episode_title: str = Field(min_length=1, max_length=200)
    chapter_ids: list[str] = Field(min_length=1, max_length=4)
    boundary_reason: str = Field(min_length=4, max_length=400)
    opening_hook: str = Field(min_length=2, max_length=300)
    closing_hook: str = Field(min_length=2, max_length=300)


class AdaptationOutline(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.0"] = "1.0"
    groups: list[EpisodeSourceGroup] = Field(min_length=1)
    provenance: NarrativePlanProvenance


class CharacterStoryState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=80)
    external_goal: str = Field(default="", max_length=300)
    inner_need: str = Field(default="", max_length=300)
    last_pressure: str = Field(default="", max_length=300)
    last_choice: str = Field(default="", max_length=300)
    arc_state: str = Field(default="", max_length=300)
    knowledge: list[str] = Field(default_factory=list)
    last_episode_number: int = Field(default=0, ge=0)


class StoryThreadState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    thread: str = Field(min_length=2, max_length=300)
    state: str = Field(min_length=2, max_length=300)
    status: Literal["opened", "advanced", "paid_off", "held"]
    last_episode_number: int = Field(ge=1)


class StoryBible(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.0"] = "1.0"
    last_episode_number: int = Field(default=0, ge=0)
    source_chapter_ids: list[str] = Field(default_factory=list)
    characters: dict[str, CharacterStoryState] = Field(default_factory=dict)
    threads: dict[str, StoryThreadState] = Field(default_factory=dict)
    world_rules: list[str] = Field(default_factory=list)


class PlannedDialogue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_dialogue_id: str = Field(min_length=1, max_length=160)
    speaker: str = Field(min_length=1, max_length=80)
    source_text: str = Field(min_length=1, max_length=1000)
    adapted_text: str = Field(min_length=1, max_length=500)
    intent: str = Field(min_length=2, max_length=200)
    subtext: str = Field(default="", max_length=300)
    adaptation_reason: str = Field(min_length=2, max_length=300)


class CharacterArcBeat(BaseModel):
    model_config = ConfigDict(extra="forbid")

    character: str = Field(min_length=1, max_length=80)
    external_goal: str = Field(min_length=2, max_length=300)
    inner_need: str = Field(default="", max_length=300)
    pressure: str = Field(min_length=2, max_length=300)
    choice: str = Field(min_length=2, max_length=300)
    change: str = Field(min_length=2, max_length=300)


class StoryThreadUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    thread: str = Field(min_length=2, max_length=300)
    previous_state: str = Field(default="", max_length=300)
    new_state: str = Field(min_length=2, max_length=300)
    status: Literal["opened", "advanced", "paid_off", "held"]


class ScenePlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scene_number: int = Field(ge=1)
    title: str = Field(min_length=2, max_length=120)
    source_event_ids: list[str] = Field(default_factory=list)
    purpose: str = Field(min_length=4, max_length=300)
    location: str = Field(min_length=1, max_length=160)
    pov_character: str = Field(default="", max_length=80)
    goal: str = Field(min_length=2, max_length=300)
    obstacle: str = Field(min_length=2, max_length=300)
    stakes: str = Field(min_length=2, max_length=300)
    strategy: str = Field(min_length=2, max_length=300)
    turn: str = Field(min_length=2, max_length=300)
    outcome: str = Field(min_length=2, max_length=300)
    value_before: str = Field(min_length=1, max_length=120)
    value_after: str = Field(min_length=1, max_length=120)
    emotion_start: str = Field(min_length=1, max_length=80)
    emotion_end: str = Field(min_length=1, max_length=80)
    revelation: str = Field(default="", max_length=300)
    setup_or_payoff: str = Field(default="", max_length=300)
    visual_motif: str = Field(default="", max_length=200)
    dialogues: list[PlannedDialogue] = Field(default_factory=list)
    estimated_shots: int = Field(ge=1, le=12)
    estimated_duration_seconds: float = Field(ge=3.0, le=90.0)

    @model_validator(mode="after")
    def require_dramatic_change(self) -> ScenePlan:
        if self.value_before.strip() == self.value_after.strip():
            raise ValueError("场次必须产生可描述的价值变化")
        return self


class EpisodePlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.0"] = "1.0"
    episode_number: int = Field(ge=1)
    episode_title: str = Field(min_length=1, max_length=200)
    source_chapter_ids: list[str] = Field(min_length=1)
    logline: str = Field(min_length=8, max_length=500)
    protagonist: str = Field(min_length=1, max_length=80)
    episode_goal: str = Field(min_length=2, max_length=300)
    central_conflict: str = Field(min_length=2, max_length=300)
    stakes: str = Field(min_length=2, max_length=300)
    opening_hook: str = Field(min_length=2, max_length=300)
    scenes: list[ScenePlan] = Field(min_length=2, max_length=8)
    climax: str = Field(min_length=2, max_length=300)
    cliffhanger: str = Field(min_length=2, max_length=300)
    emotional_arc: list[str] = Field(min_length=2, max_length=8)
    character_arcs: list[CharacterArcBeat] = Field(default_factory=list)
    thread_updates: list[StoryThreadUpdate] = Field(default_factory=list)
    unresolved_threads: list[str] = Field(default_factory=list)
    provenance: NarrativePlanProvenance

    @model_validator(mode="after")
    def require_ordered_scenes(self) -> EpisodePlan:
        expected = list(range(1, len(self.scenes) + 1))
        actual = [scene.scene_number for scene in self.scenes]
        if actual != expected:
            raise ValueError("场次编号必须从 1 开始连续递增")
        return self

    @property
    def target_shot_count(self) -> int:
        return sum(scene.estimated_shots for scene in self.scenes)

    @property
    def target_duration_seconds(self) -> float:
        return round(sum(scene.estimated_duration_seconds for scene in self.scenes), 2)
