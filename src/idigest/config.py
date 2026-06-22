"""Configuration loading.

config.toml holds defaults; config.local.toml (git-ignored) overrides it and is
where secrets like the SMTP app password live. Access via ``load_config()``.
"""

from __future__ import annotations

import os
import tomllib
from functools import lru_cache
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[2]


def _find(name: str) -> Path:
    """Locate a config file: $IDIGEST_CONFIG_DIR, then CWD, then the repo root
    (editable install). This keeps it working both on the host and in a container
    where the package lives in site-packages."""
    candidates = []
    if d := os.environ.get("IDIGEST_CONFIG_DIR"):
        candidates.append(Path(d) / name)
    candidates += [Path.cwd() / name, _ROOT / name]
    for c in candidates:
        if c.exists():
            return c
    return candidates[-1]


def _deep_merge(base: dict[str, Any], over: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


@lru_cache(maxsize=1)
def load_config() -> dict[str, Any]:
    with _find("config.toml").open("rb") as f:
        cfg = tomllib.load(f)
    local = _find("config.local.toml")
    if local.exists():
        with local.open("rb") as f:
            cfg = _deep_merge(cfg, tomllib.load(f))
    # Env overrides (handy in containers): point the app at a host LLM, etc.
    if base := os.environ.get("IDIGEST_LLM_BASE_URL"):
        cfg["llm"]["base_url"] = base
    if db := os.environ.get("IDIGEST_DB"):
        cfg["paths"]["db"] = db
    if seed := os.environ.get("IDIGEST_SEED_DIR"):
        cfg["paths"]["seed_dir"] = seed
    if tts := os.environ.get("IDIGEST_TTS_URL"):
        cfg["audio"]["tts_url"] = tts
    return cfg
