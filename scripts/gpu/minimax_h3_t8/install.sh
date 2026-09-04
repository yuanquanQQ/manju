#!/usr/bin/env bash
# Install the comfyui-minimax-h3-audio-T8 custom node package (offline bundle).
#
# The T8 package is a pure-Python ComfyUI extension: requirements.txt is empty
# by design, so this script only stages the archive under custom_nodes/ and
# verifies the five core H3 nodes the app relies on are present. ComfyUI is
# expected to be restarted by the caller after this returns.
#
# Required environment variables:
#   COMFY_ROOT   - ComfyUI installation root (default /root/autodl-tmp/ComfyUI)
#   T8_ARCHIVE   - path to comfyui-minimax-h3-audio-T8-main.tar.gz on this host
set -euo pipefail

comfy_root="${COMFY_ROOT:-/root/autodl-tmp/ComfyUI}"
archive="${T8_ARCHIVE:?T8_ARCHIVE must point at the T8 tar.gz bundle}"
pkg_name="comfyui-minimax-h3-audio-T8"
target="$comfy_root/custom_nodes/$pkg_name"

if [ ! -f "$archive" ]; then
  echo "T8 archive not found: $archive" >&2
  exit 1
fi
if [ ! -d "$comfy_root" ]; then
  echo "ComfyUI root not found: $comfy_root" >&2
  exit 1
fi

mkdir -p "$comfy_root/custom_nodes"
# Replace a previous install atomically so a failed extraction cannot leave a
# half-staged package that ComfyUI would try to import on the next boot.
rm -rf "$target.tmp"
mkdir -p "$target.tmp"
tar -xzf "$archive" -C "$target.tmp"
mv "$target.tmp/$pkg_name" "$target"
rm -rf "$target.tmp"

# The app only depends on the audio/video core nodes; verify they all landed.
for node in \
  MiniMaxH3AudioConditioningT8 \
  MiniMaxH3DualClockSamplerT8 \
  MiniMaxH3AVDecodeT8 \
  MiniMaxH3AudioMixT8 \
  MiniMaxH3OutputTrimT8
do
  if ! grep -rq "$node" "$target" --include='*.py'; then
    echo "T8 node not found in bundle: $node" >&2
    exit 2
  fi
done

echo "[T8_STAGE] t8_ready pkg=$pkg_name archive=$(basename "$archive")"
