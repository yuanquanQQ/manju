import pytest

from app.services.character_presets import (
    CHARACTER_LAYOUT_PRESETS,
    character_layout_label,
)
from app.services.image_models import (
    IMAGE_MODEL_PRESETS,
    image_model_label,
    validate_image_model_ids,
)


def test_installed_model_presets_and_friendly_labels() -> None:
    assert set(IMAGE_MODEL_PRESETS) == {"flux_krea", "juggernaut_xi"}
    assert image_model_label("flux1-krea-dev_fp8_scaled.safetensors") == (
        "FLUX.1 Krea Dev FP8"
    )
    assert image_model_label(
        "Juggernaut_XI/Juggernaut-XI-byRunDiffusion.safetensors"
    ) == "Juggernaut XI（SDXL）"


def test_model_selection_is_deduplicated_and_required() -> None:
    assert validate_image_model_ids(
        ["flux_krea", "juggernaut_xi", "flux_krea"]
    ) == ["flux_krea", "juggernaut_xi"]
    with pytest.raises(ValueError, match="至少选择"):
        validate_image_model_ids([])


def test_character_turnaround_presets_are_available() -> None:
    assert "turnaround_no_bg" in CHARACTER_LAYOUT_PRESETS
    assert "turnaround_with_bg" in CHARACTER_LAYOUT_PRESETS
    assert character_layout_label("turnaround_no_bg") == "三视图·白底"
    assert character_layout_label("turnaround_with_bg") == "三视图·有背景"
