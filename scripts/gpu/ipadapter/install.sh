#!/usr/bin/env bash
set -euo pipefail

export HF_ENDPOINT="${HF_ENDPOINT:-https://hf-mirror.com}"

comfy_root="${COMFY_ROOT:-/root/autodl-tmp/ComfyUI}"
clip_dir="$comfy_root/models/clip_vision"
adapter_dir="$comfy_root/models/ipadapter"
clip_file="$clip_dir/CLIP-ViT-H-14-laion2B-s32B-b79K.safetensors"
adapter_file="$adapter_dir/ip-adapter-plus-face_sdxl_vit-h.safetensors"

mkdir -p "$clip_dir" "$adapter_dir" "$comfy_root/custom_nodes"

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

# Install the ComfyUI_IPAdapter_plus custom node so the IPAdapterUnifiedLoader /
# IPAdapter nodes exist. Without these nodes check_status reports
# identity_adapter_ready=False even after the weights land. The package is pure
# Python with no hard pip dependencies. Mirror the official-fetch pattern from
# update_comfyui.sh: bypass the server's global GitHub rewrite via an empty
# global git config so the read-only clone hits github.com directly.
node_dir="$comfy_root/custom_nodes/ComfyUI_IPAdapter_plus"
if [ ! -d "$node_dir/.git" ]; then
  rm -rf "$node_dir"
  cloned=0
  for attempt in 1 2 3; do
    if GIT_CONFIG_GLOBAL=/dev/null git -c http.version=HTTP/1.1 clone \
      --depth 1 https://github.com/cubiq/ComfyUI_IPAdapter_plus.git "$node_dir"; then
      cloned=1
      break
    fi
    echo "ComfyUI_IPAdapter_plus clone attempt $attempt failed; retrying..." >&2
    sleep $((attempt * 3))
  done
  if [ "$cloned" != 1 ]; then
    echo "Failed to clone ComfyUI_IPAdapter_plus; the weights are installed" >&2
    echo "but the nodes are missing — install the node manually." >&2
    exit 3
  fi
else
  echo "ComfyUI_IPAdapter_plus already present, skipping clone"
fi

for node in IPAdapterUnifiedLoader IPAdapter; do
  if ! grep -rq "$node" "$node_dir" --include='*.py'; then
    echo "IPAdapter node not found after clone: $node" >&2
    exit 4
  fi
done

echo "SDXL IP-Adapter Plus Face identity models and nodes are ready"

