"""Manual import of research: by web search or from a PDF.

Both paths funnel through ``import_paper`` which dedups (cosine vector match),
generates a summary + concept edges, embeds, auto-slots into the learning path,
and grabs a key figure. The scheduled ingest job (ingest.py) reuses the same
``import_paper``.
"""

from __future__ import annotations

import re
import sqlite3
import tempfile
from pathlib import Path
from typing import Any

import httpx

from . import embeddings, generate, pathing, sources, store
from .config import load_config


def _duplicate_of(conn: sqlite3.Connection, vec, threshold: float) -> int | None:
    near = store.nearest(conn, vec, k=1)
    if near:
        pid, dist = near[0]
        if (1.0 - dist) >= threshold:  # cosine distance -> similarity
            return pid
    return None


def import_paper(
    conn: sqlite3.Connection, meta: dict[str, Any], *, source: str = "import",
    with_figure: bool = True,
) -> tuple[int, bool]:
    """Import one paper dict. Returns (paper_id, created)."""
    cfg = load_config()
    title, abstract = meta["title"], meta.get("abstract", "")
    vec = embeddings.embed_one(f"{title}. {abstract}")
    dup = _duplicate_of(conn, vec, cfg["embeddings"]["dedup_cosine_threshold"])
    if dup is not None:
        return dup, False

    extracted = generate.summarize_and_extract(title, abstract)
    pid = store.upsert_paper(
        conn,
        arxiv_id=meta.get("arxiv_id"),
        title=title,
        authors=meta.get("authors", []),
        published=meta.get("published"),
        abstract=abstract,
        pdf_url=meta.get("pdf_url"),
        topics=extracted["topics"],
        difficulty=extracted["difficulty"],
        summary_md=extracted["summary"],
        source=source,
    )
    store.set_paper_concepts(
        conn, pid, provides=extracted["provides"], requires=extracted["requires"]
    )
    store.set_embedding(conn, pid, vec)
    pathing.auto_slot(conn, pid)
    if with_figure:
        generate.ensure_figure(conn, pid)
    return pid, True


def add_from_search(query: str, limit: int = 5, auto: bool = False) -> int:
    """Search arXiv + Semantic Scholar for a query and import the top hits."""
    cfg = load_config()
    hits = sources.search_arxiv(query, max_results=limit * 2)
    seen = {h["arxiv_id"] for h in hits if h["arxiv_id"]}
    for h in sources.search_semantic_scholar(query, max_results=limit * 2):
        if h["arxiv_id"] and h["arxiv_id"] in seen:
            continue
        hits.append(h)

    imported = 0
    with store.connect(cfg["paths"]["db"]) as conn:
        for h in hits:
            if imported >= limit:
                break
            if not h.get("abstract"):
                continue
            try:
                pid, created = import_paper(conn, h, source="search")
            except Exception as e:
                print(f"  [error] {h['title'][:60]}: {type(e).__name__}: {e}")
                continue
            print(f"  [{'added' if created else 'dup'}] {h['title'][:72]}")
            if created:
                imported += 1
    return imported


# --------------------------------------------------------------------------- #
# PDF import
# --------------------------------------------------------------------------- #
def _fetch_pdf(source: str) -> tuple[Path, str | None]:
    """Return (local_path, original_url_or_None)."""
    if re.match(r"^https?://", source):
        try:
            r = httpx.get(source, timeout=60, follow_redirects=True)
            r.raise_for_status()
        except httpx.HTTPError as e:
            raise RuntimeError(f"could not download PDF: {e}") from e
        if r.content[:4] != b"%PDF":
            raise ValueError(f"{source} did not return a PDF")
        tmp = Path(tempfile.gettempdir()) / f"idigest_import_{abs(hash(source))}.pdf"
        tmp.write_bytes(r.content)
        return tmp, source
    p = Path(source).expanduser()
    if not p.exists():
        raise FileNotFoundError(f"no such file: {source}")
    return p, None


def _pdf_title_abstract(path: Path) -> tuple[str, str]:
    import fitz

    doc = fitz.open(path)
    title = (doc.metadata or {}).get("title") or ""
    page0 = doc[0].get_text("text")
    if not title.strip():
        # first substantial line on page 1 is usually the title
        for line in page0.splitlines():
            s = line.strip()
            if len(s) > 12 and not s.lower().startswith(("arxiv", "http")):
                title = s
                break
    # grab the Abstract section, else the first ~1800 chars of page 1
    m = re.search(r"Abstract\s*[:.\n]\s*(.+?)(?:\n\s*\n|Introduction)", page0, re.DOTALL | re.IGNORECASE)
    abstract = re.sub(r"\s+", " ", (m.group(1) if m else page0)).strip()[:1800]
    doc.close()
    return title.strip()[:300], abstract


def add_from_pdf(source: str) -> int:
    cfg = load_config()
    path, url = _fetch_pdf(source)
    title, abstract = _pdf_title_abstract(path)
    aid = None
    if url and "arxiv.org" in url:
        m = re.search(r"(\d{4}\.\d{4,5})", url)
        aid = m.group(1) if m else None
    meta = {
        "title": title,
        "abstract": abstract,
        "arxiv_id": aid,
        "pdf_url": url or str(path),
        "authors": [],
        "published": None,
    }
    with store.connect(cfg["paths"]["db"]) as conn:
        pid, created = import_paper(conn, meta, source="pdf")
    print(f"  [{'added' if created else 'dup'}] {title[:72]}")
    return pid
