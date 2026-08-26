#!/usr/bin/env bash
set -euo pipefail

COMFY_ROOT="/root/autodl-tmp/ComfyUI"
MODELS_ROOT="$COMFY_ROOT/models"
HF_ENDPOINT="${HF_ENDPOINT:-https://hf-mirror.com}"

mkdir -p \
  "$MODELS_ROOT/diffusion_models" \
  "$MODELS_ROOT/text_encoders" \
  "$MODELS_ROOT/vae" \
  "$MODELS_ROOT/loras"

download() {
  local repo="$1"
  local remote_path="$2"
  local destination="$3"
  local minimum_bytes="$4"
  local url="$HF_ENDPOINT/$repo/resolve/main/$remote_path"
  if [[ -f "$destination" ]] && (( $(stat -c%s "$destination") >= minimum_bytes )); then
    echo "[CAST_MODELS] ready $(basename "$destination")"
    return
  fi
  echo "[CAST_MODELS] downloading $(basename "$destination")"
  curl --fail --location --retry 12 --retry-delay 5 --continue-at - \
    --output "$destination" "$url"
  local actual
  actual=$(stat -c%s "$destination")
  if (( actual < minimum_bytes )); then
    echo "File too small: $destination ($actual bytes)" >&2
    exit 1
  fi
  echo "[CAST_MODELS] verified $(basename "$destination") $actual"
}

download \
  "Comfy-Org/z_image_turbo" \
  "split_files/diffusion_models/z_image_turbo_int8_convrot.safetensors" \
  "$MODELS_ROOT/diffusion_models/z_image_turbo_int8_convrot.safetensors" \
  6000000000
download \
  "Comfy-Org/z_image_turbo" \
  "split_files/text_encoders/qwen_3_4b_fp8_mixed.safetensors" \
  "$MODELS_ROOT/text_encoders/qwen_3_4b_fp8_mixed.safetensors" \
  5400000000
download \
  "Comfy-Org/z_image_turbo" \
  "split_files/vae/ae.safetensors" \
  "$MODELS_ROOT/vae/z_image_ae.safetensors" \
  300000000

download \
  "cardamonnl/qwen-image-edit-2511-int8-convrot" \
  "qwen_image_edit_2511_int8_convrot.safetensors" \
  "$MODELS_ROOT/diffusion_models/qwen_image_edit_2511_int8_convrot.safetensors" \
  20000000000
download \
  "Comfy-Org/Qwen-Image_ComfyUI" \
  "split_files/text_encoders/qwen_2.5_vl_7b_fp8_scaled.safetensors" \
  "$MODELS_ROOT/text_encoders/qwen_2.5_vl_7b_fp8_scaled.safetensors" \
  9000000000
download \
  "Comfy-Org/Qwen-Image_ComfyUI" \
  "split_files/vae/qwen_image_vae.safetensors" \
  "$MODELS_ROOT/vae/qwen_image_vae.safetensors" \
  240000000
download \
  "fal/Qwen-Image-Edit-2511-Multiple-Angles-LoRA" \
  "qwen-image-edit-2511-multiple-angles-lora.safetensors" \
  "$MODELS_ROOT/loras/qwen-image-edit-2511-multiple-angles-lora.safetensors" \
  260000000

echo "[CAST_MODELS] all_ready"
