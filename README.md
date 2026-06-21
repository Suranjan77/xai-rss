# idigest — local interpretability-research daily digest

A local-first tool that keeps a curated, growing knowledge base of explainable-AI /
interpretability research and emails you **one byte-sized paper per day**, ordered so
earlier papers teach the prerequisites for later ones. Full depth lives in a local web
UI linked from each email. New papers are pulled in automatically; you can also add
papers by web search or PDF import. All generation runs on a **local LLM** (Gemma-4-12B
via `llama-server`); embeddings run on CPU so they never compete with the one GPU model.

## What's distinctive
- **Prerequisite ordering** — papers form a concept graph (`provides`/`requires`); the
  email order is a topological walk, and new papers auto-slot after their prerequisites.
- **One model at a time** — the only GPU model is Gemma-4-12B (with MTP speculative
  decoding); embeddings use a small CPU ONNX model (`fastembed`).
- **Figures in the email** — the key figure is extracted from the paper PDF (PyMuPDF
  embedded images + OpenCV region detection) and embedded inline with a caption.

## Setup

```bash
cd /home/sur/repo/local-inference
python3 -m venv .venv && . .venv/bin/activate
pip install -e .
idigest init-db
idigest load-seed                 # ~20 curated, ordered foundational papers

cp config.local.toml.example config.local.toml   # add your Gmail App Password
```

Start the local LLM (loads Gemma-4-12B + MTP draft):

```bash
./scripts/serve_llm.sh            # leave running (or use the systemd unit below)
```

## Daily use

```bash
idigest status                    # paused? path length, sent count, next paper
idigest email --dry-run           # generate + print today's email (no send)
idigest email                     # generate + send via Gmail SMTP, log it
idigest email-pause / email-resume

idigest add-search "concept bottleneck models" --limit 3   # search the web + import
idigest add-pdf https://arxiv.org/pdf/1702.08608           # import a PDF (URL or path)
idigest add-pdf ./some-paper.pdf

idigest ingest                    # fetch + filter + add recent interpretability papers
idigest serve-web                 # browse at http://127.0.0.1:8081
idigest path                      # print the ordered learning path
```

## Automate (systemd user units)

```bash
mkdir -p ~/.config/systemd/user
cp systemd/*.service systemd/*.timer ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now idigest-llm.service idigest-web.service
systemctl --user enable --now idigest-ingest.timer idigest-email.timer
systemctl --user list-timers | grep idigest
```

Email goes out at 07:00, ingestion runs at 06:30 (edit the `.timer` files to taste).

## Audio narration (F5-TTS on GPU)

Each daily paper gets a **single-narrator spoken explainer** (audio-native script
written by Gemma — no markdown/LaTeX, math spoken in words), synthesized by
**F5-TTS on the GPU** and **attached as an MP3** to the email (also playable in the
UI). Episode length adapts to the paper's difficulty.

The TTS stack runs in a **separate venv** (`.venv-tts`) on the system Python so it
can use Fedora's ROCm PyTorch (the GPU build), kept apart from the main app:

```bash
sudo dnf install -y python3-torch python3-torchaudio   # ROCm GPU torch for gfx1151
python3 -m venv --system-site-packages .venv-tts
.venv-tts/bin/pip install f5-tts
.venv-tts/bin/pip uninstall -y torchcodec               # CUDA build; we use soundfile instead
```

Config lives under `[audio]` in `config.toml` (voice reference, `nfe_step` quality/speed,
bitrate). Synthesis runs on `cuda` automatically; first run downloads the ~1.3 GB model.

## Remote access (Tailscale)

Reach the UI from your phone/laptop anywhere, privately — the app stays on
`127.0.0.1` and Tailscale proxies it over HTTPS to your own devices only.

```bash
! ./scripts/setup_tailscale.sh        # installs tailscale, logs in, runs `tailscale serve`
```

Then, in `config.local.toml`, point email links at your tailnet URL and turn on
the UI login (defense-in-depth):

```toml
[email]
ui_base_url = "https://<your-host>.<tailnet>.ts.net"

[web]
auth_user = "sur"
auth_password = "a-long-passphrase"
```

Install the Tailscale app on your phone/laptop, sign into the same account, and
open that URL. To stop sharing: `sudo tailscale serve --bg 8081 off`.

## Configuration
- `config.toml` — model paths, ports, scope keywords, schedule, MTP settings, full_depth email.
- `config.local.toml` — secrets (SMTP app password, UI auth); overrides `config.toml`.
- The daily email now carries the **full depth inline** (`email.full_depth = true`).

## Tests

```bash
pytest -q        # offline tests (no network/LLM): store, pathing, dedup, JSON parsing
```
