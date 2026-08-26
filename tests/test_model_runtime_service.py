from pathlib import Path

from app.services.model_runtime_service import LocalModelRuntimeService


def _write_model(root: Path, relative: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"model")


def test_local_model_inventory_distinguishes_installed_and_callable(
    tmp_path: Path,
    monkeypatch,
) -> None:
    root = tmp_path / "models"
    _write_model(
        root,
        (
            "ComfyUI/models/checkpoints/Juggernaut_XI/"
            "Juggernaut-XI-byRunDiffusion.safetensors"
        ),
    )
    for relative in (
        "ComfyUI/models/diffusion_models/wan2.2_ti2v_5B_fp16.safetensors",
        "ComfyUI/models/text_encoders/umt5_xxl_fp8_e4m3fn_scaled.safetensors",
        "ComfyUI/models/vae/wan2.2_vae.safetensors",
    ):
        _write_model(root, relative)
    service = LocalModelRuntimeService(root, comfy_url="localhost:8189")
    monkeypatch.setattr(service, "_gpu_info", lambda: ("RTX Test", 8.0))
    monkeypatch.setattr(service, "_ram_gb", lambda: 32.0)
    monkeypatch.setattr(
        service,
        "_comfy_status",
        lambda: (
            True,
            {
                "CheckpointLoaderSimple",
                "Wan22ImageToVideoLatent",
                "CreateVideo",
                "SaveVideo",
            },
        ),
    )

    inventory = service.check_status()
    statuses = {model.model_id: model for model in inventory.models}

    assert statuses["juggernaut_xi"].callable is True
    assert statuses["wan22_ti2v_5b"].callable is True
    assert statuses["flux_krea"].installed is False
    assert statuses["latentsync_1_6"].compatible is False
    assert inventory.gpu_name == "RTX Test"


def test_installed_large_model_is_not_claimed_callable_on_8gb(
    tmp_path: Path,
    monkeypatch,
) -> None:
    root = tmp_path / "models"
    for relative in (
        "LatentSync/checkpoints/latentsync_unet.pt",
        "LatentSync/checkpoints/whisper/tiny.pt",
    ):
        _write_model(root, relative)
    service = LocalModelRuntimeService(root)
    monkeypatch.setattr(service, "_gpu_info", lambda: ("RTX Test", 8.0))
    monkeypatch.setattr(service, "_ram_gb", lambda: 32.0)
    monkeypatch.setattr(service, "_comfy_status", lambda: (False, set()))

    status = {
        model.model_id: model
        for model in service.check_status().models
    }["latentsync_1_6"]

    assert status.installed is True
    assert status.compatible is False
    assert status.callable is False
    assert "18GB" in status.message
