"""Scheduled ingestion: pull recent interpretability papers and add the good ones.

Queries recent arXiv submissions in the configured categories/keywords, dedups
against the KB, gates on an LLM relevance score, then imports (summary + concept
extraction + auto-slot) via importer.import_paper. Capped by max_new_per_run.
"""

from __future__ import annotations

from . import email_send, generate, importer, sources, store
from .config import load_config


def pregenerate_next() -> None:
    """Pre-generate the next email's content (explanation, figure, audio) so the
    morning send is instant. Failures here never raise — the email job regenerates."""
    cfg = load_config()
    with store.connect(cfg["paths"]["db"]) as conn:
        if store.emails_paused(conn):
            return
        nxt = email_send.pick_next(conn)
        if nxt is None:
            return
        pid = nxt["id"]
    try:
        with store.connect(cfg["paths"]["db"]) as conn:
            generate.ensure_explanations(conn, pid)
        with store.connect(cfg["paths"]["db"]) as conn:
            generate.ensure_audio(conn, pid)
        print(f"pre-generated content for paper {pid}: {nxt['title'][:60]}")
    except Exception as e:
        print(f"pre-generation failed (email job will retry): {type(e).__name__}: {e}")


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

    # Operational hardening: prepare the morning email now so 07:30 send is instant.
    pregenerate_next()
    return added
