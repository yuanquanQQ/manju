"""Deployability of the SDXL IP-Adapter Plus Face identity reference."""

from app.core.config import settings
from app.pipeline.generate_image import build_sdxl_workflow


def test_install_script_clones_ipadapter_node_and_verifies() -> None:
    script = settings.project_root / "scripts" / "gpu" / "ipadapter" / "install.sh"
    source = script.read_text(encoding="utf-8")

    # Weights only land the model files; the nodes must come from the
    # ComfyUI_IPAdapter_plus custom node, which the script must clone.
    assert "ComfyUI_IPAdapter_plus" in source
    assert "git" in source and "clone" in source
    # check_status requires exactly these two node class names to be present.
    assert "IPAdapterUnifiedLoader" in source
    assert "IPAdapter" in source
    # The script must not declare success without verifying the nodes landed.
    assert "grep -rq" in source


def test_legacy_pipeline_ipadapter_file_matches_deployed_face_weight() -> None:
    # The face weight deployed by install.sh is ip-adapter-plus-face_sdxl_vit-h;
    # the legacy IPAdapterModelLoader path must reference the same file rather
    # than the non-face variant, otherwise identity locking silently breaks.
    workflow = build_sdxl_workflow(
        positive_prompt="a portrait",
        negative_prompt="",
        seed=1,
        ipadapter_image="cast/front.png",
        ipadapter_weight=0.5,
    )
    assert workflow["9"]["class_type"] == "IPAdapterModelLoader"
    assert (
        workflow["9"]["inputs"]["ipadapter_file"]
        == "ip-adapter-plus-face_sdxl_vit-h.safetensors"
    )
