#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -lt 3 ]; then
  echo "usage: run.sh INPUT_VIDEO INPUT_AUDIO OUTPUT_VIDEO [STEPS] [GUIDANCE] [FACE_REFERENCE] [MIN_SIMILARITY]" >&2
  exit 2
fi

export HF_ENDPOINT="${HF_ENDPOINT:-https://hf-mirror.com}"
export HF_HOME="${HF_HOME:-/root/autodl-tmp/huggingface}"
export PYTORCH_CUDA_ALLOC_CONF="${PYTORCH_CUDA_ALLOC_CONF:-expandable_segments:True}"

source_dir=/root/autodl-tmp/LatentSync
python_bin=/root/autodl-tmp/latentsync-env/bin/python
input_video=$1
input_audio=$2
output_video=$3
steps=${4:-20}
guidance=${5:-1.5}
face_reference=${6:-}
minimum_face_similarity=${7:-0.18}
patched_detector="$(dirname "$0")/face_detector.py"

test -x "$python_bin"
test -f "$source_dir/checkpoints/latentsync_unet.pt"
test -f "$source_dir/checkpoints/whisper/tiny.pt"
test -f "$input_video"
test -f "$input_audio"
test -f "$patched_detector"
mkdir -p "$(dirname "$output_video")"

# Install the project's drop-in selector. It behaves exactly like upstream for
# single-person jobs and activates cast-reference matching only when requested.
if ! cmp -s "$patched_detector" "$source_dir/latentsync/utils/face_detector.py"; then
  if [ ! -f "$source_dir/latentsync/utils/face_detector.py.upstream" ]; then
    cp "$source_dir/latentsync/utils/face_detector.py" \
      "$source_dir/latentsync/utils/face_detector.py.upstream"
  fi
  cp "$patched_detector" "$source_dir/latentsync/utils/face_detector.py"
fi
if [ -n "$face_reference" ]; then
  test -f "$face_reference"
  export LATENTSYNC_FACE_REFERENCE="$face_reference"
  export LATENTSYNC_FACE_MIN_SIMILARITY="$minimum_face_similarity"
else
  unset LATENTSYNC_FACE_REFERENCE || true
  unset LATENTSYNC_FACE_MIN_SIMILARITY || true
fi

# The official CLI follows audio duration. Preserve editorial timing by padding
# short dialogue with silence; never silently truncate dialogue that is longer
# than its source shot.
video_duration=$(ffprobe -v error -show_entries format=duration \
  -of default=noprint_wrappers=1:nokey=1 "$input_video")
audio_duration=$(ffprobe -v error -show_entries format=duration \
  -of default=noprint_wrappers=1:nokey=1 "$input_audio")
if awk "BEGIN {exit !($audio_duration > $video_duration + 0.10)}"; then
  echo "Audio duration (${audio_duration}s) exceeds video duration (${video_duration}s)" >&2
  echo "Regenerate a longer source video or shorten the dialogue before lip sync." >&2
  exit 4
fi
inference_audio=$input_audio
normalized_audio="$(dirname "$output_video")/audio_matched_to_video.wav"
if awk "BEGIN {exit !($audio_duration < $video_duration - 0.05)}"; then
  ffmpeg -y -v error -i "$input_audio" -af apad -t "$video_duration" \
    -ar 16000 -ac 1 "$normalized_audio"
  inference_audio=$normalized_audio
  trap 'rm -f "$normalized_audio"' EXIT
  echo "AUDIO_PADDED_TO_VIDEO_DURATION=$video_duration"
fi

# LatentSync 1.6 needs about 18 GB. Stop competing GPU services first.
# ComfyUI is launched after `cd`, so /proc/<pid>/cmdline contains only
# `python main.py ... --port 8188`, not the absolute main.py path.
comfy_pids=$(pgrep -f 'main\.py .*--port 8188' 2>/dev/null || true)
if [ -n "$comfy_pids" ]; then
  kill $comfy_pids 2>/dev/null || true
  for _ in $(seq 1 20); do
    remaining=""
    for pid in $comfy_pids; do
      if kill -0 "$pid" 2>/dev/null; then
        remaining="$remaining $pid"
      fi
    done
    [ -z "$remaining" ] && break
    sleep 1
  done
  [ -z "${remaining:-}" ] || kill -9 $remaining 2>/dev/null || true
fi
if [ -f /root/cosyvoice-service/cosyvoice.pid ]; then
  cosy_pid=$(cat /root/cosyvoice-service/cosyvoice.pid 2>/dev/null || true)
  if [ -n "$cosy_pid" ]; then
    kill "$cosy_pid" 2>/dev/null || true
  fi
fi
sleep 2

# Do not start an expensive inference when another process still owns most of
# the card. A clear error here is much easier to diagnose than a VAE-load OOM.
gpu_used=$(nvidia-smi --query-compute-apps=used_gpu_memory \
  --format=csv,noheader,nounits 2>/dev/null \
  | awk '{sum += $1} END {print sum + 0}')
if [ "$gpu_used" -gt 4096 ]; then
  echo "GPU memory is still occupied: ${gpu_used} MiB" >&2
  nvidia-smi --query-compute-apps=pid,process_name,used_gpu_memory \
    --format=csv,noheader 2>/dev/null >&2 || true
  exit 3
fi

cd "$source_dir"
"$python_bin" -m scripts.inference \
  --unet_config_path configs/unet/stage2_512.yaml \
  --inference_ckpt_path checkpoints/latentsync_unet.pt \
  --inference_steps "$steps" \
  --guidance_scale "$guidance" \
  --enable_deepcache \
  --video_path "$input_video" \
  --audio_path "$inference_audio" \
  --video_out_path "$output_video"

test -s "$output_video"
echo "LATENTSYNC_OUTPUT=$output_video"
