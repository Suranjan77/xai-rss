"""Audio narration client.

Synthesis runs in a separate **host TTS service** (scripts/tts_server.py) that
keeps F5-TTS resident on the GPU. The app just POSTs the narration text to
``[audio] tts_url`` (override with IDIGEST_TTS_URL) and receives an MP3 — so this
works the same whether the app runs on the host or in a container.
"""

from __future__ import annotations

from pathlib import Path

import httpx

from .config import load_config

_ROOT = Path(__file__).resolve().parents[2]
_AUDIO_DIR = _ROOT / "data" / "audio"


def _tts_url() -> str:
    return (load_config()["audio"].get("tts_url") or "").rstrip("/")


def available() -> bool:
    """True when audio is enabled and the TTS service is reachable."""
    cfg = load_config()["audio"]
    url = _tts_url()
    if not cfg.get("enabled", True) or not url:
        return False
    try:
        return httpx.get(f"{url}/health", timeout=3.0).status_code == 200
    except httpx.HTTPError:
        return False


def synthesize(text: str, out_mp3: Path) -> bool:
    """Ask the TTS service to render narration text to an MP3. Returns True on success."""
    cfg = load_config()["audio"]
    url = _tts_url()
    if not url or not text.strip():
        return False
    _AUDIO_DIR.mkdir(parents=True, exist_ok=True)
    try:
        r = httpx.post(
            f"{url}/synthesize",
            json={"text": text, "speed": cfg.get("speed", 1.0),
                  "nfe": cfg.get("nfe_step", 16)},
            timeout=cfg.get("timeout_s", 1800),
        )
        r.raise_for_status()
        out_mp3.write_bytes(r.content)
        return out_mp3.exists() and out_mp3.stat().st_size > 0
    except httpx.HTTPError:
        return False


def audio_path_for(paper_id: int) -> Path:
    return _AUDIO_DIR / f"{paper_id}.mp3"
