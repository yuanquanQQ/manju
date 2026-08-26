from pathlib import Path

from app.services.gpu_service import (
    _cast_reference_character,
    _resolve_cast_reference,
    _shot_character_names,
)


def test_shot_character_names_falls_back_to_continuity_signature() -> None:
    shot = {"continuity_plan": {"cast_signature": "秦风|秦三秋|秦风"}}

    assert _shot_character_names(shot) == ["秦风", "秦三秋"]


def test_cast_reference_prefers_visible_speaker_then_storyboard_lead() -> None:
    base = {
        "characters": [{"name": "秦风"}, {"name": "林浪"}],
    }

    assert _cast_reference_character(
        {**base, "audio_generation": {"mode": "dialogue", "speaker": "林浪"}}
    ) == "林浪"
    assert _cast_reference_character(
        {**base, "audio_generation": {"mode": "auto_narration", "speaker": "旁白"}}
    ) == "秦风"


def test_resolve_cast_reference_rejects_missing_and_external_paths(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    portrait = project / "outputs" / "qin_feng.png"
    portrait.parent.mkdir(parents=True)
    portrait.write_bytes(b"png")
    shot = {"characters": [{"name": "秦风"}]}

    assert _resolve_cast_reference(
        project,
        {"秦风": "outputs/qin_feng.png"},
        shot,
    ) == ("秦风", portrait.resolve())
    assert _resolve_cast_reference(project, {}, shot) is None
    assert _resolve_cast_reference(
        project,
        {"秦风": "../outside.png"},
        shot,
    ) is None
