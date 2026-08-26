"""生成第1章 8 个镜头图片（majicMIX realistic SD1.5）。"""
import json
from pathlib import Path
from app.adapters.comfyui import ComfyUIClient
from app.core.config import settings
from app.pipeline.generate_image import build_sdxl_workflow, NEGATIVE_PROMPT

EPISODE_FILE = Path("projects/jueshi/production/episodes/episode_001.json")
OUTPUT_DIR = Path("projects/jueshi/assets/shots")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# majicMIX realistic 配置
CHECKPOINT = "majicmixRealistic_v7.safetensors"
WIDTH, HEIGHT = 768, 512  # SD1.5 最佳横构图
STEPS = 25
CFG = 7.5

comfy = ComfyUIClient(base_url=settings.comfyui_url, timeout=settings.comfyui_timeout)
print(f"ComfyUI: {settings.comfyui_url}")

episode = json.loads(EPISODE_FILE.read_text("utf-8"))
shots = episode["shots"]
print(f"共 {len(shots)} 个镜头\n")

for shot in shots:
    num = shot["shot_number"]
    prompt = shot["image_prompt"]
    desc = shot.get("scene_description", "")[:60]

    # 注入美型关键词（在质量标签后）
    if "raw photo," in prompt:
        prompt = prompt.replace(
            "raw photo,",
            "raw photo, perfect face, attractive, beautiful detailed eyes, flawless skin, symmetrical features, ",
        )

    print(f"镜头 {num}/{len(shots)}: {desc}")
    print(f"  prompt: {prompt[:150]}...")

    workflow = build_sdxl_workflow(
        prompt,
        NEGATIVE_PROMPT,
        width=WIDTH,
        height=HEIGHT,
        steps=STEPS,
        cfg=CFG,
        checkpoint=CHECKPOINT,
        filename_prefix=f"ep001_shot{num:02d}",
    )
    try:
        saved = comfy.generate(workflow, str(OUTPUT_DIR), filename_prefix=f"ep001_shot{num:02d}")
        print(f"  -> {len(saved)} 张: {[p.name for p in saved]}\n")
    except Exception as exc:
        print(f"  -> 失败: {exc}\n")
