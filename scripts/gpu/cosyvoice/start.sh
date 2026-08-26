#!/usr/bin/env bash
set -euo pipefail

export HF_ENDPOINT="${HF_ENDPOINT:-https://hf-mirror.com}"
export COSYVOICE_SOURCE_DIR="${COSYVOICE_SOURCE_DIR:-/root/cosyvoice-runtime/CosyVoice}"
export COSYVOICE_MODEL_DIR="${COSYVOICE_MODEL_DIR:-/root/cosyvoice-models/Fun-CosyVoice3-0.5B}"
export PYTHONPATH="$COSYVOICE_SOURCE_DIR:$COSYVOICE_SOURCE_DIR/third_party/Matcha-TTS"

service_dir=/root/cosyvoice-service
log_file="$service_dir/cosyvoice.log"
pid_file="$service_dir/cosyvoice.pid"
python_bin=/root/cosyvoice-env/bin/python
mkdir -p "$service_dir"

if curl -fsS --max-time 3 http://127.0.0.1:50000/health >/dev/null 2>&1; then
  echo "CosyVoice is already online"
  exit 0
fi

if [ -f "$pid_file" ]; then
  old_pid=$(cat "$pid_file" || true)
  if [ -n "$old_pid" ] && kill -0 "$old_pid" 2>/dev/null; then
    kill "$old_pid"
    for _ in $(seq 1 20); do
      kill -0 "$old_pid" 2>/dev/null || break
      sleep 0.5
    done
  fi
fi

cd "$service_dir"
nohup "$python_bin" -m uvicorn server:app \
  --host 127.0.0.1 --port 50000 --workers 1 \
  >"$log_file" 2>&1 </dev/null &
echo $! >"$pid_file"
echo "CosyVoice starting as PID $(cat "$pid_file"); log: $log_file"

