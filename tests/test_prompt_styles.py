from app.pipeline.generate_image import build_prompt as build_pipeline_prompt
from app.services.prompt_styles import STYLE_PRESETS, apply_style
from workflows.krea.generate_samples import (
    build_portrait_prompt,
    build_sdxl_workflow,
    detect_character_gender,
    output_dimensions_for_layout,
)


def test_style_presets_cover_requested_visual_styles() -> None:
    assert {
        "真人电影",
        "简笔画",
        "油画",
        "中国水墨",
        "迪士尼动画感",
        "游戏 CG",
    } <= set(STYLE_PRESETS)


def test_apply_style_is_idempotent() -> None:
    once = apply_style("young xianxia hero", "中国水墨")
    twice = apply_style(once, "中国水墨")
    assert once == twice


def test_non_photorealistic_portrait_prompt_does_not_force_live_action() -> None:
    prompt = build_portrait_prompt(
        "秦风",
        "an exceptionally handsome 18-year-old hero",
        STYLE_PRESETS["中国水墨"],
    )
    assert "traditional Chinese ink wash painting" in prompt
    assert "real human actor" not in prompt


def test_portrait_prompt_enforces_casting_grade_handsome_beautiful_faces() -> None:
    prompt = build_portrait_prompt(
        "秦风",
        "handsome 18-year-old male hero",
        "live-action realistic human photograph",
    ).lower()
    assert "casting-grade appearance" in prompt
    assert "casting sex lock: male" in prompt
    assert "exceptionally handsome" in prompt
    assert "clearly masculine craniofacial structure" in prompt
    assert "exceptionally beautiful young woman" not in prompt
    assert "ordinary-looking" in prompt
    assert "plastic skin" in prompt
    assert "woman, female, girl" in prompt


def test_gender_detection_does_not_match_man_inside_woman() -> None:
    assert detect_character_gender("beautiful young woman and heroine") == "female"
    assert detect_character_gender("handsome young man and hero") == "male"


def test_sdxl_male_portrait_enables_sex_specific_negative_prompt() -> None:
    prompt = build_portrait_prompt(
        "Qin Feng",
        "handsome 18-year-old male hero",
        "live-action realistic human photograph",
    )
    workflow = build_sdxl_workflow(
        prompt,
        seed=42,
        width=768,
        height=1024,
        filename_prefix="cast/male",
        checkpoint="Juggernaut_XI/model.safetensors",
    )
    negative = workflow["3"]["inputs"]["text"]
    assert "woman, female, girl" in negative
    assert "androgynous face" in negative
    assert "gender swap" in negative


def test_pipeline_character_prompt_separates_male_and_female_casting() -> None:
    male_prompt, male_negative = build_pipeline_prompt(
        "Qin Feng",
        "handsome 18-year-old male hero",
    )
    female_prompt, female_negative = build_pipeline_prompt(
        "Lin Shuwan",
        "beautiful 18-year-old female swordswoman",
    )
    assert "CASTING SEX LOCK: MALE" in male_prompt
    assert "1woman" not in male_prompt
    assert "woman, female, girl" in male_negative
    assert "CASTING SEX LOCK: FEMALE" in female_prompt
    assert "1man" not in female_prompt
    assert "man, male, boy" in female_negative


def test_reference_board_prompt_is_strict_three_view_turnaround() -> None:
    prompt = build_portrait_prompt(
        "秦风",
        "十八岁白衣少年",
        "live-action realistic human photograph",
        "turnaround_no_bg",
    )
    assert "STRICT THREE-VIEW LAYOUT" in prompt
    assert "exactly three complete head-to-toe views" in prompt
    assert "exact front orthographic view" in prompt
    assert "strict left profile orthographic view" in prompt
    assert "exact back orthographic view" in prompt
    assert "only one eye and one ear are visible" in prompt
    assert "zero eye, nose, lips, cheek or facial profile visible" in prompt
    assert "IDENTITY LOCK" in prompt
    assert "pure white seamless studio background" in prompt
    assert "no scenery" in prompt.lower()
    assert "no portrait inset, no close-up grid" in prompt.lower()


def test_reference_board_uses_square_canvas() -> None:
    assert output_dimensions_for_layout(
        "turnaround_no_bg",
        "flux_krea",
        768,
        1024,
    ) == (1024, 1024)
    assert output_dimensions_for_layout(
        "turnaround_no_bg",
        "juggernaut_xi",
        768,
        1024,
    ) == (1024, 1024)


def test_sdxl_workflow_uses_checkpoint_loader() -> None:
    workflow = build_sdxl_workflow(
        "young hero",
        seed=42,
        width=1216,
        height=832,
        filename_prefix="cast/test",
        checkpoint="Juggernaut_XI/model.safetensors",
    )
    assert workflow["1"]["class_type"] == "CheckpointLoaderSimple"
    assert workflow["1"]["inputs"]["ckpt_name"] == "Juggernaut_XI/model.safetensors"
    assert workflow["4"]["inputs"]["width"] == 1216


def test_sdxl_reference_board_negative_prompt_preserves_multiple_views() -> None:
    workflow = build_sdxl_workflow(
        "character reference board",
        seed=42,
        width=1024,
        height=1024,
        filename_prefix="cast/reference",
        checkpoint="Juggernaut_XI/model.safetensors",
        layout_preset="turnaround_no_bg",
    )
    negative = workflow["3"]["inputs"]["text"]
    assert "mismatched identity" in negative
    assert "irregular grid" in negative
    assert "duplicate body" not in negative
