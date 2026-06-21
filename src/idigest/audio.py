"""Audio narration: synthesize a spoken script to MP3 via F5-TTS on the GPU.

The TTS stack (PyTorch-ROCm + F5-TTS) lives in a separate venv (.venv-tts) on the
system Python so it can use the GPU; the main app (Python 3.14) shells out to
scripts/tts_synth.py there, then encodes the WAV to a compact MP3 with ffmpeg.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path

from .config import load_config

_ROOT = Path(__file__).resolve().parents[2]
_TTS_PYTHON = _ROOT / ".venv-tts" / "bin" / "python"
_TTS_SCRIPT = _ROOT / "scripts" / "tts_synth.py"
_AUDIO_DIR = _ROOT / "data" / "audio"


def available() -> bool:
    return _TTS_PYTHON.exists() and _TTS_SCRIPT.exists()


def synthesize(text: str, out_mp3: Path) -> bool:
    """Render narration text to an MP3. Returns True on success."""
    cfg = load_config()["audio"]
    if not available() or not text.strip():
        return False
    _AUDIO_DIR.mkdir(parents=True, exist_ok=True)

    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as tf:
        tf.write(text)
        txt_path = tf.name
    wav_path = out_mp3.with_suffix(".wav")

    # Env for the GPU TTS subprocess on gfx1151 (Strix Halo):
    #  - HSA_OVERRIDE_GFX_VERSION=11.0.0 runs F5-TTS's MIOpen/rocBLAS ops as
    #    gfx1100 (RDNA3), which has tuned kernels — gfx1151 native errors out.
    #    Affects only this subprocess; llama.cpp keeps running native gfx1151.
    #  - PYTHONHASHSEED=0 keeps spawned helper processes valid under Python 3.14.
    env = {
        **os.environ,
        "PYTHONHASHSEED": "0",
        "HSA_OVERRIDE_GFX_VERSION": "11.0.0",
    }

    try:
        subprocess.run(
            [
                str(_TTS_PYTHON), str(_TTS_SCRIPT),
                "--text-file", txt_path,
                "--out-wav", str(wav_path),
                "--ref", cfg["ref_file"],
                "--ref-text", cfg["ref_text"],
                "--speed", str(cfg.get("speed", 1.0)),
                "--nfe", str(cfg.get("nfe_step", 32)),
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=cfg.get("timeout_s", 1200),
            env=env,
        )
        # encode to a small mono MP3 for email
        subprocess.run(
            ["ffmpeg", "-y", "-i", str(wav_path), "-ac", "1",
             "-b:a", cfg.get("mp3_bitrate", "64k"), str(out_mp3)],
            check=True, capture_output=True, text=True,
        )
        return out_mp3.exists()
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return False
    finally:
        Path(txt_path).unlink(missing_ok=True)
        wav_path.unlink(missing_ok=True)


def audio_path_for(paper_id: int) -> Path:
    return _AUDIO_DIR / f"{paper_id}.mp3"
