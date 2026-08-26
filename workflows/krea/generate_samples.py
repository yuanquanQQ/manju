"""Generate style-controlled cast images with selectable ComfyUI models."""

from __future__ import annotations

import argparse
import json
import re
import time
import urllib.parse
from datetime import datetime
from pathlib import Path
from typing import Any

import requests

DEFAULT_MODEL = "flux1-krea-dev_fp8_scaled.safetensors"
KONTEXT_MODEL = "flux1-dev-kontext_fp8_scaled.safetensors"
DEFAULT_CLIP_L = "clip_l.safetensors"
DEFAULT_T5 = "t5xxl_fp8_e4m3fn.safetensors"
DEFAULT_VAE = "ae.safetensors"
DEFAULT_MODEL_ID = "flux_krea"

MODEL_PRESETS: dict[str, dict[str, str]] = {
    "flux_krea": {
        "label": "FLUX.1 Krea Dev FP8",
        "file": DEFAULT_MODEL,
        "architecture": "flux",
    },
    "juggernaut_xi": {
        "label": "Juggernaut XI（SDXL）",
        "file": "Juggernaut_XI/Juggernaut-XI-byRunDiffusion.safetensors",
        "architecture": "sdxl",
    },
}

LAYOUT_LABELS = {
    "portrait": "单人定妆照",
    "turnaround_no_bg": "三视图·白底",
    "turnaround_with_bg": "三视图·有背景",
}

CASTING_QUALITY_DIRECTIVE = (
    "CASTING-GRADE APPEARANCE: this is the definitive hero-character casting portrait, "
    "not an ordinary person and not a generic face. The face must have refined East Asian "
    "facial harmony, balanced eye spacing, a well-proportioned nose, coherent cheekbones "
    "and jaw, healthy realistic skin, and a memorable silhouette. The result must feel "
    "cinematic and believable rather than plastic, over-smoothed, childish, or "
    "influencer-like. Use restrained professional period-drama grooming, flattering key "
    "light, soft fill, clean catchlights, and a tasteful cool-warm color grade. Preserve "
    "a distinctive face that can be recognized at a glance in later shots."
)

MALE_CASTING_DIRECTIVE = (
    "CASTING SEX LOCK: MALE. Show exactly one unmistakably male young adult East Asian "
    "actor. He is exceptionally handsome with clearly masculine craniofacial structure: "
    "a defined brow ridge, straight masculine eyebrows, firm cheek and jaw planes, a "
    "proportional straight nose, a natural male hairline, and an athletic neck and shoulder "
    "line. Keep youthful refinement without feminizing him. Use clean, restrained male "
    "grooming with natural lips and realistic skin texture; no lipstick, blush, eyeliner, "
    "false eyelashes, feminine contouring, or beauty-filter face. He must never be depicted "
    "as a woman, girl, androgynous beauty, or gender-swapped character."
)

FEMALE_CASTING_DIRECTIVE = (
    "CASTING SEX LOCK: FEMALE. Show exactly one unmistakably female young adult East Asian "
    "actor. She is exceptionally beautiful with graceful feminine facial structure, "
    "luminous expressive eyes, elegant cheek and jaw contours, a refined nose, natural "
    "lips, and confident presence. Use tasteful premium period-drama makeup and realistic "
    "skin texture. She must never be depicted as a man or gender-swapped character."
)

BEAUTY_NEGATIVE_DIRECTIVE = (
    "ordinary-looking, plain face, generic face, average face, forgettable face, "
    "unattractive, awkward facial proportions, uneven eyes, dull eyes, flat lighting, "
    "plastic skin, waxy skin, over-smoothed skin, excessive makeup, influencer makeup, "
    "childlike face, elderly face, caricature, beauty-filter distortion, uncanny valley, "
    "facial asymmetry, crooked nose, weak jawline, puffy face, swollen eyes, bad teeth, "
    "blank expression, lifeless eyes, messy hair, cheap costume, modern clothing"
)

MALE_NEGATIVE_DIRECTIVE = (
    "woman, female, girl, feminine face, female body, breasts, feminine costume, lipstick, "
    "rouge, blush, eyeliner, false eyelashes, feminine eye makeup, feminine contouring, "
    "androgynous face, gender swap, overly delicate doll face, tiny pointed chin"
)

FEMALE_NEGATIVE_DIRECTIVE = (
    "man, male, boy, masculine face, male body, beard, moustache, stubble, facial hair, "
    "masculine brow ridge, gender swap"
)


def detect_character_gender(profile: str) -> str:
    """Return ``male``, ``female`` or ``unknown`` from unambiguous profile terms."""

    value = profile.casefold()
    female_pattern = r"\b(?:female|woman|women|girl|lady|heroine|swordswoman)\b"
    male_pattern = r"\b(?:male|man|men|boy|gentleman|hero|nobleman|guard|warrior)\b"
    if re.search(female_pattern, value) or any(
        term in value for term in ("女性", "女人", "少女", "姑娘", "小姐", "女主")
    ):
        return "female"
    if re.search(male_pattern, value) or any(
        term in value for term in ("男性", "男人", "少年", "公子", "少爷", "男主")
    ):
        return "male"
    return "unknown"


def _gender_beauty_directive(profile: str) -> str:
    """Add an explicit gender presentation without guessing from a character name."""

    gender = detect_character_gender(profile)
    if gender == "male":
        return MALE_CASTING_DIRECTIVE
    if gender == "female":
        return FEMALE_CASTING_DIRECTIVE
    return (
        "CASTING SEX LOCK: follow the sex stated in the character profile exactly. "
        "Do not make the character androgynous and do not gender-swap the character."
    )


def _gender_negative_directive(profile: str) -> str:
    gender = detect_character_gender(profile)
    if gender == "male":
        return MALE_NEGATIVE_DIRECTIVE
    if gender == "female":
        return FEMALE_NEGATIVE_DIRECTIVE
    return "androgynous face, gender swap"


class ComfyClient:
    def __init__(self, base_url: str, timeout: int = 1200) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.session = requests.Session()

    def health(self) -> None:
        response = self.session.get(f"{self.base_url}/system_stats", timeout=10)
        response.raise_for_status()

    def queue(self, workflow: dict[str, Any]) -> str:
        response = self.session.post(
            f"{self.base_url}/prompt",
            json={"prompt": workflow},
            timeout=30,
        )
        response.raise_for_status()
        payload = response.json()
        if "prompt_id" not in payload:
            raise RuntimeError(f"ComfyUI rejected workflow: {payload}")
        return str(payload["prompt_id"])

    def wait(self, prompt_id: str) -> dict[str, Any]:
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            response = self.session.get(
                f"{self.base_url}/history/{prompt_id}",
                timeout=30,
            )
            response.raise_for_status()
            entry = response.json().get(prompt_id)
            if entry:
                status = entry.get("status") or {}
                for message in status.get("messages", []):
                    if message and message[0] == "execution_error":
                        raise RuntimeError(json.dumps(message[1], ensure_ascii=False))
                images = [
                    image
                    for output in (entry.get("outputs") or {}).values()
                    for image in output.get("images", [])
                ]
                if images:
                    return images[0]
            time.sleep(2)
        raise TimeoutError(f"Timed out waiting for prompt {prompt_id}")

    def download(self, image: dict[str, Any], destination: Path) -> Path:
        query = urllib.parse.urlencode(
            {
                "filename": image["filename"],
                "subfolder": image.get("subfolder", ""),
                "type": image.get("type", "output"),
            }
        )
        response = self.session.get(f"{self.base_url}/view?{query}", timeout=120)
        response.raise_for_status()
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(response.content)
        return destination


def build_portrait_prompt(
    character: str,
    profile: str,
    style_prompt: str = "",
    layout_preset: str = "portrait",
) -> str:
    live_action = not style_prompt or any(
        marker in style_prompt.lower()
        for marker in ("live-action", "realistic human", "photograph")
    )
    subject = (
        f"The character is {character}. {profile}. The character must look "
        "unmistakably young, exceptionally attractive and charismatic, with "
        "refined East Asian facial proportions. "
        f"{_gender_beauty_directive(profile)} "
        "Authentic layered period costume, carefully arranged long hair, consistent "
        "face, hairstyle, costume, colors and accessories. "
        f"{CASTING_QUALITY_DIRECTIVE}"
    )
    if layout_preset == "turnaround_no_bg":
        style = (
            f"Visual style: {style_prompt}."
            if style_prompt
            else "Premium live-action Chinese historical fantasy drama photography."
        )
        return (
            "A professional live-action character turnaround reference sheet "
            f"presented as one clean image. {subject} "
            "STRICT THREE-VIEW LAYOUT: use a pure white seamless studio background "
            "and show exactly three complete head-to-toe views of the same character "
            "at identical scale, aligned on one ground line and evenly spaced. LEFT: "
            "exact front orthographic view, shoulders perfectly square to camera, both "
            "eyes and both ears symmetrically visible. CENTER: strict left profile "
            "orthographic view with the face, shoulders, hips and feet turned exactly "
            "90 degrees; only one eye and one ear are visible and the nose forms a "
            "clean side silhouette. RIGHT: exact back orthographic view, shoulders "
            "perfectly square away from camera; show only the back of the head and "
            "costume, with zero eye, nose, lips, cheek or facial profile visible. "
            "These are technical orthographic views, never three-quarter views. "
            "Neutral upright pose, "
            "arms relaxed slightly away from the torso, hands and all ten fingers "
            "readable, complete costume layers and both shoes visible in every view. "
            "REFERENCE-SHEET PROP RULE: all three views have empty hands and no held, "
            "worn or sheathed weapon, even if the story profile mentions a sword; "
            "weapons will be designed separately. "
            "IDENTITY LOCK: all three figures are the same single character with "
            "identical age, facial structure, body proportions, skin tone, hairline, "
            "hairstyle, costume construction, fabric pattern, accessories and colors. "
            "Flat even studio lighting, accurate anatomy and sharp face, hair and "
            "garment details. No perspective glamour pose, no three-quarter view, no "
            "portrait inset, no close-up grid, no scenery, no furniture, no loose "
            "props, no divider lines, no text, no pseudo-text, no captions, no labels, "
            "no numbers, no logo, no watermark, no repeated front view, no repeated "
            "profile view, no cropped body entering from any image edge, no fourth "
            "figure and no extra person. "
            f"{style}"
        )
    if layout_preset == "turnaround_with_bg":
        style = (
            f"Visual style: {style_prompt}."
            if style_prompt
            else "Premium live-action Chinese historical fantasy drama photography."
        )
        return (
            "A professional full-body character turnaround sheet in one wide "
            f"horizontal image divided into three equal vertical zones. {subject} "
            "Show exactly three complete full-body depictions of the same character. "
            "The image contains only these three full-body figures, all at the "
            "same scale, evenly spaced with a clearly visible gap between them. "
            "LEFT ZONE: exact front view. CENTER ZONE: exact back view, clearly "
            "facing away from the camera. RIGHT ZONE: 45-degree left three-quarter "
            "view. Neutral standing pose, arms relaxed, hands must not touch another "
            "figure, feet visible, no cropped body. No portrait inset, no close-up, "
            "no headshot, no fourth figure, no extra view, no text, no labels, "
            "no watermark. A refined Chinese xianxia environment in the background, "
            "coherent lighting and perspective across the entire sheet, with scenery "
            "kept subtle enough that the costume silhouette remains clear. "
            f"{style}"
        )
    if not live_action:
        return (
            "A premium single-character design portrait for a Chinese xianxia "
            f"historical fantasy story. {subject} Chest-up three-quarter composition, "
            "clear expressive eyes, "
            f"no text, no watermark, no other people. Visual style: {style_prompt}."
        )
    return (
        "A premium live-action casting portrait for a Chinese xianxia historical "
        f"fantasy drama. {subject} Chest-up three-quarter portrait, standing alone in "
        "an elegant ancient wooden corridor, relaxed upright posture, subtle "
        "confident expression, looking slightly past the camera. Authentic layered "
        "period costume and carefully arranged long hair. Soft warm window light, "
        "faint cool rim light, flattering professional drama makeup, realistic "
        "skin texture with fine pores, clear lively eyes, natural lips, restrained "
        "cinematic color grading, shallow depth of field, 85mm portrait lens. "
        "A real human actor photographed on a high-end cinema camera, not anime, "
        "not illustration, not CGI, no text, no watermark, no props, no other people. "
        f"Avoid: {BEAUTY_NEGATIVE_DIRECTIVE}, {_gender_negative_directive(profile)}."
    )


def output_dimensions_for_layout(
    layout_preset: str,
    model_id: str,
    default_width: int,
    default_height: int,
) -> tuple[int, int]:
    """Return a canvas suited to the selected composition preset."""
    if layout_preset == "turnaround_no_bg":
        return (1024, 1024)
    if layout_preset == "turnaround_with_bg":
        return (1344, 768) if model_id == "flux_krea" else (1216, 832)
    return (default_width, default_height)


def build_workflow(
    prompt: str,
    *,
    seed: int,
    width: int,
    height: int,
    filename_prefix: str,
    model: str = DEFAULT_MODEL,
    clip_l: str = DEFAULT_CLIP_L,
    t5: str = DEFAULT_T5,
    vae: str = DEFAULT_VAE,
    steps: int = 28,
    guidance: float = 3.5,
    reference_image: str = "",
    denoise: float = 1.0,
) -> dict[str, Any]:
    latent_source: list[Any] = ["11", 0]
    workflow: dict[str, Any] = {
        "1": {
            "class_type": "UNETLoader",
            "inputs": {"unet_name": model, "weight_dtype": "default"},
        },
        "2": {
            "class_type": "DualCLIPLoader",
            "inputs": {
                "clip_name1": clip_l,
                "clip_name2": t5,
                "type": "flux",
                "device": "default",
            },
        },
        "3": {
            "class_type": "VAELoader",
            "inputs": {"vae_name": vae},
        },
        "4": {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": prompt, "clip": ["2", 0]},
        },
        "5": {
            "class_type": "FluxGuidance",
            "inputs": {"conditioning": ["4", 0], "guidance": guidance},
        },
        "6": {
            "class_type": "ModelSamplingFlux",
            "inputs": {
                "model": ["1", 0],
                "max_shift": 1.15,
                "base_shift": 0.5,
                "width": width,
                "height": height,
            },
        },
        "7": {
            "class_type": "BasicGuider",
            "inputs": {"model": ["6", 0], "conditioning": ["5", 0]},
        },
        "8": {
            "class_type": "RandomNoise",
            "inputs": {"noise_seed": seed},
        },
        "9": {
            "class_type": "BasicScheduler",
            "inputs": {
                "model": ["6", 0],
                "scheduler": "simple",
                "steps": steps,
                "denoise": max(0.45, min(float(denoise), 1.0)),
            },
        },
        "10": {
            "class_type": "KSamplerSelect",
            "inputs": {"sampler_name": "euler"},
        },
        "11": {
            "class_type": "EmptySD3LatentImage",
            "inputs": {
                "width": width,
                "height": height,
                "batch_size": 1,
            },
        },
        "12": {
            "class_type": "SamplerCustomAdvanced",
            "inputs": {
                "noise": ["8", 0],
                "guider": ["7", 0],
                "sampler": ["10", 0],
                "sigmas": ["9", 0],
                "latent_image": ["11", 0],
            },
        },
        "13": {
            "class_type": "VAEDecode",
            "inputs": {"samples": ["12", 0], "vae": ["3", 0]},
        },
        "14": {
            "class_type": "SaveImage",
            "inputs": {
                "images": ["13", 0],
                "filename_prefix": filename_prefix,
            },
        },
    }
    if reference_image:
        workflow["15"] = {
            "class_type": "LoadImage",
            "inputs": {"image": reference_image},
        }
        workflow["16"] = {
            "class_type": "ImageScale",
            "inputs": {
                "image": ["15", 0],
                "upscale_method": "lanczos",
                "width": width,
                "height": height,
                "crop": "center",
            },
        }
        workflow["17"] = {
            "class_type": "VAEEncode",
            "inputs": {"pixels": ["16", 0], "vae": ["3", 0]},
        }
        latent_source = ["17", 0]
    workflow["12"]["inputs"]["latent_image"] = latent_source
    return workflow


def build_kontext_workflow(
    prompt: str,
    *,
    seed: int,
    width: int,
    height: int,
    filename_prefix: str,
    reference_image: str,
    model: str = KONTEXT_MODEL,
    clip_l: str = DEFAULT_CLIP_L,
    t5: str = DEFAULT_T5,
    vae: str = DEFAULT_VAE,
    steps: int = 20,
    guidance: float = 2.5,
) -> dict[str, Any]:
    """Build the official single-reference FLUX.1 Kontext edit graph."""

    if not reference_image:
        raise ValueError("FLUX.1 Kontext requires a reference image")
    return {
        "1": {
            "class_type": "UNETLoader",
            "inputs": {"unet_name": model, "weight_dtype": "default"},
        },
        "2": {
            "class_type": "DualCLIPLoader",
            "inputs": {
                "clip_name1": clip_l,
                "clip_name2": t5,
                "type": "flux",
                "device": "default",
            },
        },
        "3": {
            "class_type": "VAELoader",
            "inputs": {"vae_name": vae},
        },
        "4": {
            "class_type": "LoadImage",
            "inputs": {"image": reference_image},
        },
        "5": {
            "class_type": "FluxKontextImageScale",
            "inputs": {"image": ["4", 0]},
        },
        "6": {
            "class_type": "VAEEncode",
            "inputs": {"pixels": ["5", 0], "vae": ["3", 0]},
        },
        "7": {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": prompt, "clip": ["2", 0]},
        },
        "8": {
            "class_type": "ReferenceLatent",
            "inputs": {
                "conditioning": ["7", 0],
                "latent": ["6", 0],
            },
        },
        "9": {
            "class_type": "FluxGuidance",
            "inputs": {"conditioning": ["8", 0], "guidance": guidance},
        },
        "10": {
            "class_type": "ConditioningZeroOut",
            "inputs": {"conditioning": ["7", 0]},
        },
        "11": {
            "class_type": "KSampler",
            "inputs": {
                "seed": seed,
                "steps": steps,
                "cfg": 1.0,
                "sampler_name": "euler",
                "scheduler": "simple",
                "denoise": 1.0,
                "model": ["1", 0],
                "positive": ["9", 0],
                "negative": ["10", 0],
                "latent_image": ["6", 0],
            },
        },
        "12": {
            "class_type": "VAEDecode",
            "inputs": {"samples": ["11", 0], "vae": ["3", 0]},
        },
        "13": {
            "class_type": "ImageScale",
            "inputs": {
                "image": ["12", 0],
                "upscale_method": "lanczos",
                "width": width,
                "height": height,
                "crop": "center",
            },
        },
        "14": {
            "class_type": "SaveImage",
            "inputs": {
                "images": ["13", 0],
                "filename_prefix": filename_prefix,
            },
        },
    }


def build_sdxl_workflow(
    prompt: str,
    *,
    seed: int,
    width: int,
    height: int,
    filename_prefix: str,
    checkpoint: str,
    layout_preset: str = "portrait",
    steps: int = 30,
    cfg: float = 5.0,
    reference_image: str = "",
    denoise: float = 1.0,
    identity_reference: bool = False,
) -> dict[str, Any]:
    negative = (
        "ugly, old, middle-aged, beard, moustache, stubble, facial hair, deformed, "
        "bad anatomy, extra limbs, extra fingers, inconsistent costume, cropped "
        "feet, blurry, low quality, text, logo, watermark"
    )
    lowered_prompt = prompt.casefold()
    legacy_male_lock = re.search(
        r"\b(?:unmistakably\s+male|male\s+young\s+man|young\s+male)\b",
        lowered_prompt,
    )
    legacy_female_lock = re.search(
        r"\b(?:unmistakably\s+female|female\s+young\s+woman|young\s+female)\b",
        lowered_prompt,
    )
    if "casting sex lock: male" in lowered_prompt or legacy_male_lock:
        negative += f", {MALE_NEGATIVE_DIRECTIVE}"
    elif "casting sex lock: female" in lowered_prompt or legacy_female_lock:
        negative += f", {FEMALE_NEGATIVE_DIRECTIVE}"
    if layout_preset == "turnaround_no_bg":
        negative += (
            ", mismatched identity, different people, different face, different age, "
            "different hairstyle, different outfit, inconsistent colors, missing "
            "view, missing panel, irregular grid, crooked divider, environmental "
            "background, scenery, furniture, extra person outside the reference grid"
        )
    elif layout_preset == "portrait":
        negative += ", multiple people, duplicate person"
    latent_source: list[Any] = ["4", 0]
    workflow: dict[str, Any] = {
        "1": {
            "class_type": "CheckpointLoaderSimple",
            "inputs": {"ckpt_name": checkpoint},
        },
        "2": {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": prompt, "clip": ["1", 1]},
        },
        "3": {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": negative, "clip": ["1", 1]},
        },
        "4": {
            "class_type": "EmptyLatentImage",
            "inputs": {"width": width, "height": height, "batch_size": 1},
        },
        "5": {
            "class_type": "KSampler",
            "inputs": {
                "seed": seed,
                "steps": steps,
                "cfg": cfg,
                "sampler_name": "dpmpp_2m",
                "scheduler": "karras",
                "denoise": max(0.45, min(float(denoise), 1.0)),
                "model": ["1", 0],
                "positive": ["2", 0],
                "negative": ["3", 0],
                "latent_image": ["4", 0],
            },
        },
        "6": {
            "class_type": "VAEDecode",
            "inputs": {"samples": ["5", 0], "vae": ["1", 2]},
        },
        "7": {
            "class_type": "SaveImage",
            "inputs": {"images": ["6", 0], "filename_prefix": filename_prefix},
        },
    }
    if reference_image and identity_reference:
        workflow["8"] = {
            "class_type": "LoadImage",
            "inputs": {"image": reference_image},
        }
        workflow["9"] = {
            "class_type": "IPAdapterUnifiedLoader",
            "inputs": {
                "model": ["1", 0],
                "preset": "PLUS FACE (portraits)",
            },
        }
        workflow["10"] = {
            "class_type": "IPAdapter",
            "inputs": {
                "model": ["9", 0],
                "ipadapter": ["9", 1],
                "image": ["8", 0],
                "weight": 0.5,
                "start_at": 0.0,
                "end_at": 0.65,
                "weight_type": "prompt is more important",
            },
        }
        workflow["5"]["inputs"]["model"] = ["10", 0]
    elif reference_image:
        workflow["8"] = {
            "class_type": "LoadImage",
            "inputs": {"image": reference_image},
        }
        workflow["9"] = {
            "class_type": "ImageScale",
            "inputs": {
                "image": ["8", 0],
                "upscale_method": "lanczos",
                "width": width,
                "height": height,
                "crop": "center",
            },
        }
        workflow["10"] = {
            "class_type": "VAEEncode",
            "inputs": {"pixels": ["9", 0], "vae": ["1", 2]},
        }
        latent_source = ["10", 0]
    workflow["5"]["inputs"]["latent_image"] = latent_source
    return workflow


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--episode", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--comfy-url", default="http://127.0.0.1:8188")
    parser.add_argument("--character", default="")
    parser.add_argument("--all-cast", action="store_true")
    parser.add_argument("--portrait-count", type=int, default=1)
    parser.add_argument("--seed", type=int, default=20260727)
    parser.add_argument("--width", type=int, default=768)
    parser.add_argument("--height", type=int, default=1024)
    parser.add_argument("--prompt-override", default="")
    parser.add_argument("--style-prompt", default="")
    parser.add_argument(
        "--model",
        action="append",
        choices=tuple(MODEL_PRESETS),
        dest="models",
    )
    parser.add_argument(
        "--layout-preset",
        choices=tuple(LAYOUT_LABELS),
        default="portrait",
    )
    args = parser.parse_args()
    model_ids = list(dict.fromkeys(args.models or [DEFAULT_MODEL_ID]))

    episode = json.loads(args.episode.read_text(encoding="utf-8-sig"))
    profiles: dict[str, str] = episode["character_profiles"]
    if args.all_cast:
        selected = list(profiles.items())
    elif args.character:
        if args.character not in profiles:
            raise KeyError(f"Character not found: {args.character}")
        selected = [(args.character, profiles[args.character])]
    else:
        selected = [next(iter(profiles.items()))]

    client = ComfyClient(args.comfy_url)
    client.health()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    generated_started_at = datetime.now().astimezone().isoformat(timespec="seconds")
    manifest: dict[str, Any] = {
        "schema_version": "2.0",
        "model": (
            MODEL_PRESETS[model_ids[0]]["file"] if len(model_ids) == 1 else "multi-model"
        ),
        "models": [
            {
                "id": model_id,
                "label": MODEL_PRESETS[model_id]["label"],
                "file": MODEL_PRESETS[model_id]["file"],
                "architecture": MODEL_PRESETS[model_id]["architecture"],
            }
            for model_id in model_ids
        ],
        "clip_l": DEFAULT_CLIP_L,
        "t5": DEFAULT_T5,
        "vae": DEFAULT_VAE,
        "width": args.width,
        "height": args.height,
        "style_prompt": args.style_prompt,
        "layout_preset": args.layout_preset,
        "layout_label": LAYOUT_LABELS[args.layout_preset],
        "generated_at": generated_started_at,
        "images": [],
    }

    total = len(selected) * args.portrait_count * len(model_ids)
    completed = 0
    for character_index, (character, profile) in enumerate(selected):
        effective_profile = args.prompt_override or profile
        for model_index, model_id in enumerate(model_ids):
            model = MODEL_PRESETS[model_id]
            for candidate_index in range(args.portrait_count):
                seed = (
                    args.seed
                    + character_index * 1000
                    + model_index * 100_000
                    + candidate_index * 97
                )
                safe_character = f"character_{character_index + 1:02d}"
                destination = args.output_dir / (
                    f"{safe_character}_{model_id}_candidate_{candidate_index + 1:02d}.png"
                )
                prompt = build_portrait_prompt(
                    character,
                    effective_profile,
                    args.style_prompt,
                    args.layout_preset,
                )
                output_width, output_height = output_dimensions_for_layout(
                    args.layout_preset,
                    model_id,
                    args.width,
                    args.height,
                )
                filename_prefix = (
                    f"cast/{safe_character}_{model_id}_{candidate_index + 1:02d}"
                )
                if model["architecture"] == "flux":
                    workflow = build_workflow(
                        prompt,
                        seed=seed,
                        width=output_width,
                        height=output_height,
                        filename_prefix=filename_prefix,
                        model=model["file"],
                    )
                else:
                    workflow = build_sdxl_workflow(
                        prompt,
                        seed=seed,
                        width=output_width,
                        height=output_height,
                        filename_prefix=filename_prefix,
                        checkpoint=model["file"],
                        layout_preset=args.layout_preset,
                    )
                image = client.wait(client.queue(workflow))
                client.download(image, destination)
                generated_at = datetime.now().astimezone().isoformat(timespec="seconds")
                manifest["images"].append(
                    {
                        "character": character,
                        "candidate": candidate_index + 1,
                        "seed": seed,
                        "file": destination.name,
                        "model_id": model_id,
                        "model_label": model["label"],
                        "model_file": model["file"],
                        "layout_preset": args.layout_preset,
                        "layout_label": LAYOUT_LABELS[args.layout_preset],
                        "generated_at": generated_at,
                    }
                )
                completed += 1
                print(
                    f"[PROGRESS] {completed}/{total} {character} "
                    f"{model['label']} candidate {candidate_index + 1}",
                    flush=True,
                )

    (args.output_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
