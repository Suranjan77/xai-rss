#!/usr/bin/env python3
"""F5-TTS host service — keeps the model resident on the GPU and renders narration
to MP3 on request. Run inside .venv-tts (system ROCm torch). The idigest app POSTs
to /synthesize; this works for both host and containerized app.

Launch (env matters for gfx1151 — see scripts/run_tts_server.sh):
  HSA_OVERRIDE_GFX_VERSION=11.0.0 PYTHONHASHSEED=0 \
    .venv-tts/bin/python scripts/tts_server.py --host 0.0.0.0 --port 8090
"""

from __future__ import annotations

import argparse
import io
import subprocess
import tempfile
from pathlib import Path

import numpy as np
import soundfile as sf
import torch
import torchaudio
import uvicorn
from fastapi import FastAPI
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel

# torchaudio 2.9 routes .load through torchcodec (CUDA-only); shim onto soundfile.
def _sf_load(path, *a, **k):
    data, sr = sf.read(str(path), dtype="float32", always_2d=True)
    return torch.from_numpy(data.T).contiguous(), sr


torchaudio.load = _sf_load

from f5_tts.api import F5TTS  # noqa: E402
from f5_tts.infer.utils_infer import chunk_text  # noqa: E402

_REF = str(Path(__file__).resolve().parents[1] / ".venv-tts" / "lib" / "python3.14"
           / "site-packages" / "f5_tts" / "infer" / "examples" / "basic" / "basic_ref_en.wav")
_REF_TEXT = "Some call me nature, others call me mother nature."

app = FastAPI(title="idigest-tts")
_model: F5TTS | None = None


def _get_model() -> F5TTS:
    global _model
    if _model is None:
        device = "cuda" if torch.cuda.is_available() else "cpu"
        _model = F5TTS(device=device)
    return _model


def _crossfade(pieces, sr, seconds=0.08):
    pieces = [p for p in pieces if len(p)]
    if not pieces:
        return np.zeros(1, dtype="float32")
    n = max(1, int(seconds * sr))
    out = pieces[0].astype("float32")
    for p in pieces[1:]:
        p = p.astype("float32")
        if len(out) >= n and len(p) >= n:
            fade = np.linspace(1.0, 0.0, n, dtype="float32")
            out[-n:] = out[-n:] * fade + p[:n] * (1.0 - fade)
            out = np.concatenate([out, p[n:]])
        else:
            out = np.concatenate([out, p])
    return out


def _wav_to_mp3(wav: np.ndarray, sr: int, bitrate: str = "64k") -> bytes:
    with tempfile.TemporaryDirectory() as d:
        wav_path, mp3_path = f"{d}/a.wav", f"{d}/a.mp3"
        sf.write(wav_path, wav, sr)
        subprocess.run(["ffmpeg", "-y", "-i", wav_path, "-ac", "1", "-b:a", bitrate,
                        mp3_path], check=True, capture_output=True)
        return Path(mp3_path).read_bytes()


class SynthReq(BaseModel):
    text: str
    speed: float = 1.18
    nfe: int = 16
    bitrate: str = "64k"


@app.get("/health")
def health():
    return JSONResponse({"status": "ok", "cuda": torch.cuda.is_available()})


@app.post("/synthesize")
def synthesize(req: SynthReq):
    text = (req.text or "").strip()
    if not text:
        return JSONResponse({"error": "empty text"}, status_code=400)
    f5 = _get_model()
    # one small single-batch chunk at a time (the multi-batch path is unreliable
    # under Python 3.14), crossfaded for smooth flow.
    chunks = chunk_text(text, max_chars=140) or [text]
    pieces, sr = [], f5.target_sample_rate
    for ch in chunks:
        if not ch.strip():
            continue
        wav, sr, _ = f5.infer(ref_file=_REF, ref_text=_REF_TEXT, gen_text=ch,
                              nfe_step=req.nfe, speed=req.speed, remove_silence=True)
        pieces.append(np.asarray(wav, dtype=np.float32))
    final = _crossfade(pieces, sr)
    mp3 = _wav_to_mp3(final, sr, req.bitrate)
    return Response(content=mp3, media_type="audio/mpeg")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8090)
    ap.add_argument("--preload", action="store_true", help="load the model at startup")
    args = ap.parse_args()
    if args.preload:
        _get_model()
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
