#!/usr/bin/env bash
set -euo pipefail

export HF_ENDPOINT="${HF_ENDPOINT:-https://hf-mirror.com}"
comfy_root="${COMFY_ROOT:-/root/autodl-tmp/ComfyUI}"
model_root="$comfy_root/models"
repo="Comfy-Org/MiniMax-H3"

mkdir -p \
  "$model_root/diffusion_models" \
  "$model_root/text_encoders" \
  "$model_root/vae"

download_file() {
  local relative_path="$1"
  local expected_size="$2"
  local destination="$model_root/$relative_path"
  local current_size=0
  if [ -f "$destination" ]; then
    current_size="$(stat -c '%s' "$destination")"
  fi
  if [ "$current_size" = "$expected_size" ] && [ ! -f "$destination.aria2" ]; then
    echo "[H3_FILE_READY] $relative_path $expected_size"
    return
  fi
  if [ "$current_size" -gt "$expected_size" ]; then
    echo "Unexpected oversized file; refusing to overwrite: $destination" >&2
    exit 3
  fi

  echo "[H3_DOWNLOAD] $relative_path $current_size/$expected_size"
  local url="$HF_ENDPOINT/$repo/resolve/main/$relative_path?download=true"
  if command -v aria2c >/dev/null 2>&1; then
    aria2c \
      --continue=true \
      --max-connection-per-server=8 \
      --split=8 \
      --min-split-size=16M \
      --file-allocation=none \
      --auto-file-renaming=false \
      --allow-overwrite=true \
      --summary-interval=15 \
      --dir="$(dirname "$destination")" \
      --out="$(basename "$destination")" \
      "$url"
  else
    curl --fail --location --retry 20 --retry-delay 5 \
      --continue-at - --output "$destination" "$url"
  fi

  current_size="$(stat -c '%s' "$destination")"
  if [ "$current_size" != "$expected_size" ]; then
    echo "Size verification failed: $relative_path $current_size/$expected_size" >&2
    exit 4
  fi
  echo "[H3_FILE_READY] $relative_path $expected_size"
}

required_bytes=0
while read -r relative expected; do
  current=0
  if [ -f "$model_root/$relative" ]; then
    logical_size="$(stat -c '%s' "$model_root/$relative")"
    if [ "$logical_size" -gt "$expected" ]; then
      echo "Unexpected oversized file: $model_root/$relative" >&2
      exit 3
    fi
    if [ "$logical_size" = "$expected" ] && [ ! -f "$model_root/$relative.aria2" ]; then
      current="$expected"
    else
      current="$(( $(stat -c '%b' "$model_root/$relative") * 512 ))"
      if [ "$current" -gt "$expected" ]; then current="$expected"; fi
    fi
  fi
  required_bytes=$((required_bytes + expected - current))
done <<'FILES'
diffusion_models/minimax_h3_fl2va_pruned_int8_convrot.safetensors 20970379616
text_encoders/qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors 15687142551
vae/minimax_h3_video_vae_fp16.safetensors 5207808496
vae/minimax_h3_audio_vae_fp32.safetensors 605254808
FILES

available_bytes="$(df -PB1 "$model_root" | awk 'NR==2 {print $4}')"
reserve_bytes=$((8 * 1024 * 1024 * 1024))
if [ "$available_bytes" -lt $((required_bytes + reserve_bytes)) ]; then
  echo "Insufficient disk space: need $required_bytes bytes plus 8GiB reserve; available $available_bytes." >&2
  exit 5
fi

echo "[H3_STAGE] downloading_models missing_bytes=$required_bytes"
download_file diffusion_models/minimax_h3_fl2va_pruned_int8_convrot.safetensors 20970379616
download_file text_encoders/qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors 15687142551
download_file vae/minimax_h3_video_vae_fp16.safetensors 5207808496
download_file vae/minimax_h3_audio_vae_fp32.safetensors 605254808

license_path="$model_root/minimax_h3_LICENSE.txt"
curl --fail --location --retry 5 \
  "$HF_ENDPOINT/MiniMaxAI/MiniMax-H3/resolve/main/LICENSE?download=true" \
  --output "$license_path"

echo "[H3_STAGE] models_ready"
du -h \
  "$model_root/diffusion_models/minimax_h3_fl2va_pruned_int8_convrot.safetensors" \
  "$model_root/text_encoders/qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors" \
  "$model_root/vae/minimax_h3_video_vae_fp16.safetensors" \
  "$model_root/vae/minimax_h3_audio_vae_fp32.safetensors"
