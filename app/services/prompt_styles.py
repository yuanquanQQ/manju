"""Shared visual-style presets for desktop prompt editing."""

from __future__ import annotations

STYLE_PRESETS: dict[str, str] = {
    "真人电影": (
        "premium live-action cinematic photography, realistic human actors, "
        "natural skin texture, professional lighting, shallow depth of field"
    ),
    "简笔画": (
        "minimalist line drawing, clean confident strokes, simple shapes, "
        "white background, restrained color accents"
    ),
    "油画": (
        "classical oil painting, visible textured brushstrokes, rich pigments, "
        "dramatic museum lighting, painterly composition"
    ),
    "中国水墨": (
        "traditional Chinese ink wash painting, expressive brushwork, rice paper "
        "texture, elegant negative space, subtle mineral colors"
    ),
    "迪士尼动画感": (
        "polished family animation aesthetic, appealing expressive characters, "
        "soft rounded shapes, vibrant colors, cinematic animated lighting"
    ),
    "游戏 CG": (
        "high-end AAA game cinematic CG, detailed materials, dramatic volumetric "
        "lighting, heroic composition, polished concept-art finish"
    ),
}

DEFAULT_STYLE = "真人电影"


def style_prompt(name: str) -> str:
    return STYLE_PRESETS.get(name, STYLE_PRESETS[DEFAULT_STYLE])


def apply_style(prompt: str, name: str) -> str:
    base = prompt.strip().rstrip(" ,.")
    addition = style_prompt(name)
    if addition.lower() in base.lower():
        return base
    return f"{base}, {addition}" if base else addition
