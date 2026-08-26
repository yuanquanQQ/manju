import json
import urllib.error
from argparse import Namespace
from pathlib import Path

import pytest
from PIL import Image

from scripts.generate_high_quality_cast import build_casting_prompt
from workflows.chinese_cast.generate_cast import (
    MULTI_ANGLE_LORA,
    QWEN_EDIT_MODEL,
    Z_IMAGE_MODEL,
    build_qwen_angle_workflow,
    build_qwen_edit_workflow,
    build_z_image_workflow,
    normalize_image_size,
    run_angles,
    technical_qc,
    wait_for_image,
)


def test_z_image_casting_uses_quantized_official_sampling_graph() -> None:
    workflow = build_z_image_workflow(
        "中国青年男演员定妆照",
        seed=42,
        width=901,
        height=1351,
        steps=9,
        filename_prefix="cast/qinfeng/01",
    )

    assert workflow["1"]["inputs"]["unet_name"] == Z_IMAGE_MODEL
    assert workflow["2"]["inputs"]["type"] == "lumina2"
    assert workflow["6"]["inputs"]["width"] == 896
    assert workflow["6"]["inputs"]["height"] == 1344
    assert workflow["7"]["class_type"] == "ModelSamplingAuraFlow"
    assert workflow["8"]["inputs"]["steps"] == 9
    assert workflow["8"]["inputs"]["sampler_name"] == "res_multistep"
    assert workflow["8"]["inputs"]["negative"] == ["5", 0]


def test_qwen_angles_use_identity_reference_40_steps_and_angle_lora() -> None:
    workflow = build_qwen_angle_workflow(
        image_name="novel2anime/cast/qinfeng.png",
        edit_prompt="严格90度左侧面",
        seed=7,
        filename_prefix="cast/qinfeng/left",
    )

    assert workflow["3"]["inputs"]["unet_name"] == QWEN_EDIT_MODEL
    assert workflow["4"]["inputs"]["lora_name"] == MULTI_ANGLE_LORA
    assert workflow["9"]["inputs"]["image1"] == ["31", 0]
    assert workflow["11"]["inputs"]["reference_latents_method"] == ("index_timestep_zero")
    assert workflow["14"]["inputs"]["steps"] == 40
    assert workflow["14"]["inputs"]["cfg"] == 4.0


def test_qwen_keyframe_edit_accepts_composition_and_two_identity_anchors() -> None:
    workflow = build_qwen_edit_workflow(
        image_names=["composition.png", "qinfeng.png", "linlang.png"],
        edit_prompt="保持构图并锁定两位演员身份",
        seed=11,
        filename_prefix="keyframes/shot_014",
        output_width=832,
        output_height=480,
    )

    assert workflow["9"]["inputs"]["image1"] == ["31", 0]
    assert workflow["9"]["inputs"]["image2"] == ["32", 0]
    assert workflow["9"]["inputs"]["image3"] == ["33", 0]
    assert "4" not in workflow
    assert workflow["5"]["inputs"]["model"] == ["3", 0]
    assert workflow["17"]["class_type"] == "ImageScale"
    assert workflow["17"]["inputs"]["width"] == 832
    assert workflow["17"]["inputs"]["height"] == 480
    assert workflow["16"]["inputs"]["images"] == ["17", 0]


def test_angle_stage_requires_explicit_matching_approval(tmp_path: Path) -> None:
    source = tmp_path / "candidate.png"
    Image.new("RGB", (64, 64), "gray").save(source)
    approval = tmp_path / "approval.json"
    approval.write_text('{"status":"pending"}', encoding="utf-8")

    with pytest.raises(RuntimeError, match="does not approve"):
        run_angles(
            Namespace(
                source_image=str(source),
                approval_file=str(approval),
                comfy_root=str(tmp_path),
            )
        )


def test_wait_for_image_retries_transient_history_timeout(monkeypatch) -> None:
    responses = iter(
        [
            urllib.error.URLError("temporarily unavailable"),
            {"prompt-1": {"outputs": {"save": {"images": [{"filename": "result.png"}]}}}},
        ]
    )

    def fake_request(*_args, **_kwargs):
        response = next(responses)
        if isinstance(response, Exception):
            raise response
        return response

    monkeypatch.setattr("workflows.chinese_cast.generate_cast.request_json", fake_request)
    monkeypatch.setattr("workflows.chinese_cast.generate_cast.time.sleep", lambda _s: None)

    assert wait_for_image("http://comfy", "prompt-1", timeout_seconds=2) == {
        "filename": "result.png"
    }


def test_angle_stage_resumes_valid_existing_candidates(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "candidate.png"
    Image.new("RGB", (64, 96), "gray").save(source)
    approval = tmp_path / "approval.json"
    approval.write_text(
        json.dumps({"status": "approved", "source_image": str(source.resolve())}),
        encoding="utf-8",
    )
    output_dir = tmp_path / "angles"
    output_dir.mkdir()
    for angle in ("front", "left_profile", "back"):
        for candidate in (1, 2):
            Image.new("RGB", (64, 96), "gray").save(
                output_dir / f"{angle}_candidate_{candidate:02d}.png"
            )

    monkeypatch.setattr(
        "workflows.chinese_cast.generate_cast._copy_comfy_input",
        lambda *_args: "approved.png",
    )
    monkeypatch.setattr(
        "workflows.chinese_cast.generate_cast.release_comfy_memory",
        lambda *_args: None,
    )
    monkeypatch.setattr(
        "workflows.chinese_cast.generate_cast.submit_workflow",
        lambda *_args, **_kwargs: pytest.fail("resume submitted a duplicate job"),
    )

    manifest = run_angles(
        Namespace(
            source_image=str(source),
            approval_file=str(approval),
            comfy_root=str(tmp_path),
            output_dir=str(output_dir),
            comfy_url="http://comfy",
            candidate_count=2,
            seed=7,
            run_name="resume-test",
            lora_strength=1.0,
            timeout_seconds=10,
        )
    )

    assert len(manifest["outputs"]) == 6
    assert {record["prompt_id"] for record in manifest["outputs"]} == {"recovered_existing_output"}


def test_technical_qc_records_objective_checks(tmp_path: Path) -> None:
    path = tmp_path / "candidate.png"
    image = Image.effect_noise((896, 1344), 80).convert("RGB")
    image.save(path)

    result = technical_qc(path, expected_size=normalize_image_size(896, 1344))

    assert result["width"] == 896
    assert result["height"] == 1344
    assert result["aesthetic_review"] == "required"
    assert set(result["checks"]) == {
        "dimensions",
        "file_size",
        "tonal_range",
        "edge_detail",
        "limited_clipping",
    }


def test_casting_prompt_requires_chinese_actor_and_forbids_one_step_sheet() -> None:
    prompt = build_casting_prompt(
        "秦风",
        "18岁俊美阳刚的中国男性",
        "凤眼、直眉、利落下颌",
    )

    assert "中国籍、汉族面部特征明确" in prompt
    assert "俊美但明确阳刚" in prompt
    assert "三视图" not in prompt
    assert "单幅、单一构图" in prompt
    assert "肩部以上肖像" in prompt
    assert "廉价影楼服" in prompt
