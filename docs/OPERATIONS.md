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

## Troubleshooting / known gotchas

- **Email empty / model returns nothing** — Gemma always "thinks"; structured calls need
  a generous `max_tokens` to finish thinking before the JSON answer. (Handled in code.)
- **Audio fails silently** — it never blocks the email; check
  `journalctl --user -u idigest-email`. Common gfx1151 fixes (`HSA_OVERRIDE_GFX_VERSION`,
  `PYTHONHASHSEED`) are applied automatically — see [AUDIO](AUDIO.md).
- **`systemctl --user` can't connect** — ensure `XDG_RUNTIME_DIR=/run/user/$(id -u)`.
- **Port already in use** — a stray manual `serve-web`; `systemctl --user restart
  idigest-web` rebinds.
- **Tests** — `pytest -q` (offline: store, pathing, dedup, JSON parsing, figure skip).
