"""Small production-facing HTTP wrapper around Fun-CosyVoice 3."""

from __future__ import annotations

import io
import os
import sys
import tempfile
import threading
import time
from pathlib import Path

import soundfile as sf
import torch
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse, Response

SOURCE_DIR = Path(
    os.environ.get("COSYVOICE_SOURCE_DIR", "/root/cosyvoice-runtime/CosyVoice")
)
MODEL_DIR = Path(
    os.environ.get(
        "COSYVOICE_MODEL_DIR",
        "/root/cosyvoice-models/Fun-CosyVoice3-0.5B",
    )
)
sys.path.insert(0, str(SOURCE_DIR))
sys.path.insert(0, str(SOURCE_DIR / "third_party" / "Matcha-TTS"))

from cosyvoice.cli.cosyvoice import AutoModel  # noqa: E402

app = FastAPI(title="Manju CosyVoice Service", version="1.0")
MODEL = AutoModel(model_dir=str(MODEL_DIR), fp16=torch.cuda.is_available())
INFERENCE_LOCK = threading.Lock()
SYSTEM_PREFIX = "You are a helpful assistant."
PROMPT_END = "<|endofprompt|>"


@app.get("/health")
def health() -> JSONResponse:
    return JSONResponse(
        {
            "ok": True,
            "model": "Fun-CosyVoice3-0.5B-2512",
            "model_dir": str(MODEL_DIR),
            "sample_rate": MODEL.sample_rate,
            "cuda": torch.cuda.is_available(),
            "gpu": torch.cuda.get_device_name(0) if torch.cuda.is_available() else "",
        }
    )


@app.post("/synthesize")
def synthesize(
    tts_text: str = Form(...),
    prompt_text: str = Form(...),
    instruct_text: str = Form(""),
    voice_profile_id: str = Form(""),
    speed: float = Form(1.0),
    prompt_wav: UploadFile = File(...),  # noqa: B008
) -> Response:
    text = tts_text.strip()
    reference_text = prompt_text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="tts_text is required")
    if not reference_text:
        raise HTTPException(status_code=400, detail="prompt_text is required")
    if not 0.6 <= speed <= 1.6:
        raise HTTPException(status_code=400, detail="speed must be between 0.6 and 1.6")

    suffix = Path(prompt_wav.filename or "reference.wav").suffix or ".wav"
    started = time.monotonic()
    temp_path = ""
    try:
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as temp_file:
            temp_file.write(prompt_wav.file.read())
            temp_path = temp_file.name
        with INFERENCE_LOCK, torch.inference_mode():
            if instruct_text.strip():
                instruction = instruct_text.strip()
                if PROMPT_END not in instruction:
                    instruction = f"{SYSTEM_PREFIX} {instruction}{PROMPT_END}"
                chunks = MODEL.inference_instruct2(
                    text,
                    instruction,
                    temp_path,
                    stream=False,
                    speed=speed,
                )
            else:
                zero_shot_prompt = reference_text
                if PROMPT_END not in zero_shot_prompt:
                    zero_shot_prompt = (
                        f"{SYSTEM_PREFIX}{PROMPT_END}{zero_shot_prompt}"
                    )
                profile_id = "".join(
                    character
                    for character in voice_profile_id
                    if character.isalnum() or character in "_-"
                )[:80]
                if profile_id:
                    if profile_id not in MODEL.frontend.spk2info:
                        MODEL.add_zero_shot_spk(
                            zero_shot_prompt,
                            temp_path,
                            profile_id,
                        )
                    chunks = MODEL.inference_zero_shot(
                        text,
                        "",
                        "",
                        zero_shot_spk_id=profile_id,
                        stream=False,
                        speed=speed,
                    )
                else:
                    chunks = MODEL.inference_zero_shot(
                        text,
                        zero_shot_prompt,
                        temp_path,
                        stream=False,
                        speed=speed,
                    )
            tensors = [item["tts_speech"].detach().cpu() for item in chunks]
        if not tensors:
            raise RuntimeError("model returned no speech")
        waveform = torch.cat(tensors, dim=1).squeeze(0).numpy()
        buffer = io.BytesIO()
        sf.write(
            buffer,
            waveform,
            MODEL.sample_rate,
            format="WAV",
            subtype="PCM_16",
        )
        elapsed = time.monotonic() - started
        return Response(
            content=buffer.getvalue(),
            media_type="audio/wav",
            headers={
                "X-CosyVoice-Model": "Fun-CosyVoice3-0.5B-2512",
                "X-CosyVoice-Elapsed": f"{elapsed:.3f}",
            },
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    finally:
        if temp_path:
            Path(temp_path).unlink(missing_ok=True)
