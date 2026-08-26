from workflows.krea.generate_samples import (
    build_kontext_workflow,
    build_sdxl_workflow,
    build_workflow,
)
from workflows.krea.generate_shots import (
    _cast_reference_strategy,
    _kontext_end_prompt,
    _shot_prompt,
)


def test_shot_prompt_uses_storyboard_prompt_and_style() -> None:
    prompt = _shot_prompt(
        {
            "image_prompt": "cinematic young hero in an ancient herb garden",
            "scene_description": "药圃清晨",
        },
        "photorealistic live-action cinematic photography",
    )

    assert "young hero" in prompt
    assert "photorealistic live-action" in prompt


def test_shot_prompt_has_fallback_when_director_omits_it() -> None:
    prompt = _shot_prompt(
        {
            "scene_description": "少年在晨雾中拔剑",
            "camera_angle": "wide shot",
        },
        "",
    )

    assert prompt.startswith("masterpiece, best quality")
    assert "少年在晨雾中拔剑" in prompt
    assert "action-ready keyframe" in prompt
    assert "not a rigid symmetrical stance" in prompt


def test_shot_prompt_injects_distinct_character_identity_locks() -> None:
    prompt = _shot_prompt(
        {
            "scene_description": "秦风与林浪在药圃对峙",
            "characters": [{"name": "秦风"}, {"name": "林浪"}],
            "image_prompt": "two young men confronting each other",
        },
        "",
        {"秦风": "pale-cyan robe", "林浪": "royal-blue brocade robe"},
        {
            "秦风": "round youthful face, large phoenix eyes, pale-cyan ribbon",
            "林浪": "long narrow face, fox eyes, dark-blue jade clasp",
        },
    )

    assert "Identity lock" in prompt
    assert "Qin Feng (" in prompt
    assert "Lin Lang (" in prompt
    assert "do not blend" in prompt
    assert "pale-cyan ribbon" in prompt
    assert "dark-blue jade clasp" in prompt


def test_shot_prompt_honors_detail_insert_without_full_character_lock() -> None:
    shot = {
        "scene_description": "Qin Feng checks a damaged spirit herb",
        "camera_angle": "macro insert",
        "image_prompt": "macro insert of one hand, cyan sleeve and exposed roots",
        "characters": [{"name": "Qin Feng"}],
    }

    prompt = _shot_prompt(
        shot,
        "",
        {"Qin Feng": "18-year-old boy hero"},
        {"Qin Feng": "18-year-old male hero in pale-cyan robes"},
    )

    assert "do not invent a full actor" in prompt
    assert "zero full people" in prompt
    assert "Identity lock" not in prompt


def test_shot_prompt_hardens_male_identity_with_english_alias() -> None:
    prompt = _shot_prompt(
        {
            "scene_description": "Qin Feng confronts his rival",
            "camera_angle": "medium shot",
            "image_prompt": "Qin Feng in a pale-cyan robe",
            "characters": [{"name": "秦风"}],
        },
        "",
        {"秦风": "18-year-old boy hero"},
        {"秦风": "18-year-old male hero in pale-cyan robes"},
    )

    assert "Qin Feng (秦风)" in prompt
    assert "unmistakably male young man" in prompt
    assert "flat male chest" in prompt
    assert "Qin Feng = MALE young man" in prompt


def test_detail_prompt_counts_seven_leaves_and_keeps_plant_in_soil() -> None:
    prompt = _shot_prompt(
        {
            "scene_description": "七叶灵草歪倒在田埂边",
            "camera_angle": "macro insert",
            "image_prompt": "macro insert of an exactly seven-leaf withered herb",
            "characters": [{"name": "秦风"}],
        },
        "",
    )

    assert "exactly seven separate visible leaf blades" in prompt
    assert "no flowers" in prompt
    assert "remains planted" in prompt
    assert "no hand holds or uproots" in prompt


def test_multi_male_prompt_forbids_feminizing_teal_robed_qin_feng() -> None:
    profiles = {
        "秦风": "18-year-old male hero in teal robes",
        "秦三秋": "21-year-old male guard in brown armor",
    }
    prompt = _shot_prompt(
        {
            "shot_number": 10,
            "scene_description": "Qin Sanqiu reports to Qin Feng",
            "camera_angle": "medium shot",
            "image_prompt": "two-shot in an herb garden",
            "characters": [{"name": "秦风"}, {"name": "秦三秋"}],
        },
        "",
        profiles,
        profiles,
    )

    assert "every visible human in this frame is a young adult MALE actor" in prompt
    assert "zero women" in prompt
    assert "Do not feminize the pale-cyan or teal-robed young man" in prompt
    assert "Exactly two foreground MEN in an over-the-shoulder composition" in prompt
    assert "LEFT foreground is Qin Feng" in prompt
    assert "seen strictly from behind" in prompt
    assert "RIGHT is Qin Sanqiu" in prompt
    assert "full male face clear for dialogue animation" in prompt


def test_single_male_prompt_adds_unmistakable_actor_guard() -> None:
    profiles = {"秦风": "18-year-old male hero in teal robes"}
    prompt = _shot_prompt(
        {
            "scene_description": "Qin Feng studies the damaged herbs",
            "camera_angle": "close-up",
            "image_prompt": "close-up of Qin Feng in teal robes",
            "characters": [{"name": "秦风"}],
        },
        "",
        profiles,
        profiles,
    )

    assert "The sole named actor is Qin Feng" in prompt
    assert "unmistakably masculine face" in prompt
    assert "no feminine facial proportions" in prompt
    assert "zero off-screen hands" in prompt
    assert "no hand or finger may enter from any image edge" in prompt


def test_multi_male_dialogue_keeps_only_speaker_face_readable() -> None:
    profiles = {
        "林浪": "19-year-old male nobleman in royal blue",
        "秦风": "18-year-old male hero in teal robes",
    }
    prompt = _shot_prompt(
        {
            "scene_description": "Lin Lang confronts Qin Feng",
            "camera_angle": "over-shoulder",
            "image_prompt": "tense confrontation",
            "characters": [{"name": "林浪"}, {"name": "秦风"}],
            "audio_generation": {"mode": "dialogue", "speaker": "林浪"},
        },
        "",
        profiles,
        profiles,
    )

    assert "Dialogue-safe blocking: Lin Lang" in prompt
    assert "only visible person and has a clear frontal or three-quarter male face" in prompt
    assert "Qin Feng" in prompt
    assert "remains completely off-camera" in prompt
    assert prompt.startswith("TOP PRIORITY DIALOGUE CAST AND CAMERA RULE")
    assert "Show exactly one visible young man" in prompt
    assert "zero additional people" in prompt
    assert "Identity lock — Lin Lang" in prompt
    assert "Identity lock — Qin Feng" not in prompt
    assert "strict single-person dialogue coverage shot" in prompt
    assert "tense confrontation" not in prompt


def test_irrigation_hand_is_only_added_to_explicit_pointing_shot() -> None:
    profiles = {"秦风": "18-year-old male hero in teal robes"}
    shot_six = _shot_prompt(
        {
            "shot_number": 6,
            "scene_description": "blocked irrigation channel",
            "camera_angle": "POV insert",
            "image_prompt": "weak trickle around stones",
            "characters": [{"name": "秦风"}],
        },
        "",
        profiles,
        profiles,
    )
    shot_eight = _shot_prompt(
        {
            "shot_number": 8,
            "scene_description": "Qin Feng studies a blocked irrigation channel",
            "camera_angle": "close-up",
            "image_prompt": "Qin Feng with the channel behind him",
            "characters": [{"name": "秦风"}],
        },
        "",
        profiles,
        profiles,
    )

    assert "pointing hand enters from the far left edge" in shot_six
    assert "pointing hand enters from the far left edge" not in shot_eight


def test_offscreen_dialogue_builds_silent_reaction_coverage() -> None:
    profiles = {
        "秦风": "18-year-old male hero in teal robes",
        "秦三秋": "21-year-old male guard in brown armor",
    }
    prompt = _shot_prompt(
        {
            "shot_number": 13,
            "scene_description": "an offscreen guard reports bad news",
            "camera_angle": "medium shot",
            "image_prompt": "Qin Feng and Qin Sanqiu react",
            "characters": [{"name": "秦风"}, {"name": "秦三秋"}],
            "audio_generation": {"mode": "dialogue", "speaker": "护卫"},
        },
        "",
        profiles,
        profiles,
    )

    assert "exactly one visible young male actor: Qin Feng" in prompt
    assert "silent listening reaction with closed relaxed lips" in prompt
    assert "Identity lock — Qin Feng" in prompt
    assert "Identity lock — Qin Sanqiu" not in prompt


def test_shot_eleven_uses_two_male_back_view_blocking() -> None:
    profiles = {
        "秦风": "18-year-old male hero in teal robes",
        "秦三秋": "21-year-old male guard in brown armor",
    }
    prompt = _shot_prompt(
        {
            "shot_number": 11,
            "scene_description": "two men walk through the herb plots",
            "camera_angle": "wide shot",
            "image_prompt": "Qin Feng and Qin Sanqiu walk left-to-right",
            "characters": [{"name": "秦风"}, {"name": "秦三秋"}],
        },
        "",
        profiles,
        profiles,
    )

    assert prompt.startswith("TOP PRIORITY DIALOGUE CAST AND CAMERA RULE")
    assert "Exactly two young MEN walking left-to-right" in prompt
    assert "seen strictly from behind" in prompt


def test_flux_workflow_uses_previous_shot_as_img2img_latent() -> None:
    workflow = build_workflow(
        "action-ready hero",
        seed=7,
        width=832,
        height=480,
        filename_prefix="continuity/test",
        reference_image="storyboard/previous.png [output]",
        denoise=0.72,
    )

    assert workflow["15"]["class_type"] == "LoadImage"
    assert workflow["12"]["inputs"]["latent_image"] == ["17", 0]
    assert workflow["9"]["inputs"]["denoise"] == 0.72


def test_kontext_workflow_uses_reference_latent_and_official_sampler() -> None:
    workflow = build_kontext_workflow(
        "Keep identity and scene; move the left foot one step forward.",
        seed=17,
        width=832,
        height=480,
        filename_prefix="continuity/kontext",
        reference_image="manju_continuity/start.png",
    )

    assert workflow["1"]["inputs"]["unet_name"] == (
        "flux1-dev-kontext_fp8_scaled.safetensors"
    )
    assert workflow["5"]["class_type"] == "FluxKontextImageScale"
    assert workflow["8"]["class_type"] == "ReferenceLatent"
    assert workflow["8"]["inputs"]["latent"] == ["6", 0]
    assert workflow["11"]["inputs"]["latent_image"] == ["6", 0]
    assert workflow["11"]["inputs"]["denoise"] == 1.0
    assert workflow["13"]["inputs"]["width"] == 832
    assert workflow["14"]["inputs"]["images"] == ["13", 0]


def test_kontext_end_prompt_prioritizes_visible_leg_reversal() -> None:
    prompt = _kontext_end_prompt(
        {
            "scene_description": "young man walks down the path",
            "video_generation": {
                "subject_motion": "\u5411\u524d\u8d70\u4e24\u6b65",
                "end_frame_prompt": "same actor completes the second step",
            },
        }
    )

    assert prompt.startswith("Edit the provided image")
    assert "Reverse the visible leading and trailing legs" in prompt
    assert "Do not zoom, crop, recenter" in prompt


def test_sdxl_workflow_uses_previous_shot_as_img2img_latent() -> None:
    workflow = build_sdxl_workflow(
        "action-ready hero",
        seed=7,
        width=832,
        height=480,
        filename_prefix="continuity/test",
        checkpoint="Juggernaut/model.safetensors",
        reference_image="continuity/previous.png",
        denoise=0.7,
    )

    assert workflow["8"]["class_type"] == "LoadImage"
    assert workflow["5"]["inputs"]["latent_image"] == ["10", 0]
    assert workflow["5"]["inputs"]["denoise"] == 0.7


def test_sdxl_identity_reference_uses_ipadapter_without_copying_composition() -> None:
    workflow = build_sdxl_workflow(
        "young hero kneeling in an herb garden",
        seed=9,
        width=832,
        height=480,
        filename_prefix="identity/test",
        checkpoint="Juggernaut_XI/model.safetensors",
        reference_image="cast/qin_feng.png",
        identity_reference=True,
    )

    assert workflow["9"]["class_type"] == "IPAdapterUnifiedLoader"
    assert workflow["10"]["class_type"] == "IPAdapter"
    assert workflow["5"]["inputs"]["model"] == ["10", 0]
    assert workflow["5"]["inputs"]["latent_image"] == ["4", 0]


def test_sdxl_male_prompt_has_explicit_gender_swap_negative() -> None:
    workflow = build_sdxl_workflow(
        "Identity lock: unmistakably male young man; Qin Feng = MALE young man",
        seed=10,
        width=832,
        height=480,
        filename_prefix="identity/male",
        checkpoint="Juggernaut_XI/model.safetensors",
    )

    negative = workflow["3"]["inputs"]["text"]
    assert "woman, female, girl" in negative
    assert "gender swap" in negative


def test_flux_cast_reference_is_only_img2img_for_single_person_coverage() -> None:
    common = {
        "reference_mode": "cast_selection",
        "reference_image": "cast/qin_feng.png",
        "architecture": "flux",
    }

    assert _cast_reference_strategy(
        **common,
        prompt="strict single-person dialogue coverage shot",
    ) == "img2img"
    assert _cast_reference_strategy(
        **common,
        prompt="wide two-shot in an herb garden",
    ) == "prompt_only"


def test_sdxl_cast_reference_uses_identity_adapter_when_gender_safe() -> None:
    assert _cast_reference_strategy(
        reference_mode="cast_selection",
        reference_image="cast/heroine.png",
        architecture="sdxl",
        prompt="unmistakably female young woman in white robes",
    ) == "identity_adapter"
    assert _cast_reference_strategy(
        reference_mode="none",
        reference_image="cast/heroine.png",
        architecture="sdxl",
        prompt="single portrait",
    ) == "none"


def test_shot_prompt_uses_explicit_end_state_without_start_pose_suffix() -> None:
    prompt = _shot_prompt(
        {
            "shot_number": 1,
            "scene_description": "少年沿土路行走",
            "image_prompt": "full body young man starting to walk",
            "video_generation": {
                "end_frame_prompt": "same young man stopped with both feet planted",
            },
        },
        "photorealistic live action",
        frame_role="end",
    )

    assert "STRICT END-FRAME CONTINUITY RULE" in prompt
    assert "same young man stopped with both feet planted" in prompt
    assert "cinematic action-ready keyframe captured immediately before" not in prompt
