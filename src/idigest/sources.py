"""External research sources: arXiv and Semantic Scholar search.

Returns normalized paper dicts: title, abstract, arxiv_id, authors (list),
published (ISO), pdf_url. Shared by manual import (importer.py) and the
scheduled ingestion job (ingest.py).
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import quote_plus

import feedparser
import httpx

_ARXIV_API = "https://export.arxiv.org/api/query"
_S2_API = "https://api.semanticscholar.org/graph/v1/paper/search"


def _arxiv_id_from_url(url: str) -> str | None:
    m = re.search(r"arxiv\.org/abs/([^v\s]+)", url)
    return m.group(1) if m else None


def search_arxiv(query: str, max_results: int = 10) -> list[dict[str, Any]]:
    params = {
        "search_query": f"all:{query}",
        "start": 0,
        "max_results": max_results,
        "sortBy": "relevance",
        "sortOrder": "descending",
    }
    try:
        r = httpx.get(_ARXIV_API, params=params, timeout=30, follow_redirects=True)
        r.raise_for_status()
    except httpx.HTTPError:
        return []
    feed = feedparser.parse(r.text)
    out = []
    for e in feed.entries:
        aid = _arxiv_id_from_url(e.get("id", ""))
        pdf = next(
            (l.href for l in e.get("links", []) if l.get("type") == "application/pdf"),
            f"https://arxiv.org/pdf/{aid}" if aid else None,
        )
        out.append(
            {
                "title": re.sub(r"\s+", " ", e.get("title", "")).strip(),
                "abstract": re.sub(r"\s+", " ", e.get("summary", "")).strip(),
                "arxiv_id": aid,
                "authors": [a.name for a in e.get("authors", [])],
                "published": (e.get("published", "") or "")[:10],
                "pdf_url": pdf,
            }
        )
    return out


def search_arxiv_recent(categories: list[str], keywords: list[str], max_results: int = 30):
    """Recent submissions in the given categories matching any keyword."""
    cat = " OR ".join(f"cat:{c}" for c in categories)
    kw = " OR ".join(f'abs:"{k}"' for k in keywords)
    params = {
        "search_query": f"({cat}) AND ({kw})",
        "start": 0,
        "max_results": max_results,
        "sortBy": "submittedDate",
        "sortOrder": "descending",
    }
    try:
        r = httpx.get(_ARXIV_API, params=params, timeout=30, follow_redirects=True)
        r.raise_for_status()
    except httpx.HTTPError:
        return []
    feed = feedparser.parse(r.text)
    results = []
    for e in feed.entries:
        aid = _arxiv_id_from_url(e.get("id", ""))
        results.append(
            {
                "title": re.sub(r"\s+", " ", e.get("title", "")).strip(),
                "abstract": re.sub(r"\s+", " ", e.get("summary", "")).strip(),
                "arxiv_id": aid,
                "authors": [a.name for a in e.get("authors", [])],
                "published": (e.get("published", "") or "")[:10],
                "pdf_url": f"https://arxiv.org/pdf/{aid}" if aid else None,
            }
        )
    return results


def search_semantic_scholar(query: str, max_results: int = 10) -> list[dict[str, Any]]:
    fields = "title,abstract,year,authors,externalIds,openAccessPdf"
    try:
        r = httpx.get(
            _S2_API,
            params={"query": query, "limit": max_results, "fields": fields},
            timeout=30,
            follow_redirects=True,
        )
        r.raise_for_status()
        data = r.json()
    except (httpx.HTTPError, ValueError):
        return []
    out = []
    for p in data.get("data", []):
        ext = p.get("externalIds") or {}
        oa = p.get("openAccessPdf") or {}
        out.append(
            {
                "title": (p.get("title") or "").strip(),
                "abstract": (p.get("abstract") or "").strip(),
                "arxiv_id": ext.get("ArXiv"),
                "authors": [a.get("name") for a in (p.get("authors") or [])],
                "published": str(p.get("year") or ""),
                "pdf_url": oa.get("url")
                or (f"https://arxiv.org/pdf/{ext['ArXiv']}" if ext.get("ArXiv") else None),
            }
        )
    return [p for p in out if p["title"] and p["abstract"]]
