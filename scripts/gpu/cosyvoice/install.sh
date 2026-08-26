#!/usr/bin/env bash
set -euo pipefail

export HF_ENDPOINT="${HF_ENDPOINT:-https://hf-mirror.com}"

source_root=/root/cosyvoice-runtime
source_dir="$source_root/CosyVoice"
env_dir=/root/cosyvoice-env
service_dir=/root/cosyvoice-service
model_dir=/root/cosyvoice-models/Fun-CosyVoice3-0.5B
matcha_commit=dd9105b34bf2be2230f4aa1e4769fb586a3c824e

mkdir -p "$source_root" "$service_dir" "$(dirname "$model_dir")"

if [ ! -f "$source_dir/cosyvoice/cli/cosyvoice.py" ]; then
  archive=/tmp/cosyvoice-main.tar.gz
  curl -fL --retry 3 --connect-timeout 20 -o "$archive" \
    https://codeload.github.com/QwenAudio/CosyVoice/tar.gz/refs/heads/main
  rm -rf "$source_root/CosyVoice-main"
  tar -xzf "$archive" -C "$source_root"
  mv "$source_root/CosyVoice-main" "$source_dir"
  rm -f "$archive"
fi

matcha_dir="$source_dir/third_party/Matcha-TTS"
if [ ! -f "$matcha_dir/matcha/__init__.py" ]; then
  archive=/tmp/matcha-tts.tar.gz
  rm -rf "$matcha_dir"
  mkdir -p "$matcha_dir"
  curl -fL --retry 3 --connect-timeout 20 -o "$archive" \
    "https://codeload.github.com/shivammehta25/Matcha-TTS/tar.gz/$matcha_commit"
  tar -xzf "$archive" --strip-components=1 -C "$matcha_dir"
  rm -f "$archive"
fi

if [ ! -x "$env_dir/bin/python" ]; then
  /root/miniconda3/bin/conda create -y -p "$env_dir" python=3.10
fi

python_bin="$env_dir/bin/python"
"$python_bin" -m pip install --upgrade pip wheel "setuptools<81"
"$python_bin" -m pip install \
  filelock \
  fsspec \
  jinja2 \
  networkx \
  sympy \
  typing-extensions \
  -i https://mirrors.aliyun.com/pypi/simple/ \
  --trusted-host mirrors.aliyun.com
"$python_bin" -m pip install \
  torch==2.3.1 torchaudio==2.3.1 \
  --index-url https://download.pytorch.org/whl/cu121
"$python_bin" -m pip install \
  openai-whisper==20231117 \
  --no-build-isolation \
  -i https://mirrors.aliyun.com/pypi/simple/ \
  --trusted-host mirrors.aliyun.com
"$python_bin" -m pip install \
  conformer==0.3.2 \
  diffusers==0.29.0 \
  fastapi==0.115.6 \
  gdown==5.1.0 \
  huggingface_hub==0.36.0 \
  hydra-core==1.3.2 \
  HyperPyYAML==1.2.3 \
  inflect==7.3.1 \
  librosa==0.10.2 \
  lightning==2.2.4 \
  matplotlib==3.7.5 \
  modelscope==1.20.0 \
  networkx==3.1 \
  numpy==1.26.4 \
  omegaconf==2.3.0 \
  onnx==1.16.0 \
  onnxruntime-gpu==1.18.0 \
  protobuf==4.25.8 \
  pyarrow==18.1.0 \
  pydantic==2.7.0 \
  python-multipart==0.0.20 \
  pyworld==0.3.4 \
  rich==13.7.1 \
  soundfile==0.12.1 \
  transformers==4.51.3 \
  uvicorn==0.30.0 \
  wetext==0.0.4 \
  wget==3.2 \
  x-transformers==2.11.24 \
  -i https://mirrors.aliyun.com/pypi/simple/ \
  --trusted-host mirrors.aliyun.com

export COSYVOICE_MODEL_DIR="$model_dir"
"$python_bin" "$service_dir/download_model.py"
"$python_bin" -m pip cache purge >/dev/null 2>&1 || true

echo "CosyVoice environment and model are ready"
du -sh "$source_root" "$env_dir" "$model_dir"
