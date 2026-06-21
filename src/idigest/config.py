"""Configuration loading.

config.toml holds defaults; config.local.toml (git-ignored) overrides it and is
where secrets like the SMTP app password live. Access via ``load_config()``.
"""

from __future__ import annotations

import tomllib
from functools import lru_cache
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[2]
_BASE = _ROOT / "config.toml"
_LOCAL = _ROOT / "config.local.toml"


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
    with _BASE.open("rb") as f:
        cfg = tomllib.load(f)
    if _LOCAL.exists():
        with _LOCAL.open("rb") as f:
            cfg = _deep_merge(cfg, tomllib.load(f))
    return cfg
