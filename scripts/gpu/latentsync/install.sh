#!/usr/bin/env bash
set -euo pipefail

export HF_ENDPOINT="${HF_ENDPOINT:-https://hf-mirror.com}"
export HF_HOME="${HF_HOME:-/root/autodl-tmp/huggingface}"
export PIP_DEFAULT_TIMEOUT="${PIP_DEFAULT_TIMEOUT:-600}"
export PIP_RETRIES="${PIP_RETRIES:-20}"
export PIP_RESUME_RETRIES="${PIP_RESUME_RETRIES:-20}"

source_dir=/root/autodl-tmp/LatentSync
env_dir=/root/autodl-tmp/latentsync-env
archive=/tmp/latentsync-main.tar.gz

mkdir -p /root/autodl-tmp "$HF_HOME"

if [ ! -f "$source_dir/scripts/inference.py" ]; then
  rm -rf "$source_dir" /root/autodl-tmp/LatentSync-main
  curl -fL --retry 3 --connect-timeout 20 -o "$archive" \
    https://codeload.github.com/bytedance/LatentSync/tar.gz/refs/heads/main
  tar -xzf "$archive" -C /root/autodl-tmp
  mv /root/autodl-tmp/LatentSync-main "$source_dir"
  rm -f "$archive"
fi

if [ ! -x "$env_dir/bin/python" ]; then
  /root/miniconda3/bin/conda create -y -p "$env_dir" python=3.10.13
fi

python_bin="$env_dir/bin/python"
"$python_bin" -m pip install --upgrade pip wheel "setuptools<81"
"$python_bin" -m pip install "numpy==1.26.4" "cython==3.0.12"
wheelhouse=/root/autodl-tmp/latentsync-wheelhouse
if compgen -G "$wheelhouse/*.whl" >/dev/null; then
  "$python_bin" -m pip install --no-deps "$wheelhouse"/*.whl
fi
constraint_file="$source_dir/manju_constraints.txt"
cat > "$constraint_file" <<'EOF'
albumentations==1.4.3
opencv-contrib-python==4.9.0.80
opencv-python-headless==4.9.0.80
onnx==1.16.2
scikit-image==0.22.0
EOF
requirements_installed=0
for attempt in 1 2 3 4; do
  echo "Installing Python dependencies (attempt $attempt/4)"
  if "$python_bin" -m pip install -r "$source_dir/requirements.txt" \
    -c "$constraint_file" \
    -i https://mirrors.aliyun.com/pypi/simple/ \
    --trusted-host mirrors.aliyun.com \
    --no-build-isolation \
    --timeout "$PIP_DEFAULT_TIMEOUT" \
    --retries "$PIP_RETRIES" \
    --resume-retries "$PIP_RESUME_RETRIES"; then
    requirements_installed=1
    break
  fi
  sleep $((attempt * 5))
done
if [ "$requirements_installed" != 1 ]; then
  echo "Dependency installation failed after 4 attempts" >&2
  exit 1
fi

cd "$source_dir"
download_hf() {
  local repo_path="$1"
  local destination="$2"
  mkdir -p "$(dirname "$destination")"
  aria2c -c -x 16 -s 16 -k 1M \
    --user-agent='huggingface_hub/0.30.2' \
    --retry-wait=3 \
    --max-tries=30 \
    --timeout=60 \
    --file-allocation=none \
    --dir="$(dirname "$destination")" \
    --out="$(basename "$destination")" \
    "$HF_ENDPOINT/ByteDance/LatentSync-1.6/resolve/main/$repo_path?download=true"
}

download_hf "whisper/tiny.pt" "$source_dir/checkpoints/whisper/tiny.pt"
download_hf "latentsync_unet.pt" "$source_dir/checkpoints/latentsync_unet.pt"

# inference.py loads this VAE by repository id; warm the persistent cache now.
"$python_bin" - <<'PY'
from diffusers import AutoencoderKL

AutoencoderKL.from_pretrained("stabilityai/sd-vae-ft-mse")
print("Stable Diffusion VAE cache ready")
PY

test -f "$source_dir/checkpoints/latentsync_unet.pt"
test -f "$source_dir/checkpoints/whisper/tiny.pt"
echo "LatentSync 1.6 environment and checkpoints are ready"
du -sh "$source_dir" "$env_dir" "$HF_HOME" 2>/dev/null || true
