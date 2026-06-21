"""Background job worker — the queue consumer.

Runs in a daemon thread inside the web process. It polls the ``jobs`` table
(the broker), claims one job at a time (so LLM work stays serialized to the
single loaded model), processes it, and records the result. The UI enqueues
jobs and polls their status; it never blocks on the LLM.
"""

from __future__ import annotations

import threading
import time

from . import generate, store
from .config import load_config

_started = False
_lock = threading.Lock()
_IDLE_SLEEP = 1.0


def start_worker() -> None:
    """Start the worker thread once per process."""
    global _started
    with _lock:
        if _started:
            return
        _started = True
    threading.Thread(target=_loop, name="idigest-worker", daemon=True).start()


def _loop() -> None:
    db = load_config()["paths"]["db"]
    with store.connect(db) as conn:
        while True:
            job = store.claim_next_job(conn)
            if job is None:
                time.sleep(_IDLE_SLEEP)
                continue
            try:
                result = _process(conn, job)
                store.finish_job(conn, job["id"], result)
            except Exception as e:  # a failed job must not kill the worker
                store.fail_job(conn, job["id"], f"{type(e).__name__}: {e}")


def _process(conn, job) -> str:
    if job["type"] == "explore":
        paper = store.get_paper(conn, job["paper_id"])
        extra = generate.explore_deeper(paper)
        new_depth = (paper["depth_md"] or "") + "\n\n## Deeper dive\n\n" + extra
        store.upsert_paper(
            conn, arxiv_id=paper["arxiv_id"], title=paper["title"], depth_md=new_depth
        )
        return "ok"
    raise ValueError(f"unknown job type {job['type']!r}")
