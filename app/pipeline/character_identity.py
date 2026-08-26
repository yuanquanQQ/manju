"""Stable, explicit visual identity fingerprints for recurring characters."""

from __future__ import annotations

from typing import Any


def derive_visual_fingerprints(
    profiles: dict[str, str],
    shots: list[Any],
    *,
    existing: dict[str, str] | None = None,
) -> dict[str, str]:
    """Build one compact identity lock per character without inventing new lore."""

    fingerprints = {
        str(name): str(value).strip()
        for name, value in (existing or {}).items()
        if str(name).strip() and str(value).strip()
    }
    appearances: dict[str, list[str]] = {}
    for shot in shots:
        for character in getattr(shot, "characters", []) or []:
            name = str(getattr(character, "name", "") or "").strip()
            if not name:
                continue
            parts = [
                str(getattr(character, "appearance", "") or "").strip(),
                str(getattr(character, "clothing", "") or "").strip(),
            ]
            appearances.setdefault(name, []).extend(part for part in parts if part)

    for name in sorted(set(profiles) | set(appearances)):
        if name in fingerprints:
            continue
        profile = str(profiles.get(name) or "").strip()
        observed = "; ".join(dict.fromkeys(appearances.get(name) or []))
        identity = profile or observed
        if identity:
            fingerprints[name] = (
                f"{identity[:700]}; immutable identity lock for {name}; preserve "
                "the exact face geometry, eye shape, nose, jaw, hair silhouette, "
                "costume palette and signature accessory in every shot; never "
                "reuse or blend another character's face or costume"
            )[:1000]
    return fingerprints
