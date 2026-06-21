"""Client for the local llama-server (OpenAI-compatible chat completions).

Generation only — embeddings live in embeddings.py on the CPU. We assume a
single llama-server is already running (scripts/serve_llm.sh / systemd unit) for
the active model; only one model is ever loaded at a time.
"""

from __future__ import annotations

import json
import re
from typing import Any

import httpx

from .config import load_config


def is_up() -> bool:
    cfg = load_config()["llm"]
    try:
        r = httpx.get(f"{cfg['base_url']}/health", timeout=2.0)
        return r.status_code == 200
    except httpx.HTTPError:
        return False


def chat(
    messages: list[dict[str, str]],
    *,
    temperature: float = 0.3,
    max_tokens: int = 1024,
    json_mode: bool = False,
) -> str:
    """Run a chat completion against the running server, return the text."""
    cfg = load_config()["llm"]
    payload: dict[str, Any] = {
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
    try:
        r = httpx.post(
            f"{cfg['base_url']}/v1/chat/completions",
            json=payload,
            timeout=cfg["request_timeout_s"],
        )
        r.raise_for_status()
    except httpx.ConnectError as e:
        raise RuntimeError(
            f"cannot reach llama-server at {cfg['base_url']}. Start it with "
            f"scripts/serve_llm.sh (or the systemd unit)."
        ) from e
    return r.json()["choices"][0]["message"]["content"]


def chat_json(
    messages: list[dict[str, str]], *, temperature: float = 0.2, max_tokens: int = 1024
) -> Any:
    """Chat expecting JSON back. Tolerant of fences/prose; retries once stricter."""
    raw = chat(messages, temperature=temperature, max_tokens=max_tokens, json_mode=True)
    try:
        return _parse_json(raw)
    except ValueError:
        # one stricter retry — common when thinking ate the budget or fences leaked
        retry = messages + [
            {"role": "assistant", "content": raw[:500]},
            {"role": "user", "content": "Return ONLY the JSON object, nothing else."},
        ]
        raw2 = chat(retry, temperature=0.0, max_tokens=max_tokens, json_mode=True)
        return _parse_json(raw2)


def _parse_json(raw: str) -> Any:
    raw = raw.strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass
    # strip ```json fences
    m = re.search(r"```(?:json)?\s*(.*?)\s*```", raw, re.DOTALL)
    if m:
        return json.loads(m.group(1))
    # last resort: first {...} or [...] span
    m = re.search(r"(\{.*\}|\[.*\])", raw, re.DOTALL)
    if m:
        return json.loads(m.group(1))
    raise ValueError(f"could not parse JSON from model output: {raw[:200]!r}")
