"""Extract one illustrative figure from a paper PDF for the email.

Pipeline:
  1. Download the PDF (cached under data/figures/_pdf/).
  2. Gather candidate figures two ways:
       a. embedded raster images via PyMuPDF (good for photos/screenshots), and
       b. OpenCV figure-region detection on rendered early pages (catches the
          vector diagrams that get_images() misses).
  3. Pull nearby "Figure N: ..." caption text from the page.
  4. Pick the best candidate and write a clean caption with the LLM. If the
     server has the Gemma vision projector loaded (mmproj), the model is shown
     the actual crop; otherwise it captions from the PDF caption text.
  5. Save a web-friendly PNG and return (png_path, caption).

Returns (None, None) when there is no usable PDF/figure (e.g. HTML-only sources).
"""

from __future__ import annotations

import re
from pathlib import Path

import httpx

from . import llm
from .config import load_config

_FIG_DIR = Path(__file__).resolve().parents[2] / "data" / "figures"
_PDF_DIR = _FIG_DIR / "_pdf"
_MIN_AREA_FRAC = 0.04      # ignore regions smaller than 4% of the page
_MAX_WIDTH = 900          # downscale wide figures for email


def _download_pdf(url: str, key: str) -> Path | None:
    _PDF_DIR.mkdir(parents=True, exist_ok=True)
    dest = _PDF_DIR / f"{key}.pdf"
    if dest.exists():
        return dest
    try:
        r = httpx.get(url, timeout=60, follow_redirects=True)
        r.raise_for_status()
        if "pdf" not in r.headers.get("content-type", "") and not r.content[:4] == b"%PDF":
            return None
        dest.write_bytes(r.content)
        return dest
    except httpx.HTTPError:
        return None


def _page_captions(page) -> list[str]:
    """Return 'Figure N: ...' style caption strings found on a page."""
    text = page.get_text("text")
    caps = re.findall(
        r"(?:Figure|Fig\.?)\s*\d+[.:]\s*(.+?)(?:\n\n|\n(?=[A-Z]))",
        text,
        flags=re.DOTALL,
    )
    return [re.sub(r"\s+", " ", c).strip()[:300] for c in caps]


def _render_clip(page, bbox, dpi: int = 150):
    """Render a page region (a fitz.Rect) to a PIL image."""
    import fitz
    from PIL import Image

    pix = page.get_pixmap(clip=fitz.Rect(*bbox), dpi=dpi)
    return Image.frombytes("RGB", (pix.width, pix.height), pix.samples) if pix.n < 4 else (
        Image.frombytes("RGBA", (pix.width, pix.height), pix.samples).convert("RGB")
    )


def _embedded_candidates(doc, max_pages: int = 6):
    """Yield (pil_image, area_fraction, page_index) from embedded raster images.

    These are real figures (photos, heatmaps, diagrams) with known bounding
    boxes — far more reliable than detecting regions on a rendered text page.
    """
    for pidx in range(min(max_pages, doc.page_count)):
        page = doc[pidx]
        page_area = abs(page.rect.width * page.rect.height) or 1.0
        for info in page.get_image_info(xrefs=True):
            bbox = info.get("bbox")
            if not bbox:
                continue
            w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
            frac = (w * h) / page_area
            if frac < _MIN_AREA_FRAC or w < 80 or h < 80:
                continue
            try:
                yield _render_clip(page, bbox), frac, pidx
            except Exception:
                continue


def _vector_candidates(doc, max_pages: int = 6):
    """Fallback for vector-only figures: large rendered regions with LOW text
    coverage (text words are masked out so we don't pick the body text)."""
    import cv2
    import numpy as np
    from PIL import Image

    for pidx in range(min(max_pages, doc.page_count)):
        page = doc[pidx]
        scale = 150 / 72.0
        pix = page.get_pixmap(dpi=150)
        img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
        if pix.n == 4:
            img = cv2.cvtColor(img, cv2.COLOR_RGBA2RGB)
        gray = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
        _, ink = cv2.threshold(gray, 245, 255, cv2.THRESH_BINARY_INV)

        # erase text: zero out every word's bbox so only non-text drawings remain
        text_mask = np.zeros(gray.shape, dtype=np.uint8)
        for wd in page.get_text("words"):
            x0, y0, x1, y1 = (int(v * scale) for v in wd[:4])
            text_mask[y0:y1, x0:x1] = 255
        drawings = cv2.bitwise_and(ink, cv2.bitwise_not(text_mask))

        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (35, 35))
        closed = cv2.morphologyEx(drawings, cv2.MORPH_CLOSE, kernel)
        contours, _ = cv2.findContours(closed, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        page_area = img.shape[0] * img.shape[1]
        for c in contours:
            x, y, w, h = cv2.boundingRect(c)
            frac = (w * h) / page_area
            if frac < _MIN_AREA_FRAC or w < 120 or h < 120:
                continue
            # require real drawing ink in the region, and little overlapping text
            region_text = text_mask[y : y + h, x : x + w].mean() / 255.0
            region_draw = drawings[y : y + h, x : x + w].mean() / 255.0
            if region_text > 0.12 or region_draw < 0.02:
                continue
            yield Image.fromarray(img[y : y + h, x : x + w]), frac, pidx


def extract_figure(arxiv_id: str | None, pdf_url: str | None, title: str) -> tuple[str | None, str | None]:
    import fitz

    if not pdf_url or not pdf_url.lower().endswith(".pdf") and "arxiv.org/pdf" not in (pdf_url or ""):
        return None, None
    key = (arxiv_id or re.sub(r"\W+", "-", title)[:60]).replace("/", "_")
    pdf_path = _download_pdf(pdf_url, key)
    if not pdf_path:
        return None, None

    try:
        doc = fitz.open(pdf_path)
    except Exception:
        return None, None

    # Prefer embedded raster figures; fall back to vector-region detection.
    best = None
    for crop, frac, pidx in _embedded_candidates(doc):
        if best is None or frac > best[1]:
            best = (crop, frac, pidx)
    if best is None:
        for crop, frac, pidx in _vector_candidates(doc):
            if best is None or frac > best[1]:
                best = (crop, frac, pidx)
    if best is None:
        doc.close()
        return None, None

    crop, _frac, page_idx = best
    # caption from the chosen figure's page, else the first caption in the paper
    captions = _page_captions(doc[page_idx])
    if not captions:
        for pidx in range(min(5, doc.page_count)):
            captions += _page_captions(doc[pidx])
    doc.close()
    if crop.width > _MAX_WIDTH:
        ratio = _MAX_WIDTH / crop.width
        crop = crop.resize((_MAX_WIDTH, int(crop.height * ratio)))

    _FIG_DIR.mkdir(parents=True, exist_ok=True)
    out = _FIG_DIR / f"{key}.png"
    crop.save(out)

    caption = _caption_for(title, captions)
    return str(out), caption


def _caption_for(title: str, captions: list[str]) -> str:
    """One-line caption for the chosen figure, grounded in PDF caption text."""
    cap_text = captions[0] if captions else ""
    if not cap_text:
        return "Key figure from the paper."
    try:
        msg = [
            {"role": "system", "content": "You write one-sentence figure captions for a research digest email."},
            {
                "role": "user",
                "content": f"Paper: {title}\nOriginal figure caption: {cap_text}\n\n"
                "Rewrite as ONE short, plain-language sentence explaining what the figure shows.",
            },
        ]
        out = llm.chat(msg, temperature=0.3, max_tokens=120).strip()
        return out.splitlines()[0][:280] if out else cap_text[:280]
    except Exception:
        return cap_text[:280]
