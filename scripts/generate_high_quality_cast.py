"""Orchestrate the gated Chinese cast workflow on the configured GPU server."""

from __future__ import annotations

import argparse
import json
import posixpath
import re
import shlex
import time
from datetime import datetime
from pathlib import Path

from dotenv import dotenv_values

from app.services.gpu_service import GpuConnection, GpuServerService
from scripts.generate_episode_h3 import _ssh_password

MODEL_FILES = (
    "models/diffusion_models/z_image_turbo_int8_convrot.safetensors",
    "models/text_encoders/qwen_3_4b_fp8_mixed.safetensors",
    "models/vae/z_image_ae.safetensors",
    "models/diffusion_models/qwen_image_edit_2511_int8_convrot.safetensors",
    "models/text_encoders/qwen_2.5_vl_7b_fp8_scaled.safetensors",
    "models/vae/qwen_image_vae.safetensors",
    "models/loras/qwen-image-edit-2511-multiple-angles-lora.safetensors",
)

REQUIRED_NODES = (
    "ModelSamplingAuraFlow",
    "TextEncodeQwenImageEditPlus",
    "FluxKontextMultiReferenceLatentMethod",
    "CFGNorm",
    "LoraLoaderModelOnly",
)


def build_casting_prompt(character: str, profile: str, fingerprint: str) -> str:
    """Compile a casting brief that favors real Chinese period-drama actors."""

    clean_fingerprint = re.split(r"[；。]?\s*(?:严格沿用|正面).*", fingerprint)[0]
    return (
        f"为中国古装玄幻真人短剧《绝世丹神》选角，角色：{character}。\n"
        f"角色硬性设定：{profile}\n"
        f"视觉指纹：{clean_fingerprint}\n\n"
        "画面必须是且仅是一位中国籍、汉族面部特征明确的真人青年演员，不是泛东亚混合脸，"
        "不是韩国偶像，不是网红，不是欧美混血感。以中国一线古装电视剧主演的上镜标准选角："
        "五官骨相端正、比例协调、辨识度高，眼神有故事感，正脸和侧脸都经得住高清特写。"
        "男性必须俊美但明确阳刚：清晰眉骨、利落颧颌面、自然男性下颌与颈肩线，禁止女性化"
        "尖下巴、浓眼线、口红、脂粉感、奶油网红脸和幼态娃娃脸。皮肤保留真实毛孔和轻微"
        "纹理，妆容克制专业，不磨皮、不塑料、不蜡像。\n\n"
        "这是单幅、单一构图的演员试镜定妆照：只拍一位演员的肩部以上肖像，正面略带自然"
        "三分之二侧脸，脸部占画面高度约一半，眼神和五官必须足以进行选角判断。画面中只"
        "能出现这一个人的一张脸和一副肩膀，禁止任何全身、第二张脸、并排展示或重复形象。"
        "服装是高预算中国古装剧级别：真实丝、麻、皮革织物"
        "层次，剪裁合体，纹样克制，做旧和垂坠自然；禁止廉价影楼服、塑料盔甲、夸张仙侠"
        "头饰。背景为简洁的中性深灰摄影棚，85mm长焦人像质感，平视机位，柔和主光、自然"
        "补光和微弱轮廓光，电影级冷暖综合色调，高动态范围，面部、发丝和衣料均清晰。\n\n"
        "严格禁止：第二个人、第二张脸、同一人物的重复形象、并排角度展示、全身展示、拼图、分栏、文字、伪文字、"
        "标识、水印、武器漂移、"
        "额外首饰、现代服装、日漫、插画、3D塑料感、AI畸形脸、大小眼、斜视、模糊眼睛、"
        "多余肢体、坏手、断指、融合手指、裁掉头脚。"
    )


def _connection(workspace: Path) -> GpuConnection:
    env = dotenv_values(workspace / ".env")
    return GpuConnection(
        host=str(env.get("GPU_SSH_HOST") or ""),
        port=int(env.get("GPU_SSH_PORT") or 22),
        username=str(env.get("GPU_SSH_USER") or "root"),
        password=_ssh_password(workspace / "ssh.txt"),
    )


def _episode_data(workspace: Path, project: str, episode: int) -> dict:
    path = (
        workspace
        / "projects"
        / project
        / "production"
        / "episodes"
        / f"episode_{episode:03d}.json"
    )
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _preflight(
    service: GpuServerService,
    client,
    *,
    stage: str = "all",
) -> None:
    comfy_root = service.remote_comfy_root
    required_files = MODEL_FILES[:3] if stage == "casting" else MODEL_FILES
    required_nodes = (
        ("ModelSamplingAuraFlow",)
        if stage == "casting"
        else REQUIRED_NODES
    )
    quoted_files = " ".join(
        shlex.quote(posixpath.join(comfy_root, relative))
        for relative in required_files
    )
    command = (
        "for file in "
        f"{quoted_files}; do "
        "test -s \"$file\" || { echo \"missing:$file\"; exit 31; }; done; "
        "curl -fsS http://127.0.0.1:8188/object_info"
    )
    output = service._exec(client, command, timeout=45)
    missing_nodes = [name for name in required_nodes if f'"{name}"' not in output]
    if missing_nodes:
        raise RuntimeError(
            "ComfyUI missing required cast nodes: " + ", ".join(missing_nodes)
        )


def _download_tree(service: GpuServerService, client, remote_dir: str, local_dir: Path) -> None:
    local_dir.mkdir(parents=True, exist_ok=True)
    sftp = client.open_sftp()
    try:
        for name in sorted(sftp.listdir(remote_dir)):
            if name.lower().endswith((".png", ".jpg", ".json")):
                sftp.get(posixpath.join(remote_dir, name), str(local_dir / name))
    finally:
        sftp.close()


def run_casting(args: argparse.Namespace) -> Path:
    workspace = args.workspace.resolve()
    episode = _episode_data(workspace, args.project, args.episode)
    profiles = episode.get("character_profiles") or {}
    fingerprints = episode.get("character_visual_fingerprints") or {}
    if args.character not in profiles:
        raise KeyError(f"episode does not define character: {args.character}")
    prompt = build_casting_prompt(
        args.character,
        str(profiles[args.character]),
        str(fingerprints.get(args.character) or ""),
    )

    service = GpuServerService()
    config = _connection(workspace)
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_character = re.sub(r"[^A-Za-z0-9_-]+", "_", args.character).strip("_") or "cast"
    remote_dir = f"{service.remote_project_root}/outputs/chinese_cast/{run_id}_{safe_character}"
    remote_workflow_dir = f"{service.remote_project_root}/workflows/chinese_cast"
    remote_workflow = f"{remote_workflow_dir}/generate_cast.py"
    remote_prompt = f"{remote_dir}/casting_prompt.txt"
    local_dir = (
        workspace
        / "projects"
        / args.project
        / "outputs"
        / "chinese_cast"
        / f"{run_id}_{args.character}"
    )
    workflow = workspace / "workflows" / "chinese_cast" / "generate_cast.py"

    client = service._connect(config)
    try:
        service._ensure_remote_comfy(client)
        _preflight(service, client, stage="casting")
        service._exec(
            client,
            f"mkdir -p {shlex.quote(remote_workflow_dir)} {shlex.quote(remote_dir)}",
            timeout=15,
        )
        sftp = client.open_sftp()
        try:
            sftp.put(str(workflow), remote_workflow)
            with sftp.file(remote_prompt, "wb") as handle:
                handle.write(prompt.encode("utf-8"))
        finally:
            sftp.close()
        command = " ".join(
            [
                "/root/miniconda3/bin/python",
                shlex.quote(remote_workflow),
                "casting",
                "--prompt-file",
                shlex.quote(remote_prompt),
                "--output-dir",
                shlex.quote(remote_dir),
                "--run-name",
                shlex.quote(f"{run_id}_{safe_character}"),
                "--candidate-count",
                str(args.count),
                "--seed",
                str(args.seed),
                "--width",
                str(args.width),
                "--height",
                str(args.height),
                "--steps",
                str(args.steps),
            ]
        )
        service._exec_streaming(
            client,
            command,
            timeout=7200,
            output_callback=lambda line: print(line, flush=True),
        )
        _download_tree(service, client, remote_dir, local_dir)
    finally:
        client.close()
    (local_dir / "casting_prompt.txt").write_text(prompt, encoding="utf-8")
    print(f"[OUTPUT_ROOT] {local_dir}", flush=True)
    return local_dir


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", default="jueshi")
    parser.add_argument("--episode", type=int, default=1)
    parser.add_argument("--character", default="秦风")
    parser.add_argument("--count", type=int, default=4, choices=range(4, 9))
    parser.add_argument("--seed", type=int, default=2026082601)
    parser.add_argument("--width", type=int, default=896)
    parser.add_argument("--height", type=int, default=1344)
    parser.add_argument("--steps", type=int, default=9, choices=range(8, 13))
    parser.add_argument("--workspace", type=Path, default=Path.cwd())
    return parser.parse_args()


if __name__ == "__main__":
    started = time.monotonic()
    output = run_casting(parse_args())
    print(f"[DONE] elapsed={time.monotonic() - started:.1f}s dir={output}")
