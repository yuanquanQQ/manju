from pathlib import Path

from workflows.wan22.generate_video import (
    FLF_HIGH_MODEL,
    FLF_LOW_MODEL,
    build_prompt,
    find_generated_video,
    normalize_frame_count,
)


def test_wan_frame_count_uses_four_n_plus_one_sequence() -> None:
    assert normalize_frame_count(72) == 73
    assert normalize_frame_count(81) == 81
    assert normalize_frame_count(1) == 5


def test_wan_api_prompt_matches_official_core_node_graph() -> None:
    prompt = build_prompt(
        image_name="novel2anime/start.png",
        positive_prompt="人物缓慢转头，摄影机稳定推近",
        negative_prompt="face morphing",
        width=832,
        height=480,
        frame_count=72,
        fps=24,
        seed=42,
        filename_prefix="novel2anime/run/candidate_01",
    )

    assert prompt["37"]["inputs"]["unet_name"] == (
        "wan2.2_ti2v_5B_fp16.safetensors"
    )
    assert prompt["55"]["class_type"] == "Wan22ImageToVideoLatent"
    assert prompt["55"]["inputs"]["start_image"] == ["56", 0]
    assert prompt["55"]["inputs"]["length"] == 73
    assert prompt["3"]["inputs"]["seed"] == 42
    assert prompt["58"]["class_type"] == "SaveVideo"


def test_find_generated_video_uses_unique_prefix(tmp_path: Path) -> None:
    output = tmp_path / "output" / "novel2anime" / "run"
    output.mkdir(parents=True)
    expected = output / "candidate_01_00001_.mp4"
    expected.write_bytes(b"mp4")

    found = find_generated_video(
        tmp_path,
        "novel2anime/run/candidate_01",
        submitted_at=expected.stat().st_mtime,
    )

    assert found == expected


def test_wan_flf_prompt_uses_two_stage_official_graph() -> None:
    prompt = build_prompt(
        image_name="novel2anime/start.png",
        end_image_name="novel2anime/end.png",
        engine_profile="wan22_flf2v",
        positive_prompt="a young swordsman completes one controlled step",
        negative_prompt="face morphing",
        width=832,
        height=480,
        frame_count=96,
        fps=24,
        seed=42,
        filename_prefix="novel2anime/run/candidate_01",
    )

    assert prompt["10"]["inputs"]["unet_name"] == FLF_HIGH_MODEL
    assert prompt["11"]["inputs"]["unet_name"] == FLF_LOW_MODEL
    assert prompt["20"]["class_type"] == "WanFirstLastFrameToVideo"
    assert prompt["20"]["inputs"]["start_image"] == ["14", 0]
    assert prompt["20"]["inputs"]["end_image"] == ["15", 0]
    assert prompt["20"]["inputs"]["length"] == 97
    assert prompt["21"]["inputs"]["end_at_step"] == 10
    assert prompt["22"]["inputs"]["start_at_step"] == 10
    assert prompt["22"]["inputs"]["latent_image"] == ["21", 0]


def test_wan_flf_prompt_rejects_missing_end_image() -> None:
    try:
        build_prompt(
            image_name="novel2anime/start.png",
            engine_profile="wan22_flf2v",
            positive_prompt="walk",
            negative_prompt="",
            width=832,
            height=480,
            frame_count=81,
            fps=24,
            seed=1,
            filename_prefix="test",
        )
    except ValueError as exc:
        assert "end image" in str(exc)
    else:
        raise AssertionError("missing FLF2V end frame should fail")
