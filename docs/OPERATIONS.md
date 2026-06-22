# Operations

## CLI reference

| Command | What it does |
|---|---|
| `idigest init-db` | Create/migrate the SQLite schema |
| `idigest load-seed` | Load the curated, ordered seed corpus |
| `idigest status` | Paused? path length, sent count, next paper |
| `idigest path` | Print the ordered learning path |
| `idigest email [--dry-run]` | Generate + send today's email (`--dry-run` prints) |
| `idigest email-pause` / `email-resume` | Toggle daily sending |
| `idigest ingest` | Fetch + filter + add recent interpretability papers |
| `idigest add-search "<query>" [--limit N]` | Web-search papers and import |
| `idigest add-pdf <url-or-path>` | Import a paper from a PDF |
| `idigest add-citations <id> [--direction references\|citations]` | Import a paper's references/citations |
| `idigest digest [--dry-run]` | Send the weekly digest email |
| `idigest logs [-f] [-n N]` | Consolidated logs of all idigest services |
| `idigest serve-web` | Run the local UI |

## Daily routine (automated)

- **06:30** — `idigest-ingest.timer` runs ingestion.
- **07:00** — `idigest-email.timer` sends the next paper (generating explanation, figure,
  and audio first). The email lands a few minutes later if audio is being synthesized.

Long-running services: `idigest-llm` (Gemma), `idigest-tts` (F5-TTS, model resident),
`idigest-web` (UI). Timer jobs: `idigest-ingest` (05:00), `idigest-email` (07:30),
`idigest-digest` (Sun 08:00).

Manual control:
```bash
idigest logs -f                               # all services in one stream + what's running
systemctl --user start idigest-email          # send now
systemctl --user list-timers | grep idigest   # see schedule
systemctl --user restart idigest-web          # after editing config.local.toml
```

## Web UI

- `/` — learning path, pause/resume, import (search / PDF).
- `/paper/{id}` — summary, figure, rendered math, **depth**, `<audio>` player, mark
  interesting/read, async "Explore deeper".
- `/audio/{id}.mp3` — streams the narration (HTTP range requests supported).
- `/jobs/{id}` — async job status (used by "Explore deeper" polling).

Behind HTTP Basic auth when `web.auth_user`/`auth_password` are set.

## Remote access

The UI is served over Tailscale (`tailscale serve`, tailnet-only). Open the tailnet URL
on any device signed into the same tailnet; log in with the UI credentials. To stop
sharing: `sudo tailscale serve --bg 8081 off`.

## Tuning

- **Email content** — `config.toml [email] full_depth` controls inline depth.
- **Ingestion scope** — `[ingest] keywords`, `arxiv_categories`, `relevance_min_score`,
  `max_new_per_run`.
- **Audio speed/quality** — `[audio] nfe_step` (16 ≈ 2× faster than 32, still natural),
  `mp3_bitrate`, `speed`. Episode length scales with paper difficulty.

## GPU memory budget (Strix Halo / ROCm)

The box is an **AMD Strix Halo** APU (Radeon 8060S, gfx1151) running llama.cpp on the
**ROCm** backend. The GPU has no dedicated VRAM — it shares the 62 GB system RAM — but
the amount it can actually use is **not** the full 62 GB by default. Three numbers, all
from `/sys/class/drm/card1/device/mem_info_*` and `/sys/module/ttm/parameters/`:

| Limit | Default | Meaning |
|---|---|---|
| `mem_info_vram_total` | **0.5 GB** | BIOS VRAM carveout (tiny; irrelevant here) |
| `mem_info_gtt_total` | **~31 GB** | system RAM the GPU may pin — defaults to **½ of RAM** |
| `ttm.pages_limit` | **~31 GB** | global cap on GPU-pinnable pages, shared by *all* GPU procs |

**The binding ceiling is ~31 GB (GTT/TTM = half of RAM), not the 62 GB total.** Measured
budget under that ceiling:

- Gemma-4-31B QAT (Q4_K_XL) + 16k-ctx KV + MTP draft + mmproj ≈ **24.6 GB** at rest.
- F5-TTS narration adds only **~0.7 GB** (synth peak 25.25 GB) — audio *alone* is fine.
- Heavy LLM inference (speculative decode + compute buffers) **transiently** spikes GTT.

That leaves only ~6.4 GB of headroom. The daily email runs `ensure_explanations` (LLM
spike) and then `ensure_audio` (TTS) back-to-back, so the combined transient peak can
touch the 31 GB ceiling and the kernel OOM-kills a process (seen as **exit 137**). Audio
is non-blocking, so the email still sends — just without narration.

**To use more of the 62 GB** (recommended; gives ~40–45 GB working room), raise the GTT
and TTM limits via kernel boot params. Append to the kernel cmdline (e.g. GRUB
`GRUB_CMDLINE_LINUX`, then `grub2-mkconfig` and reboot):

```
amdgpu.gttsize=49152 ttm.pages_limit=12582912 ttm.page_pool_size=12582912
```

`49152` = 48 GB GTT (MiB); `12582912` = 48 GB TTM (pages × 4 KiB). Verify after reboot:
`cat /sys/class/drm/card1/device/mem_info_gtt_total` (should read ~48 GB). Lower-impact
alternatives if you'd rather not touch boot params: blank `[llm.models.*] mmproj_path`
(frees ~1.2 GB, text-only digest doesn't use it), or trim `n_gpu_layers`.

## Troubleshooting / known gotchas

- **Email empty / model returns nothing** — Gemma always "thinks"; structured calls need
  a generous `max_tokens` to finish thinking before the JSON answer. (Handled in code.)
- **A process dies with exit 137 during email/generation** — GPU memory (GTT/TTM) ceiling
  hit; the LLM-spike + TTS back-to-back peak exceeds ~31 GB. Raise the GTT/TTM kernel
  limits (see *GPU memory budget* above). Audio still won't block the send.
- **Audio fails silently** — it never blocks the email; check
  `journalctl --user -u idigest-email`. Common gfx1151 fixes (`HSA_OVERRIDE_GFX_VERSION`,
  `PYTHONHASHSEED`) are applied automatically — see [AUDIO](AUDIO.md).
- **`systemctl --user` can't connect** — ensure `XDG_RUNTIME_DIR=/run/user/$(id -u)`.
- **Port already in use** — a stray manual `serve-web`; `systemctl --user restart
  idigest-web` rebinds.
- **Tests** — `pytest -q` (offline: store, pathing, dedup, JSON parsing, figure skip).
