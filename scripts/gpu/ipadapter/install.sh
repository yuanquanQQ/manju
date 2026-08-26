#!/usr/bin/env bash
set -euo pipefail

export HF_ENDPOINT="${HF_ENDPOINT:-https://hf-mirror.com}"

comfy_root="${COMFY_ROOT:-/root/autodl-tmp/ComfyUI}"
clip_dir="$comfy_root/models/clip_vision"
adapter_dir="$comfy_root/models/ipadapter"
clip_file="$clip_dir/CLIP-ViT-H-14-laion2B-s32B-b79K.safetensors"
adapter_file="$adapter_dir/ip-adapter-plus-face_sdxl_vit-h.safetensors"

mkdir -p "$clip_dir" "$adapter_dir"

download() {
  local repo_path="$1"
  local destination="$2"
  aria2c -c -x 16 -s 16 -k 1M \
    --user-agent='huggingface_hub/0.30.2' \
    --retry-wait=3 \
    --max-tries=30 \
    --timeout=60 \
    --file-allocation=none \
    --dir="$(dirname "$destination")" \
    --out="$(basename "$destination")" \
    "$HF_ENDPOINT/h94/IP-Adapter/resolve/main/$repo_path?download=true"
}

download "models/image_encoder/model.safetensors" "$clip_file"
download "sdxl_models/ip-adapter-plus-face_sdxl_vit-h.safetensors" "$adapter_file"

printf '%s  %s\n' \
  "6ca9667da1ca9e0b0f75e46bb030f7e011f44f86cbfb8d5a36590fcd7507b030" \
  "$clip_file" | sha256sum -c -
printf '%s  %s\n' \
  "677ad8860204f7d0bfba12d29e6c31ded9beefdf3e4bbd102518357d31a292c1" \
  "$adapter_file" | sha256sum -c -

echo "SDXL IP-Adapter Plus Face identity models are ready"
