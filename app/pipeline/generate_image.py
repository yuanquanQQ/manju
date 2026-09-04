"""图片生成 Pipeline。

从数据库取实体数据 → 构建 SDXL Prompt → 调 ComfyUI 生图。
"""
from __future__ import annotations

import random
import re
from pathlib import Path
from typing import Any

from sqlalchemy import select

from app.adapters.comfyui import ComfyUIClient
from app.core.logger import logger
from app.database.db import get_session
from app.database.models import Entity

# ── Prompt 构建 ───────────────────────────────────────────

QUALITY_TAGS = (
    "masterpiece, best quality, highly detailed, photorealistic, cinematic, 8k, raw photo, "
    "casting-grade portrait, refined East Asian facial harmony, expressive eyes, "
    "balanced features, elegant jawline, graceful cheekbones, healthy luminous skin, "
    "natural attractive face, premium Chinese xianxia period-drama styling"
)
NEGATIVE_PROMPT = (
    "ugly, deformed, disfigured, bad anatomy, bad proportions, bad hands, "
    "extra limbs, fused fingers, too many fingers, long neck, "
    "lowres, bad anatomy, bad hands, text, error, missing fingers, "
    "extra digit, fewer digits, cropped, worst quality, low quality, "
    "normal quality, jpeg artifacts, signature, watermark, username, blurry, "
    "cartoon, anime, illustration, painting, 3d render, "
    "asian stereotype, exaggerated features, distorted face, asymmetrical eyes, "
    "bad face, bad eyes, cross-eyed, mutated hands, extra fingers, ordinary-looking, "
    "plain face, generic face, average face, forgettable face, plastic skin, waxy skin, "
    "over-smoothed skin, excessive makeup, influencer makeup, childlike face, uncanny valley"
)

STYLE_TAGS: dict[str, str] = {
    "character": "solo, full body, standing, dynamic pose, "
                 "chinese fantasy, xianxia, looking at viewer, "
                 "photorealistic, cinematic, 8k",
    "location": "scenery, landscape, no humans, chinese fantasy architecture, "
                "detailed background, wide shot, "
                "photorealistic, cinematic, 8k",
}

MALE_POSITIVE = (
    "CASTING SEX LOCK: MALE, 1man, one unmistakably male young adult East Asian actor, "
    "exceptionally handsome, masculine craniofacial structure, defined brow ridge, "
    "straight masculine eyebrows, firm cheek and jaw planes, natural male hairline, "
    "athletic neck and shoulders, restrained male grooming, natural lips, never feminine"
)
FEMALE_POSITIVE = (
    "CASTING SEX LOCK: FEMALE, 1woman, one unmistakably female young adult East Asian actor, "
    "exceptionally beautiful, graceful feminine facial structure, luminous eyes, elegant "
    "cheek and jaw contours, tasteful period-drama makeup, never masculine"
)
MALE_NEGATIVE = (
    "woman, female, girl, feminine face, female body, breasts, feminine costume, lipstick, "
    "rouge, blush, eyeliner, false eyelashes, feminine contouring, androgynous face, "
    "gender swap, overly delicate doll face, tiny pointed chin"
)
FEMALE_NEGATIVE = (
    "man, male, boy, masculine face, male body, beard, moustache, stubble, facial hair, "
    "masculine brow ridge, gender swap"
)


def _detect_character_gender(description: str) -> str:
    value = description.casefold()
    if re.search(r"\b(?:female|woman|women|girl|lady|heroine|swordswoman)\b", value) or any(
        term in value for term in ("女性", "女人", "少女", "姑娘", "小姐", "女主")
    ):
        return "female"
    if re.search(r"\b(?:male|man|men|boy|gentleman|hero|nobleman|guard|warrior)\b", value) or any(
        term in value for term in ("男性", "男人", "少年", "公子", "少爷", "男主")
    ):
        return "male"
    return "unknown"

TYPE_STYLE: dict[str, str] = {
    "character": STYLE_TAGS["character"],
    "location": STYLE_TAGS["location"],
    "organization": STYLE_TAGS["location"],
    "prop": "still life, detailed, intricate design, chinese fantasy artifact, "
            "photorealistic, cinematic, 8k",
    "ability": "magic effect, glowing, energy, chinese fantasy, particle effects, "
               "photorealistic, cinematic, 8k",
    "creature": "monster, mythical beast, chinese fantasy creature, detailed scales, "
                "photorealistic, cinematic, 8k",
}


def build_prompt(
    name: str,
    description: str = "",
    entity_type: str = "character",
    *,
    extra_tags: str = "",
) -> tuple[str, str]:
    """根据实体名和描述构建 SDXL 正/负向 prompt。"""
    style = TYPE_STYLE.get(entity_type, STYLE_TAGS["character"])

    desc_part = f", {description}" if description else ""

    if entity_type == "character":
        gender = _detect_character_gender(description)
        gender_positive = (
            MALE_POSITIVE if gender == "male" else FEMALE_POSITIVE if gender == "female" else ""
        )
        gender_negative = (
            MALE_NEGATIVE if gender == "male" else FEMALE_NEGATIVE if gender == "female" else ""
        )
        prompt = (
            f"{QUALITY_TAGS}, {style}, "
            f"{gender_positive}, "
            f"character name: {name}, {name}, "
            "professional casting portrait, chest-up three-quarter view, clean catchlights, "
            "restrained professional period-drama grooming, elegant composed expression, "
            "distinctive memorable facial identity"
            f"{desc_part}"
            f"{', ' + extra_tags if extra_tags else ''}"
        )
    elif entity_type == "location":
        prompt = (
            f"{QUALITY_TAGS}, {style}, "
            f"location: {name}, {name}"
            f"{desc_part}"
            f"{', ' + extra_tags if extra_tags else ''}"
        )
    else:
        prompt = (
            f"{QUALITY_TAGS}, {style}, "
            f"{name}"
            f"{desc_part}"
            f"{', ' + extra_tags if extra_tags else ''}"
        )
    if entity_type == "character":
        negative = f"{NEGATIVE_PROMPT}, {gender_negative}" if gender_negative else NEGATIVE_PROMPT
        return prompt, negative
    return prompt, NEGATIVE_PROMPT


# ── SDXL Workflow 构建 ────────────────────────────────────

def build_sdxl_workflow(
    positive_prompt: str,
    negative_prompt: str,
    *,
    width: int = 1024,
    height: int = 1024,
    steps: int = 25,
    cfg: float = 7.0,
    seed: int | None = None,
    sampler: str = "euler_ancestral",
    scheduler: str = "normal",
    batch_size: int = 1,
    filename_prefix: str = "novel2anime",
    checkpoint: str = "majicmixRealistic_v7.safetensors",
    ipadapter_image: str = "",
    ipadapter_weight: float = 0.8,
) -> dict[str, Any]:
    """构建 ComfyUI txt2img workflow JSON（SDXL/SD1.5 通用）。可选 IPAdapter。"""
    if seed is None:
        seed = random.randint(1, 2**31 - 1)

    workflow: dict[str, Any] = {
        "1": {
            "inputs": {"ckpt_name": checkpoint},
            "class_type": "CheckpointLoaderSimple",
            "_meta": {"title": "加载模型"},
        },
        "2": {
            "inputs": {
                "text": positive_prompt,
                "clip": ["1", 1],
            },
            "class_type": "CLIPTextEncode",
            "_meta": {"title": "正向提示词"},
        },
        "3": {
            "inputs": {
                "text": negative_prompt,
                "clip": ["1", 1],
            },
            "class_type": "CLIPTextEncode",
            "_meta": {"title": "负向提示词"},
        },
        "4": {
            "inputs": {
                "width": width,
                "height": height,
                "batch_size": batch_size,
            },
            "class_type": "EmptyLatentImage",
            "_meta": {"title": "空潜空间"},
        },
    }

    # 模型路由：是否经过 IPAdapter
    model_source = ["1", 0]  # 默认直接从 CheckpointLoader 取 model

    if ipadapter_image:
        workflow["8"] = {
            "inputs": {"image": ipadapter_image},
            "class_type": "LoadImage",
            "_meta": {"title": "加载参考图 (IPAdapter)"},
        }
        workflow["9"] = {
            "inputs": {
                "ipadapter_file": "ip-adapter-plus-face_sdxl_vit-h.safetensors",
            },
            "class_type": "IPAdapterModelLoader",
            "_meta": {"title": "加载 IPAdapter 模型"},
        }
        workflow["10"] = {
            "inputs": {
                "model": ["1", 0],
                "ipadapter": ["9", 0],
                "image": ["8", 0],
                "weight": ipadapter_weight,
                "weight_type": "composition",
                "combine_embeds": "concat",
                "start_at": 0.0,
                "end_at": 1.0,
                "embeds_scaling": "K+V",
            },
            "class_type": "IPAdapterAdvanced",
            "_meta": {"title": "IPAdapter 一致性控制"},
        }
        model_source = ["10", 0]

    workflow["5"] = {
        "inputs": {
            "seed": seed,
            "steps": steps,
            "cfg": cfg,
            "sampler_name": sampler,
            "scheduler": scheduler,
            "denoise": 1.0,
            "model": model_source,
            "positive": ["2", 0],
            "negative": ["3", 0],
            "latent_image": ["4", 0],
        },
        "class_type": "KSampler",
        "_meta": {"title": "采样器"},
    }
    workflow["6"] = {
        "inputs": {
            "samples": ["5", 0],
            "vae": ["1", 2],
        },
        "class_type": "VAEDecode",
        "_meta": {"title": "VAE 解码"},
    }
    workflow["7"] = {
        "inputs": {
            "images": ["6", 0],
            "filename_prefix": filename_prefix,
        },
        "class_type": "SaveImage",
        "_meta": {"title": "保存图片"},
    }

    return workflow


# ── 图片生成 ──────────────────────────────────────────────

def generate_character_image(
    name: str,
    description: str = "",
    *,
    output_dir: str | Path,
    client: ComfyUIClient | None = None,
    **kwargs: Any,
) -> list[Path]:
    """生成单个人物图片。"""
    comfy = client or ComfyUIClient()
    prompt, neg = build_prompt(name, description, entity_type="character")
    workflow = build_sdxl_workflow(
        prompt,
        neg,
        filename_prefix=f"char_{name}",
        **kwargs,
    )
    logger.info(f"生成人物 [{name}]: {prompt[:120]}...")
    return comfy.generate(workflow, output_dir, filename_prefix=f"char_{name}")


def generate_from_entities(
    output_dir: str | Path,
    *,
    entity_type: str | None = None,
    limit: int = 3,
    client: ComfyUIClient | None = None,
    **kwargs: Any,
) -> list[Path]:
    """从数据库取实体并批量生成图片。"""
    comfy = client or ComfyUIClient()
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    with get_session() as session:
        stmt = select(Entity).order_by(Entity.first_chapter_order)
        if entity_type:
            stmt = stmt.where(Entity.entity_type == entity_type)
        stmt = stmt.limit(limit)
        entities = list(session.scalars(stmt))

    if not entities:
        logger.warning("数据库中无实体数据，请先执行 compile")
        return []

    results: list[Path] = []
    for entity in entities:
        logger.info(
            f"生成 [{entity.entity_type}] {entity.canonical_name}: "
            f"{entity.description[:60]}"
        )
        prompt, neg = build_prompt(
            entity.canonical_name,
            entity.description,
            entity_type=entity.entity_type,
        )
        workflow = build_sdxl_workflow(
            prompt,
            neg,
            filename_prefix=f"{entity.entity_type}_{entity.canonical_name}",
            **kwargs,
        )
        try:
            saved = comfy.generate(
                workflow,
                out,
                filename_prefix=f"{entity.entity_type}_{entity.canonical_name}",
            )
            results.extend(saved)
        except Exception as exc:
            logger.error(f"生成失败 [{entity.canonical_name}]: {exc}")

    return results


def generate_custom(
    prompt: str,
    output_dir: str | Path,
    *,
    negative_prompt: str = "",
    client: ComfyUIClient | None = None,
    **kwargs: Any,
) -> list[Path]:
    """用自定义 prompt 生成图片。"""
    comfy = client or ComfyUIClient()
    neg = negative_prompt or NEGATIVE_PROMPT
    workflow = build_sdxl_workflow(
        prompt,
        neg,
        filename_prefix="custom",
        **kwargs,
    )
    safe_name = prompt[:30].replace(" ", "_").replace(",", "").replace(".", "")
    logger.info(f"自定义生成: {prompt[:100]}...")
    return comfy.generate(workflow, output_dir, filename_prefix=safe_name)
