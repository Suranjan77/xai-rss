"""Load the curated seed corpus into the store.

Inserts each paper, sets its concept edges (the prerequisite graph), embeds its
title+abstract (CPU), and assigns an explicit learning-path position from the
seed's `order`. Idempotent: re-running updates in place.
"""

from __future__ import annotations

import json
from pathlib import Path

from . import embeddings, pathing, store
from .config import load_config


def load_seed() -> int:
    cfg = load_config()
    corpus_path = Path(cfg["paths"]["seed_dir"]) / "corpus.json"
    data = json.loads(corpus_path.read_text())
    papers = data["papers"]

    # Embed all at once (one CPU model load) for speed.
    texts = [f"{p['title']}. {p['abstract']}" for p in papers]
    vecs = embeddings.embed(texts)

    with store.connect(cfg["paths"]["db"]) as conn:
        store.init_db(conn, dim=cfg["embeddings"]["dim"])
        for p, vec in zip(papers, vecs):
            pid = store.upsert_paper(
                conn,
                arxiv_id=p.get("arxiv_id"),
                title=p["title"],
                authors=p.get("authors", []),
                published=p.get("published"),
                abstract=p.get("abstract"),
                pdf_url=p.get("pdf_url"),
                topics=p.get("topics", []),
                difficulty=p.get("difficulty"),
                source="seed",
            )
            store.set_paper_concepts(
                conn,
                pid,
                provides=[tuple(x) for x in p.get("provides", [])],
                requires=[tuple(x) for x in p.get("requires", [])],
            )
            store.set_embedding(conn, pid, vec)
            pathing.set_position(conn, pid, float(p["order"]) * pathing.POSITION_STEP)
    return len(papers)
