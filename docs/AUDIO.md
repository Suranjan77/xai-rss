# Audio narration (F5-TTS on the GPU)

Each daily paper gets a **single-narrator spoken explainer**, written for the ear and
synthesized locally on the GPU. The MP3 is **streamed** from this machine (linked in the
email and played in the UI) — not attached.

## Two stages

1. **Audio-native script** (`generate.write_audio_script`) — Gemma writes flowing spoken
   prose: no markdown, no headings, no LaTeX; math is spoken in words; signposted
   transitions; a hook open and a takeaway close. Length adapts to the paper's
   `difficulty` (~3.5 min easy → ~7.5 min hard).
2. **TTS** (`scripts/tts_synth.py`, run in `.venv-tts`) — F5-TTS synthesizes on `cuda`;
   `audio.py` encodes the WAV to a compact mono MP3 with ffmpeg.

The script is persisted (`papers.audio_script`) and the MP3 path (`papers.audio_path`),
so audio is generated once per paper. A `__none__` sentinel marks "tried, no audio".

## Why a separate venv

PyTorch has **no Python 3.14 wheels**, but Fedora packages a **ROCm/HIP build of
PyTorch 2.9.1 for the system Python 3.14**. So the TTS stack lives in
`.venv-tts` created with `--system-site-packages` (reusing that GPU torch), while the
main app (3.14, no torch) shells out to it. This also keeps the heavy TTS dependencies
isolated from the app.

## gfx1151 (Strix Halo) gotchas — all handled in `audio.py` / `tts_synth.py`

The GPU works, but four issues had to be solved for F5-TTS on this brand-new APU:

1. **`torchcodec` is CUDA-only.** pip pulls a CUDA build that fails to load on ROCm
   (`libnvrtc.so.13`). We uninstall it and shim `torchaudio.load` onto `soundfile`
   (F5-TTS only needs to read the reference WAV).
2. **`MIOpen rocBlas error` on gfx1151.** MIOpen/rocBLAS lack tuned kernels for gfx1151.
   Setting `HSA_OVERRIDE_GFX_VERSION=11.0.0` runs F5-TTS as **gfx1100 (RDNA3)**, which has
   tuned kernels. This is applied **only to the TTS subprocess**, so `llama.cpp`/Gemma
   keeps running natively on gfx1151 — both share the GPU concurrently (~31 GB GTT).
3. **Multi-batch worker hang on Python 3.14.** F5-TTS's multi-batch path spawns a helper
   that fails to initialize (`PYTHONHASHSEED`) and hangs. We drive F5-TTS **one ≤120-char
   chunk at a time** (`chunk_text`) and concatenate — the single-batch path is reliable.
4. **`PYTHONHASHSEED`** is set to `0` for the subprocess so any spawned helpers init
   cleanly under Python 3.14.

`audio.py` sets `HSA_OVERRIDE_GFX_VERSION=11.0.0` and `PYTHONHASHSEED=0` in the subprocess
environment automatically.

## Performance

Per-chunk synthesis is thorough but not fast (≈ a few minutes for a multi-minute
episode). It runs in the nightly email job, so latency is fine — the email simply lands a
few minutes after 07:00. To speed up, lower `[audio] nfe_step` (16 is ~2× faster than 32
and still natural).

## Config (`config.toml [audio]`)

| Key | Meaning |
|---|---|
| `enabled` | Generate audio at all |
| `ref_file` / `ref_text` | Reference voice clip + its transcript (defines the narrator) |
| `nfe_step` | ODE solver steps (quality/speed) |
| `speed` | Speaking rate |
| `mp3_bitrate` | Output bitrate (default `64k`, mono) |
| `timeout_s` | Hard cap on a synthesis run |

## Changing the voice

F5-TTS is reference-based (zero-shot). Point `ref_file` at a clean ~5–10 s WAV of the
voice you want and set `ref_text` to its exact transcript.
