#!/usr/bin/env bash
set -euo pipefail

export HF_ENDPOINT="${HF_ENDPOINT:-https://hf-mirror.com}"
comfy_root="${COMFY_ROOT:-/root/autodl-tmp/ComfyUI}"
model_root="$comfy_root/models"
repo="Comfy-Org/flux1-kontext-dev_ComfyUI"
source_path="split_files/diffusion_models/flux1-dev-kontext_fp8_scaled.safetensors"
destination_path="diffusion_models/flux1-dev-kontext_fp8_scaled.safetensors"
expected_size=11904640136
destination="$model_root/$destination_path"

mkdir -p "$model_root/diffusion_models"

for required in \
  "$model_root/text_encoders/clip_l.safetensors" \
  "$model_root/text_encoders/t5xxl_fp8_e4m3fn.safetensors" \
  "$model_root/vae/ae.safetensors"; do
  if [ ! -f "$required" ]; then
    echo "Missing shared FLUX dependency: $required" >&2
    exit 2
  fi
done

current_size=0
if [ -f "$destination" ]; then
  current_size="$(stat -c '%s' "$destination")"
fi
if [ "$current_size" -gt "$expected_size" ]; then
  echo "Unexpected oversized file; refusing to overwrite: $destination" >&2
  exit 3
fi
if [ "$current_size" = "$expected_size" ] && [ ! -f "$destination.aria2" ]; then
  echo "[KONTEXT_FILE_READY] $destination_path $expected_size"
else
  allocated_size=0
  if [ -f "$destination" ]; then
    allocated_size="$(( $(stat -c '%b' "$destination") * 512 ))"
    if [ "$allocated_size" -gt "$expected_size" ]; then
      allocated_size="$expected_size"
    fi
  fi
  missing_bytes=$((expected_size - allocated_size))
  available_bytes="$(df -PB1 "$model_root" | awk 'NR==2 {print $4}')"
  reserve_bytes=$((12 * 1024 * 1024 * 1024))
  if [ "$available_bytes" -lt $((missing_bytes + reserve_bytes)) ]; then
    echo "Insufficient disk: need $missing_bytes bytes plus 12GiB reserve; available $available_bytes." >&2
    exit 5
  fi

  echo "[KONTEXT_DOWNLOAD] $destination_path $current_size/$expected_size"
  url="$HF_ENDPOINT/$repo/resolve/main/$source_path?download=true"
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
fi

current_size="$(stat -c '%s' "$destination")"
if [ "$current_size" != "$expected_size" ]; then
  echo "Size verification failed: $destination_path $current_size/$expected_size" >&2
  exit 4
fi

echo "[KONTEXT_STAGE] models_ready"
du -h "$destination"
