# Architecture

`idigest` is a local-first tool that maintains a curated, growing knowledge base of
explainable-AI / interpretability research and delivers it as a **daily byte-sized
email**, ordered so earlier papers teach the prerequisites for later ones. Full depth
(with rendered math and figures) and a **spoken audio narration** are available too.
Everything runs on the user's machine; a local LLM does all generation.

## Design principles

1. **Local-first.** No cloud LLM. Generation runs on a local `llama-server`
   (Gemma-4-12B). Embeddings run on CPU. TTS runs on the local GPU.
2. **One GPU LLM at a time.** Only Gemma is loaded in `llama-server`. Embeddings use a
   CPU ONNX model so they never compete for the GPU. (Audio TTS is a separate process
   that shares the GPU with Gemma — see [Audio](#audio).)
3. **Prerequisite ordering is the headline feature.** Papers form a concept graph; the
   email order is a walk over that graph, not just chronology or similarity.
4. **Generate once, reuse.** Explanations, figures, and audio are persisted; the LLM is
   only re-run when content is missing.

## Components

```
            ┌──────────────────────────────────────────────────────────┐
            │                        idigest (main venv, Python 3.14)    │
            │                                                            │
  arXiv ───▶│  ingest.py ─┐                                             │
  S2    ───▶│             ├─▶ importer.py ─▶ generate.py ─▶ store.py    │
  PDFs  ───▶│  importer ──┘        │            │             │         │
            │                      │            │             ▼         │
            │  seed.py ────────────┘            │        SQLite + vec   │
            │                                   │        + FTS5         │
            │  email_send.py ◀──────────────────┤             ▲         │
            │       │  pathing.py (ordering)    │             │         │
            │       ▼                           ▼             │         │
            │   Gmail SMTP                  llm.py ───▶ llama-server     │
            │                              embeddings.py (CPU ONNX)      │
            │  web/app.py (FastAPI UI) ─── worker.py (job queue)         │
            │       │                                                    │
            │       └─▶ audio.py ──subprocess──▶ .venv-tts/tts_synth.py │
            └──────────────────────────────────────────────│───────────┘
                                                            ▼
                                                  F5-TTS on the GPU (ROCm)
```

### Storage (`store.py`)
A single SQLite database with three cooperating layers:
- **Source of truth** — the `papers` table (metadata, generated `summary_md`,
  `intuition_md`, `key_insight`, `depth_md`, `figure_path`, `audio_path`, status).
- **Prerequisite graph** — `concepts` + `paper_concepts` (`provides` / `requires`
  edges) and `learning_path` (an ordered `position` per paper).
- **Search** — `vec_papers` (sqlite-vec, cosine, 384-dim) for semantic dedup/related,
  and `papers_fts` (FTS5) for keyword search.

Also: `email_log` (one send per paper, prevents repeats), `app_state` (key/value, e.g.
the email pause flag), and `jobs` (the async work queue / broker).

### Ordering (`pathing.py`)
`ordered_path()` returns papers by `learning_path.position`. New papers are
**auto-slotted**: placed just after the latest paper that *provides* a concept the new
paper *requires*. Concepts that no paper provides (general ML background) don't gate
ordering. Seed papers carry explicit positions; this is the reliable backbone.

### Generation (`generate.py`, `llm.py`, `embeddings.py`)
All LLM calls go to a single `llama-server` (Gemma-4-12B). Prompts are deliberately
directive (grounded role, fidelity rule, strict JSON, explicit LaTeX rules):
- `write_explanations` — byte-size summary/intuition/key-insight (notation-free) + a
  structured `depth` with LaTeX math.
- `summarize_and_extract` — ingestion: summary, difficulty, topics, and the
  `provides`/`requires` concept edges for the graph.
- `relevance_score` — 0–10 gate for ingested papers.
- `write_audio_script` — a spoken-word narration (no markdown/LaTeX, math in words),
  length adaptive to difficulty.
- `explore_deeper` — on-demand deeper technical dive (async via the job queue).

Embeddings use `fastembed` (`bge-small-en-v1.5`, ONNX) on the **CPU**.

### Ingestion (`ingest.py`, `sources.py`, `importer.py`)
`sources.py` queries arXiv (Atom API) and Semantic Scholar. `ingest.py` fetches recent
interpretability papers, dedups (cosine), gates on `relevance_score`, then imports.
`importer.py` also powers manual `add-search` and `add-pdf`. All paths funnel through
`importer.import_paper` (dedup → summarize/extract → embed → auto-slot → figure).

### Figures (`figures.py`)
Extracts one illustrative figure from the paper PDF: PyMuPDF embedded raster images
first, OpenCV figure-region detection (with a text mask) as fallback, plus the nearby
"Figure N:" caption. Embedded inline in the email and shown in the UI.

### Math (`mathmd.py`)
`dollarmath` parses `$...$` / `$$...$$` so markdown can't mangle it. The **UI** renders
via MathJax; the **email** (no JS) renders each math span to an inline PNG with
matplotlib, attached as a `cid:` image, with a graceful fallback for unsupported LaTeX.

### Email (`email_send.py`)
Picks the next unsent paper by learning-path order, ensures explanations + figure +
audio, renders a byte-size email (summary + intuition + key insight) with the **full
depth inline**, the figure, rendered math, and a **stream link** to the audio. Sends via
Gmail SMTP (app password). Audio is streamed from the server, not attached.

### Web UI (`web/app.py`)
FastAPI + server-rendered templates: the learning path, a paper depth view (rendered
math + figure + `<audio>` player), mark interesting/read, search/PDF import, pause/resume
emails, and an async **"Explore deeper"**. Optional HTTP Basic auth gates everything.

### Async jobs (`worker.py`)
A SQLite `jobs` table is the broker. A daemon worker thread (started by the web app)
claims jobs atomically (`UPDATE ... RETURNING`) and runs them one at a time (keeping LLM
work serialized). The UI enqueues "Explore deeper" and polls `GET /jobs/{id}`.

### Audio
See [AUDIO](AUDIO.md). The script is written by Gemma; F5-TTS synthesizes it on the GPU
in a separate `.venv-tts` (system ROCm PyTorch). `audio.py` shells out per paper, encodes
to MP3 with ffmpeg, and the UI streams it (range requests supported).

## Data flow: a day in the life
1. **06:30** `idigest-ingest` pulls new arXiv/S2 papers, filters, summarizes, slots them.
2. **07:00** `idigest-email` picks the next paper on the path, generates anything missing
   (explanation, figure, audio), and emails it with a stream link.
3. Anytime: browse the path in the UI (over the tailnet), listen on a walk, mark papers
   interesting/read, or "Explore deeper".

## Process / deployment model
- `idigest-llm.service` — `llama-server` (Gemma-4-12B + MTP), always up.
- `idigest-web.service` — the FastAPI UI, always up; exposed over Tailscale.
- `idigest-ingest.timer` / `idigest-email.timer` — daily jobs.
- TTS runs on demand as a subprocess in `.venv-tts`.
