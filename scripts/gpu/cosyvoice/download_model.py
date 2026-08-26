"""Download only the CosyVoice 3 files required for single-speaker inference."""

from __future__ import annotations

import os
from pathlib import Path

from huggingface_hub import snapshot_download

REPO_ID = "FunAudioLLM/Fun-CosyVoice3-0.5B-2512"
REQUIRED_PATTERNS = [
    "config.json",
    "configuration.json",
    "cosyvoice3.yaml",
    "llm.pt",
    "flow.pt",
    "hift.pt",
    "campplus.onnx",
    "speech_tokenizer_v3.onnx",
    "CosyVoice-BlankEN/*",
]
REQUIRED_FILES = [
    "cosyvoice3.yaml",
    "llm.pt",
    "flow.pt",
    "hift.pt",
    "campplus.onnx",
    "speech_tokenizer_v3.onnx",
    "CosyVoice-BlankEN/model.safetensors",
]


def main() -> None:
    destination = Path(
        os.environ.get(
            "COSYVOICE_MODEL_DIR",
            "/root/cosyvoice-models/Fun-CosyVoice3-0.5B",
        )
    )
    destination.mkdir(parents=True, exist_ok=True)
    print(f"[model] downloading {REPO_ID} -> {destination}", flush=True)
    snapshot_download(
        repo_id=REPO_ID,
        local_dir=destination,
        allow_patterns=REQUIRED_PATTERNS,
    )
    missing = [name for name in REQUIRED_FILES if not (destination / name).is_file()]
    if missing:
        raise RuntimeError(f"model download incomplete: {', '.join(missing)}")
    total = sum(path.stat().st_size for path in destination.rglob("*") if path.is_file())
    print(f"[model] ready, size={total / 1024**3:.2f} GiB", flush=True)


if __name__ == "__main__":
    main()
