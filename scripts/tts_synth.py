#!/usr/bin/env python3
"""Standalone F5-TTS synthesis, run by the .venv-tts interpreter (GPU torch).

The main app (Python 3.14 venv, no torch) shells out to this script so the heavy
GPU TTS stack stays isolated. Reads narration text from a file, writes a WAV.

Usage:
  .venv-tts/bin/python scripts/tts_synth.py --text-file IN.txt --out-wav OUT.wav \
      --ref REF.wav --ref-text "..." [--speed 1.0] [--nfe 32]
"""

from __future__ import annotations

import argparse
import sys


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--text-file", required=True)
    ap.add_argument("--out-wav", required=True)
    ap.add_argument("--ref", required=True)
    ap.add_argument("--ref-text", required=True)
    ap.add_argument("--speed", type=float, default=1.0)
    ap.add_argument("--nfe", type=int, default=32)
    args = ap.parse_args()

    with open(args.text_file, encoding="utf-8") as f:
        gen_text = f.read().strip()
    if not gen_text:
        print("empty narration text", file=sys.stderr)
        return 2

    import numpy as np
    import torch

    # torchaudio 2.9 routes .load through torchcodec, whose PyPI build wants CUDA
    # (libnvrtc) and fails on ROCm. F5-TTS only needs to read the reference WAV,
    # so shim torchaudio.load onto soundfile (returns [channels, frames], sr).
    import torchaudio
    import soundfile as sf

    def _sf_load(path, *a, **k):
        data, sr = sf.read(str(path), dtype="float32", always_2d=True)  # (frames, ch)
        return torch.from_numpy(data.T).contiguous(), sr

    torchaudio.load = _sf_load

    from f5_tts.api import F5TTS
    from f5_tts.infer.utils_infer import chunk_text

    device = "cuda" if torch.cuda.is_available() else "cpu"
    f5 = F5TTS(device=device)

    # Drive F5-TTS one small (single-batch) chunk at a time and concatenate.
    # The multi-batch path spawns a worker that fails to init under Python 3.14;
    # single-batch inference is reliable. ~120 chars keeps each call to one batch.
    chunks = chunk_text(gen_text, max_chars=120) or [gen_text]
    pieces, sr = [], f5.target_sample_rate
    gap = np.zeros(int(0.15 * sr), dtype=np.float32)
    for i, ch in enumerate(chunks):
        if not ch.strip():
            continue
        wav, sr, _ = f5.infer(
            ref_file=args.ref, ref_text=args.ref_text, gen_text=ch,
            nfe_step=args.nfe, speed=args.speed, remove_silence=False,
        )
        pieces.append(np.asarray(wav, dtype=np.float32))
        pieces.append(gap)
        print(f"  chunk {i + 1}/{len(chunks)} done", flush=True)

    final = np.concatenate(pieces) if pieces else np.zeros(1, dtype=np.float32)
    sf.write(args.out_wav, final, sr)
    print(f"wrote {args.out_wav} on {device} ({len(final) / sr:.1f}s, {len(chunks)} chunks)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
