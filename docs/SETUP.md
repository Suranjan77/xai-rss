# Setup

Tested on Fedora 44, AMD Ryzen AI MAX+ 395 (Strix Halo, gfx1151), 62 GB unified RAM,
ROCm 7.1, Python 3.14.

## 1. Main application (Python 3.14)

```bash
cd /home/sur/repo/local-inference
python3 -m venv .venv && . .venv/bin/activate
pip install -e .
idigest init-db
idigest load-seed                 # ~20 curated, ordered foundational papers
```

`init-db` is idempotent and also migrates older databases (adds new columns).

## 2. Local LLM (Gemma-4-12B + MTP)

llama.cpp is prebuilt at `/home/sur/repo/llama.cpp/build/bin/`. The launch script reads
the model + MTP settings from `config.toml`:

```bash
./scripts/serve_llm.sh            # leave running, or use the systemd unit
```

Multi-token prediction is enabled via `--spec-type draft-mtp --model-draft
mtp-gemma-4-12b-it.gguf` (a small draft head, not a second full model). It falls back to
`ngram-cache` if the MTP file is missing.

## 3. Embeddings

CPU ONNX via `fastembed` (`bge-small-en-v1.5`, 384-dim) — installed with the main
package, no extra steps. Never touches the GPU.

## 4. Secrets / config

```bash
cp config.local.toml.example config.local.toml
```
Set in `config.local.toml` (git-ignored):
- `[email] smtp_user` / `smtp_password` — a **Gmail App Password** (not your password).
- `[email] ui_base_url` — your tailnet URL, so email links work remotely.
- `[web] auth_user` / `auth_password` — UI login (required before exposing externally).

`config.toml` holds non-secret defaults (model paths, ports, scope keywords, schedule,
MTP, full-depth email, audio settings).

## 5. Audio (F5-TTS on the GPU) — optional but recommended

The TTS stack needs PyTorch-ROCm, which only exists for the **system** Python 3.14 via
Fedora's packages. It lives in a **separate venv** so it never disturbs the main app:

```bash
sudo dnf install -y python3-torch python3-torchaudio   # ROCm/HIP build for gfx1151
python3 -m venv --system-site-packages .venv-tts
.venv-tts/bin/pip install f5-tts
.venv-tts/bin/pip uninstall -y torchcodec               # CUDA-only; we use soundfile
```

Verify the GPU is visible:
```bash
.venv-tts/bin/python -c "import torch; print(torch.cuda.is_available(), torch.cuda.get_device_name(0))"
# -> True AMD Radeon 8060S Graphics
```

First synthesis downloads the ~1.3 GB F5-TTS model. See [AUDIO](AUDIO.md) for the
gfx1151-specific env (`HSA_OVERRIDE_GFX_VERSION`, `PYTHONHASHSEED`) — these are applied
automatically by `audio.py`.

## 6. Run it

```bash
idigest serve-web                 # UI at http://127.0.0.1:8081
idigest email --dry-run           # generate + print today's email (no send)
idigest email                     # generate + send for real
```

## 7. Automate (systemd user units)

```bash
mkdir -p ~/.config/systemd/user
cp systemd/*.service systemd/*.timer ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now idigest-llm.service idigest-web.service
systemctl --user enable --now idigest-ingest.timer idigest-email.timer
loginctl enable-linger "$USER"    # run without an active login session
```

## 8. Remote access (Tailscale)

```bash
./scripts/setup_tailscale.sh      # installs tailscale, logs in, runs `tailscale serve`
```
This proxies `127.0.0.1:8081` over your private tailnet with HTTPS (no public exposure).
Put the tailnet URL in `config.local.toml` as `email.ui_base_url` and enable
`web.auth_user`/`auth_password`.
