#!/usr/bin/env bash
set -euo pipefail

export HF_ENDPOINT="${HF_ENDPOINT:-https://hf-mirror.com}"
comfy_root="${COMFY_ROOT:-/root/autodl-tmp/ComfyUI}"
model_root="$comfy_root/models"
repo="Comfy-Org/Wan_2.2_ComfyUI_Repackaged"

mkdir -p "$model_root/diffusion_models" "$model_root/vae"

download_file() {
  local source_path="$1"
  local destination_path="$2"
  local expected_size="$3"
  local destination="$model_root/$destination_path"
  local current_size=0
  if [ -f "$destination" ]; then
    current_size="$(stat -c '%s' "$destination")"
  fi
  if [ "$current_size" = "$expected_size" ] && [ ! -f "$destination.aria2" ]; then
    echo "[FLF_FILE_READY] $destination_path $expected_size"
    return
  fi
  if [ "$current_size" -gt "$expected_size" ]; then
    echo "Unexpected oversized file; refusing to overwrite: $destination" >&2
    exit 3
  fi

  echo "[FLF_DOWNLOAD] $destination_path $current_size/$expected_size"
  local url="$HF_ENDPOINT/$repo/resolve/main/$source_path?download=true"
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
    echo "Size verification failed: $destination_path $current_size/$expected_size" >&2
    exit 4
  fi
  echo "[FLF_FILE_READY] $destination_path $expected_size"
}

required_bytes=0
while read -r destination expected; do
  current=0
  if [ -f "$model_root/$destination" ]; then
    logical_size="$(stat -c '%s' "$model_root/$destination")"
    if [ "$logical_size" -gt "$expected" ]; then
      echo "Unexpected oversized file: $model_root/$destination" >&2
      exit 3
    fi
    if [ "$logical_size" = "$expected" ] && [ ! -f "$model_root/$destination.aria2" ]; then
      current="$expected"
    else
      current="$(( $(stat -c '%b' "$model_root/$destination") * 512 ))"
      if [ "$current" -gt "$expected" ]; then current="$expected"; fi
    fi
  fi
  required_bytes=$((required_bytes + expected - current))
done <<'FILES'
diffusion_models/wan2.2_i2v_high_noise_14B_fp8_scaled.safetensors 14294742832
diffusion_models/wan2.2_i2v_low_noise_14B_fp8_scaled.safetensors 14294742832
vae/wan_2.1_vae.safetensors 253815318
FILES

available_bytes="$(df -PB1 "$model_root" | awk 'NR==2 {print $4}')"
reserve_bytes=$((12 * 1024 * 1024 * 1024))
if [ "$available_bytes" -lt $((required_bytes + reserve_bytes)) ]; then
  echo "Insufficient disk: need $required_bytes bytes plus 12GiB reserve; available $available_bytes." >&2
  exit 5
fi

echo "[FLF_STAGE] downloading_models missing_bytes=$required_bytes"
download_file \
  split_files/diffusion_models/wan2.2_i2v_high_noise_14B_fp8_scaled.safetensors \
  diffusion_models/wan2.2_i2v_high_noise_14B_fp8_scaled.safetensors \
  14294742832
download_file \
  split_files/diffusion_models/wan2.2_i2v_low_noise_14B_fp8_scaled.safetensors \
  diffusion_models/wan2.2_i2v_low_noise_14B_fp8_scaled.safetensors \
  14294742832
download_file \
  split_files/vae/wan_2.1_vae.safetensors \
  vae/wan_2.1_vae.safetensors \
  253815318

echo "[FLF_STAGE] models_ready"
du -h \
  "$model_root/diffusion_models/wan2.2_i2v_high_noise_14B_fp8_scaled.safetensors" \
  "$model_root/diffusion_models/wan2.2_i2v_low_noise_14B_fp8_scaled.safetensors" \
  "$model_root/vae/wan_2.1_vae.safetensors"
