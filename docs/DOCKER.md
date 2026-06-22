# Docker

idigest is a local-first app whose heavy pieces are **GPU-bound** (the Gemma LLM via
llama.cpp, and F5-TTS audio via ROCm PyTorch). Those run best **on the host**. The
container packages the **CPU application** — the web UI, ingestion, email, embeddings,
and the SQLite store — and talks to the host's LLM over the network.

```
┌─ host ─────────────────────────────────────────────┐
│  llama-server (Gemma, GPU) :8080                    │
│  F5-TTS (ROCm, GPU)         ← audio (host-only)     │
│                                                     │
│  ┌─ container: idigest (CPU) ──────────────────┐    │
│  │  web UI :8081, ingest, email, embeddings    │    │
│  │  → reaches llama-server at 127.0.0.1:8080    │    │
│  └─────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────┘
```

`network_mode: host` lets the container reach the host LLM at `127.0.0.1:8080` and serve
the UI on `127.0.0.1:8081` directly (matching the non-container setup, including
Tailscale `serve`).

## Quick start

Works with Docker or Podman (`podman compose` / `podman-compose`).

```bash
# 1. host: have llama-server running (GPU) — scripts/serve_llm.sh or the systemd unit
# 2. create config.local.toml from the example (SMTP app password, UI auth, ui_base_url)
cp config.local.toml.example config.local.toml   # then edit

# 3. build + run the app
docker compose up -d --build        # or: podman compose up -d --build
# UI on http://127.0.0.1:8081  (first run seeds the DB automatically)
```

## Running the scheduled jobs

The container's default command is the web server. Run jobs on demand:

```bash
docker compose run --rm jobs idigest ingest    # ingest + pre-generate next email
docker compose run --rm jobs idigest email     # send today's email
docker compose run --rm jobs idigest digest    # weekly digest
docker compose run --rm jobs idigest status
```

To schedule them, point host systemd timers (or cron) at these commands — e.g. a 05:00
`ingest` and a 07:30 `email`, mirroring `systemd/`. (Audio will be skipped in the
container; see below.)

## Audio (optional, host-only)

Audio narration needs the GPU + Fedora's ROCm PyTorch in the dedicated `.venv-tts`,
which is host-specific. Inside the container `audio.available()` is false, so the app
runs fine **without** audio (email/UI just omit the listen link). To keep audio,
generate it on the host: run the `email`/`ingest` jobs on the host (systemd) rather than
in the container, and let the container serve everything else. Mixing is fine — both use
the same `data/` and DB.

## Volumes
- `./config.local.toml` → secrets/overrides (read-only).
- `./data/db`, `./data/figures` → persisted on the host.
- `idigest-cache` → the CPU embedding model cache (avoids re-download on restart).

## Notes
- The image is `python:3.12-slim` (the app's deps have clean 3.12 wheels; the host uses
  3.14, but the app is compatible with 3.11+).
- Env overrides: `IDIGEST_LLM_BASE_URL` (host LLM), `IDIGEST_DB` (DB path).
- To fully containerize the LLM too, run a ROCm llama.cpp image as a second service with
  `--device /dev/kfd --device /dev/dri` and the model mounted, then point
  `IDIGEST_LLM_BASE_URL` at it. That's GPU/driver-specific and left as an exercise.
```
