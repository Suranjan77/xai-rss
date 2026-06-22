#!/usr/bin/env bash
# Set up the F5-TTS host service: create the venv, install F5-TTS against the
# system ROCm PyTorch, and DOWNLOAD the F5-TTS model so the first request is fast.
#
# Run on the HOST (the GPU machine):  ./scripts/setup_tts.sh
set -e
cd "$(dirname "${BASH_SOURCE[0]}")/.."

echo ">> ensuring Fedora ROCm PyTorch is installed (needs sudo)"
if ! /usr/bin/python3 -c "import torch" 2>/dev/null; then
  sudo dnf install -y python3-torch python3-torchaudio
fi

if [ ! -d .venv-tts ]; then
  echo ">> creating .venv-tts (reusing system GPU torch)"
  python3 -m venv --system-site-packages .venv-tts
fi

echo ">> installing F5-TTS"
.venv-tts/bin/pip install -q --upgrade pip
.venv-tts/bin/pip install -q f5-tts
.venv-tts/bin/pip uninstall -y -q torchcodec 2>/dev/null || true  # CUDA-only; we use soundfile

echo ">> downloading the F5-TTS model (one-time, ~1.3 GB)"
HSA_OVERRIDE_GFX_VERSION=11.0.0 PYTHONHASHSEED=0 .venv-tts/bin/python - <<'PY'
import soundfile as sf, torch, torchaudio
torchaudio.load = lambda p, *a, **k: (
    torch.from_numpy(sf.read(str(p), dtype="float32", always_2d=True)[0].T).contiguous(),
    sf.read(str(p), dtype="float32", always_2d=True)[1])
from f5_tts.api import F5TTS
F5TTS(device="cuda" if torch.cuda.is_available() else "cpu")  # triggers model download
print("model downloaded and loadable on", "cuda" if torch.cuda.is_available() else "cpu")
PY

echo
echo ">> done. Start the TTS service with:"
echo "     ./scripts/run_tts_server.sh"
echo "   or install the systemd unit (systemd/idigest-tts.service)."
