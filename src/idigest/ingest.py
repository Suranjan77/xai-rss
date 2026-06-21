"""Scheduled ingestion: pull recent interpretability papers and add the good ones.

Queries recent arXiv submissions in the configured categories/keywords, dedups
against the KB, gates on an LLM relevance score, then imports (summary + concept
extraction + auto-slot) via importer.import_paper. Capped by max_new_per_run.
"""

from __future__ import annotations

from . import generate, importer, sources, store
from .config import load_config


def run() -> int:
    cfg = load_config()
    ing = cfg["ingest"]
    candidates = sources.search_arxiv_recent(
        ing["arxiv_categories"], ing["keywords"], max_results=ing["max_new_per_run"] * 4
    )

    added = 0
    with store.connect(cfg["paths"]["db"]) as conn:
        for c in candidates:
            if added >= ing["max_new_per_run"]:
                break
            if not c.get("abstract"):
                continue
            # cheap dedup first (exact arxiv id already present)
            if c.get("arxiv_id") and conn.execute(
                "SELECT 1 FROM papers WHERE arxiv_id=?", (c["arxiv_id"],)
            ).fetchone():
                continue
            try:
                score = generate.relevance_score(c["title"], c["abstract"])
                if score < ing["relevance_min_score"]:
                    print(f"  [skip {score}/10] {c['title'][:64]}")
                    continue
                pid, created = importer.import_paper(conn, c, source="ingest")
            except Exception as e:  # one bad paper must not kill the batch
                print(f"  [error] {c['title'][:54]}: {type(e).__name__}: {e}")
                continue
            print(f"  [{'add ' + str(score) + '/10' if created else 'dup'}] {c['title'][:64]}")
            if created:
                added += 1
    return added
