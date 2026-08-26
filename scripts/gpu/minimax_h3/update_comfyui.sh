#!/usr/bin/env bash
set -euo pipefail

comfy_root="${COMFY_ROOT:-/root/autodl-tmp/ComfyUI}"
python_bin="${COMFY_PYTHON:-/root/miniconda3/bin/python}"
backup_file="/root/autodl-tmp/comfyui_before_minimax_h3_commit.txt"

if [ ! -d "$comfy_root/.git" ]; then
  echo "ComfyUI Git repository not found: $comfy_root" >&2
  exit 1
fi

cd "$comfy_root"
if ! git diff --quiet || ! git diff --cached --quiet; then
  echo "ComfyUI has tracked local changes; refusing to update automatically." >&2
  git status --short >&2
  exit 2
fi

git rev-parse HEAD > "$backup_file"
echo "[H3_STAGE] updating_comfyui"
if git grep -q "MiniMaxH3ImageToVideo" HEAD -- comfy comfy_extras nodes.py 2>/dev/null; then
  echo "[H3_STAGE] comfyui_h3_code_already_present"
else
  # This server image may contain a stale global GitHub-to-ghproxy rewrite.
  # Bypass only that global file for the official, read-only fetch.
  fetched=0
  for attempt in 1 2 3; do
    if GIT_CONFIG_GLOBAL=/dev/null git -c http.version=HTTP/1.1 fetch \
      --depth=512 https://github.com/Comfy-Org/ComfyUI.git master; then
      fetched=1
      break
    fi
    echo "Official ComfyUI fetch attempt $attempt failed; retrying..." >&2
    sleep $((attempt * 3))
  done
  if [ "$fetched" != 1 ]; then
    exit 6
  fi
  git merge --ff-only FETCH_HEAD
fi

echo "[H3_STAGE] installing_comfyui_requirements"
# The server's fast Aliyun mirror can lag one release behind for comfy-kitchen.
# Fetch only that small missing wheel from official PyPI, then keep the mirror
# for the larger frontend and runtime packages.
PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple \
  "$python_bin" -m pip install comfy-kitchen==0.2.27
"$python_bin" -m pip install -r requirements.txt

version="$($python_bin - <<'PY'
try:
    import comfyui_version
    print(comfyui_version.__version__)
except Exception:
    print("unknown")
PY
)"
echo "[H3_STAGE] comfyui_updated version=$version commit=$(git rev-parse --short HEAD)"
echo "Previous commit recorded in $backup_file"
