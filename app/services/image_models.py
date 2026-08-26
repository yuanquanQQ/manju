"""Image model presets shared by the desktop application and GPU service."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ImageModelPreset:
    model_id: str
    label: str
    filename: str
    architecture: str


IMAGE_MODEL_PRESETS: dict[str, ImageModelPreset] = {
    "flux_krea": ImageModelPreset(
        model_id="flux_krea",
        label="FLUX.1 Krea Dev FP8",
        filename="flux1-krea-dev_fp8_scaled.safetensors",
        architecture="flux",
    ),
    "juggernaut_xi": ImageModelPreset(
        model_id="juggernaut_xi",
        label="Juggernaut XI（SDXL）",
        filename="Juggernaut_XI/Juggernaut-XI-byRunDiffusion.safetensors",
        architecture="sdxl",
    ),
}

DEFAULT_IMAGE_MODEL_ID = "flux_krea"


def image_model_label(model_id_or_file: str) -> str:
    """Return a friendly label for a model id or stored filename."""
    value = model_id_or_file.strip()
    if value in IMAGE_MODEL_PRESETS:
        return IMAGE_MODEL_PRESETS[value].label
    lowered = value.lower()
    for preset in IMAGE_MODEL_PRESETS.values():
        if lowered in {preset.filename.lower(), preset.label.lower()}:
            return preset.label
    if "krea" in lowered:
        return IMAGE_MODEL_PRESETS["flux_krea"].label
    if "juggernaut" in lowered:
        return IMAGE_MODEL_PRESETS["juggernaut_xi"].label
    return value or "未知模型"


def validate_image_model_ids(model_ids: list[str]) -> list[str]:
    """Normalize a model selection while preserving UI order."""
    normalized: list[str] = []
    for model_id in model_ids:
        if model_id not in IMAGE_MODEL_PRESETS:
            raise ValueError(f"不支持的生图模型: {model_id}")
        if model_id not in normalized:
            normalized.append(model_id)
    if not normalized:
        raise ValueError("请至少选择一个生图模型")
    return normalized
