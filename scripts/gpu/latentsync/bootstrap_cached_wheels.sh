#!/usr/bin/env bash
set -euo pipefail

env_dir="${LATENTSYNC_ENV:-/root/autodl-tmp/latentsync-env}"
wheelhouse="${LATENTSYNC_WHEELHOUSE:-/root/autodl-tmp/latentsync-wheelhouse}"
mkdir -p "$wheelhouse"

copy_cached() {
  local pattern="$1"
  local source
  source=$(find /tmp /root/.cache/pip -type f -name "$pattern" -print -quit 2>/dev/null || true)
  if [ -n "$source" ]; then
    cp -n "$source" "$wheelhouse/"
  fi
}

copy_cached 'torch-2.5.1+cu121-cp310-cp310-linux_x86_64.whl'
copy_cached 'onnxruntime_gpu-1.21.0-cp310-*.whl'
copy_cached 'nvidia_cuda_nvrtc_cu12-12.1.105-*.whl'
copy_cached 'nvidia_cuda_runtime_cu12-12.1.105-*.whl'
copy_cached 'nvidia_cuda_cupti_cu12-12.1.105-*.whl'
copy_cached 'nvidia_cublas_cu12-12.1.3.1-*.whl'
copy_cached 'nvidia_cufft_cu12-11.0.2.54-*.whl'
copy_cached 'nvidia_curand_cu12-10.3.2.106-*.whl'
copy_cached 'nvidia_cusolver_cu12-11.4.5.107-*.whl'
copy_cached 'nvidia_cusparse_cu12-12.1.0.106-*.whl'
copy_cached 'nvidia_nvtx_cu12-12.1.105-*.whl'

download() {
  local url="$1"
  local filename="$2"
  aria2c -c -x 16 -s 16 -k 1M \
    --user-agent='pip/26.0' \
    --referer='https://mirrors.aliyun.com/pypi/simple/' \
    --retry-wait=3 \
    --max-tries=30 \
    --timeout=60 \
    --file-allocation=none \
    --dir="$wheelhouse" \
    --out="$filename" \
    "$url"
}

download \
  'https://mirrors.aliyun.com/pypi/packages/9f/fd/713452cd72343f682b1c7b9321e23829f00b842ceaedcda96e742ea0b0b3/nvidia_cudnn_cu12-9.1.0.70-py3-none-manylinux2014_x86_64.whl' \
  'nvidia_cudnn_cu12-9.1.0.70-py3-none-manylinux2014_x86_64.whl'
download \
  'https://mirrors.aliyun.com/pypi/packages/df/99/12cd266d6233f47d00daf3a72739872bdc10267d0383508b0b9c84a18bb6/nvidia_nccl_cu12-2.21.5-py3-none-manylinux2014_x86_64.whl' \
  'nvidia_nccl_cu12-2.21.5-py3-none-manylinux2014_x86_64.whl'
download \
  'https://mirrors.aliyun.com/pypi/packages/98/29/69aa56dc0b2eb2602b553881e34243475ea2afd9699be042316842788ff5/triton-3.1.0-cp310-cp310-manylinux_2_17_x86_64.manylinux2014_x86_64.whl' \
  'triton-3.1.0-cp310-cp310-manylinux_2_17_x86_64.manylinux2014_x86_64.whl'
download \
  'https://mirrors.aliyun.com/pypi/packages/46/0c/c75bbfb967457a0b7670b8ad267bfc4fffdf341c074e0a80db06c24ccfd4/nvidia_nvjitlink_cu12-12.9.86-py3-none-manylinux2010_x86_64.manylinux_2_12_x86_64.whl' \
  'nvidia_nvjitlink_cu12-12.9.86-py3-none-manylinux2010_x86_64.manylinux_2_12_x86_64.whl'

"$env_dir/bin/python" -m pip install --no-deps "$wheelhouse"/*.whl
echo "LatentSync cached CUDA wheel bootstrap is ready"
