"""Character composition presets for cast image generation."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class CharacterLayoutPreset:
    preset_id: str
    label: str
    short_label: str


CHARACTER_LAYOUT_PRESETS: dict[str, CharacterLayoutPreset] = {
    "portrait": CharacterLayoutPreset(
        preset_id="portrait",
        label="普通单人定妆照",
        short_label="单人定妆照",
    ),
    "turnaround_no_bg": CharacterLayoutPreset(
        preset_id="turnaround_no_bg",
        label="预设1｜标准角色三视图：正面＋严格侧面＋背面｜白底",
        short_label="三视图·白底",
    ),
    "turnaround_with_bg": CharacterLayoutPreset(
        preset_id="turnaround_with_bg",
        label="预设2｜三视图：正面、背面、45°左侧面｜有背景",
        short_label="三视图·有背景",
    ),
}

DEFAULT_CHARACTER_LAYOUT_ID = "portrait"


def character_layout_label(preset_id: str) -> str:
    preset = CHARACTER_LAYOUT_PRESETS.get(preset_id)
    return preset.short_label if preset else preset_id
