from workflows.krea.revise_image import build_revision_prompt


def test_revision_prompt_prioritizes_problem_and_preserves_identity() -> None:
    prompt = build_revision_prompt(
        "an 18-year-old handsome young swordsman in the same teal robe",
        "the face looks too old and must have no beard",
        "middle-aged, beard, moustache, extra fingers",
        "strict",
    )

    assert prompt.startswith("Edit the provided image")
    assert "face looks too old" in prompt
    assert "Make only the requested local correction" in prompt
    assert "same face identity" in prompt
    assert "middle-aged, beard" in prompt


def test_revision_prompt_supports_broader_creative_adjustment() -> None:
    prompt = build_revision_prompt(
        "full-body composition with both feet visible",
        "the current framing crops the feet",
        "cropped feet",
        "creative",
    )

    assert "allow a broader re-render of pose, framing or styling" in prompt
    assert "full-body composition" in prompt
