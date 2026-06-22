#!/usr/bin/env bash
# Run the F5-TTS host service with the gfx1151 (Strix Halo) environment:
#  - HSA_OVERRIDE_GFX_VERSION=11.0.0 : use gfx1100 kernels (rocBLAS/MIOpen tuned)
#  - PYTHONHASHSEED=0                : keep spawned helpers valid under Python 3.14
# Binds 0.0.0.0:8090 so a containerized app (network_mode host) can reach it too.
set -e
cd "$(dirname "${BASH_SOURCE[0]}")/.."
exec env HSA_OVERRIDE_GFX_VERSION=11.0.0 PYTHONHASHSEED=0 \
  .venv-tts/bin/python scripts/tts_server.py --host 0.0.0.0 --port "${1:-8090}" --preload
