"""SQLite store: source of truth + vector search (sqlite-vec) + keyword (FTS5).

The layered design:
  - ``papers``          : the canonical record for every paper.
  - ``concepts`` + ``paper_concepts`` : the prerequisite graph (provides/requires).
  - ``learning_path``   : the ordered "learn this first" sequence (the headline feature).
  - ``email_log``       : which paper was emailed on which day (prevents repeats).
  - ``vec_papers``      : sqlite-vec virtual table of abstract embeddings (dedup/related).
  - ``papers_fts``      : FTS5 keyword index over title+abstract.

Embeddings are 384-dim CPU vectors (see embeddings.py); they never touch the GPU.
"""

from __future__ import annotations

import json
import sqlite3
import struct
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable, Iterator, Sequence

import sqlite_vec

# Statuses a paper can be in. "unread" papers are eligible for the daily email.
STATUSES = ("unread", "queued", "read", "interesting")


def _vec_blob(vec: Sequence[float]) -> bytes:
    """Pack a float vector into the little-endian float32 blob sqlite-vec wants."""
    return struct.pack(f"<{len(vec)}f", *vec)


@contextmanager
def connect(db_path: str | Path) -> Iterator[sqlite3.Connection]:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    conn.execute("PRAGMA busy_timeout=5000;")  # the worker + web share the db (WAL)
    try:
        yield conn
    finally:
        conn.close()


def init_db(conn: sqlite3.Connection, dim: int) -> None:
    """Create all tables/indexes if absent. Safe to call repeatedly."""
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS papers (
            id          INTEGER PRIMARY KEY,
            arxiv_id    TEXT UNIQUE,
            title       TEXT NOT NULL,
            authors     TEXT,                 -- JSON array
            published   TEXT,                 -- ISO date
            abstract    TEXT,
            pdf_url     TEXT,
            topics      TEXT,                 -- JSON array
            difficulty  INTEGER,              -- 1 (intro) .. 5 (advanced)
            status      TEXT NOT NULL DEFAULT 'unread',
            source      TEXT NOT NULL DEFAULT 'ingest',  -- 'seed' | 'ingest'
            summary_md     TEXT,              -- byte-size summary (email)
            intuition_md   TEXT,              -- intuitive explanation (email)
            key_insight    TEXT,              -- one-line "aha"
            depth_md       TEXT,              -- full depth (UI)
            figure_path    TEXT,              -- cached key figure (PNG) for the email
            figure_caption TEXT,              -- LLM/extracted caption for the figure
            audio_script   TEXT,              -- spoken-word narration script
            audio_path     TEXT,              -- cached MP3 narration
            created_at  TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS concepts (
            id    INTEGER PRIMARY KEY,
            slug  TEXT UNIQUE NOT NULL,       -- e.g. 'shapley-values'
            name  TEXT NOT NULL
        );

        -- The prerequisite graph. relation: 'provides' (paper teaches concept)
        -- or 'requires' (paper assumes concept). Topological order over these
        -- edges yields the learning sequence.
        CREATE TABLE IF NOT EXISTS paper_concepts (
            paper_id   INTEGER NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
            concept_id INTEGER NOT NULL REFERENCES concepts(id) ON DELETE CASCADE,
            relation   TEXT NOT NULL CHECK (relation IN ('provides','requires')),
            PRIMARY KEY (paper_id, concept_id, relation)
        );

        -- Ordered learning path. Lower position = learn earlier. Seed papers get
        -- explicit positions; ingested papers are auto-slotted (pathing.py).
        CREATE TABLE IF NOT EXISTS learning_path (
            paper_id  INTEGER PRIMARY KEY REFERENCES papers(id) ON DELETE CASCADE,
            position  REAL NOT NULL          -- REAL so we can insert between rows
        );
        CREATE INDEX IF NOT EXISTS idx_path_position ON learning_path(position);

        CREATE TABLE IF NOT EXISTS email_log (
            id        INTEGER PRIMARY KEY,
            paper_id  INTEGER NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
            sent_date TEXT NOT NULL,          -- ISO date, one send per day
            UNIQUE(paper_id)
        );

        CREATE VIRTUAL TABLE IF NOT EXISTS papers_fts USING fts5(
            title, abstract
        );

        -- key/value app state (e.g. email pause flag), flippable at runtime.
        CREATE TABLE IF NOT EXISTS app_state (
            key   TEXT PRIMARY KEY,
            value TEXT
        );

        -- durable job queue (the "broker"). A background worker claims queued
        -- jobs one at a time; the UI polls status. Serialized => one model load.
        CREATE TABLE IF NOT EXISTS jobs (
            id         INTEGER PRIMARY KEY,
            paper_id   INTEGER REFERENCES papers(id) ON DELETE CASCADE,
            type       TEXT NOT NULL,         -- e.g. 'explore'
            status     TEXT NOT NULL DEFAULT 'queued',  -- queued|running|done|error
            result     TEXT,
            error      TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);

        -- spaced-repetition reviews (#1). One row per paper; reschedules forward.
        CREATE TABLE IF NOT EXISTS reviews (
            paper_id  INTEGER PRIMARY KEY REFERENCES papers(id) ON DELETE CASCADE,
            stage     INTEGER NOT NULL DEFAULT 0,   -- index into the interval ladder
            due_date  TEXT NOT NULL,                -- ISO date
            question  TEXT                          -- cached recall prompt
        );
        CREATE INDEX IF NOT EXISTS idx_reviews_due ON reviews(due_date);

        -- highlights & notes (#13)
        CREATE TABLE IF NOT EXISTS notes (
            id         INTEGER PRIMARY KEY,
            paper_id   INTEGER NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
            text       TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_notes_paper ON notes(paper_id);
        """
    )
    # sqlite-vec virtual table needs the dim baked in; create separately.
    conn.execute(
        f"CREATE VIRTUAL TABLE IF NOT EXISTS vec_papers USING vec0("
        f"paper_id INTEGER PRIMARY KEY, "
        f"embedding FLOAT[{dim}] distance_metric=cosine)"
    )
    # Migrate older DBs: add any missing papers columns (CREATE IF NOT EXISTS
    # never alters an existing table).
    have = {r["name"] for r in conn.execute("PRAGMA table_info(papers)")}
    for col in ("figure_path", "figure_caption", "audio_script", "audio_path"):
        if col not in have:
            conn.execute(f"ALTER TABLE papers ADD COLUMN {col} TEXT")
    have_c = {r["name"] for r in conn.execute("PRAGMA table_info(concepts)")}
    if "definition" not in have_c:
        conn.execute("ALTER TABLE concepts ADD COLUMN definition TEXT")
    conn.commit()


# --------------------------------------------------------------------------- #
# Concepts / prerequisite graph
# --------------------------------------------------------------------------- #
def upsert_concept(conn: sqlite3.Connection, slug: str, name: str) -> int:
    conn.execute(
        "INSERT INTO concepts(slug, name) VALUES(?, ?) "
        "ON CONFLICT(slug) DO UPDATE SET name=excluded.name",
        (slug, name),
    )
    row = conn.execute("SELECT id FROM concepts WHERE slug=?", (slug,)).fetchone()
    return int(row["id"])


def set_paper_concepts(
    conn: sqlite3.Connection,
    paper_id: int,
    provides: Iterable[tuple[str, str]] = (),
    requires: Iterable[tuple[str, str]] = (),
) -> None:
    """Replace a paper's concept edges. Each item is (slug, display_name)."""
    conn.execute("DELETE FROM paper_concepts WHERE paper_id=?", (paper_id,))
    for relation, items in (("provides", provides), ("requires", requires)):
        for slug, name in items:
            cid = upsert_concept(conn, slug, name)
            conn.execute(
                "INSERT OR IGNORE INTO paper_concepts(paper_id, concept_id, relation) "
                "VALUES(?,?,?)",
                (paper_id, cid, relation),
            )
    conn.commit()


# --------------------------------------------------------------------------- #
# Papers
# --------------------------------------------------------------------------- #
def upsert_paper(conn: sqlite3.Connection, **fields: Any) -> int:
    """Insert or update a paper by arxiv_id (or title when arxiv_id is None).

    Returns the paper id. JSON-encodes ``authors`` and ``topics`` if lists.
    """
    for k in ("authors", "topics"):
        if isinstance(fields.get(k), (list, tuple)):
            fields[k] = json.dumps(list(fields[k]))

    arxiv_id = fields.get("arxiv_id")
    existing = None
    if arxiv_id:
        existing = conn.execute(
            "SELECT id FROM papers WHERE arxiv_id=?", (arxiv_id,)
        ).fetchone()
    else:
        existing = conn.execute(
            "SELECT id FROM papers WHERE title=?", (fields.get("title"),)
        ).fetchone()

    if existing:
        pid = int(existing["id"])
        cols = [k for k in fields if k != "id"]
        if cols:
            conn.execute(
                f"UPDATE papers SET {', '.join(f'{c}=?' for c in cols)} WHERE id=?",
                [fields[c] for c in cols] + [pid],
            )
    else:
        cols = list(fields)
        cur = conn.execute(
            f"INSERT INTO papers({', '.join(cols)}) "
            f"VALUES({', '.join('?' for _ in cols)})",
            [fields[c] for c in cols],
        )
        pid = int(cur.lastrowid)

    # keep FTS row in sync
    row = conn.execute(
        "SELECT title, abstract FROM papers WHERE id=?", (pid,)
    ).fetchone()
    conn.execute("DELETE FROM papers_fts WHERE rowid=?", (pid,))
    conn.execute(
        "INSERT INTO papers_fts(rowid, title, abstract) VALUES(?,?,?)",
        (pid, row["title"] or "", row["abstract"] or ""),
    )
    conn.commit()
    return pid


def set_embedding(conn: sqlite3.Connection, paper_id: int, vec: Sequence[float]) -> None:
    conn.execute("DELETE FROM vec_papers WHERE paper_id=?", (paper_id,))
    conn.execute(
        "INSERT INTO vec_papers(paper_id, embedding) VALUES(?, ?)",
        (paper_id, _vec_blob(vec)),
    )
    conn.commit()


def nearest(
    conn: sqlite3.Connection, vec: Sequence[float], k: int = 5
) -> list[tuple[int, float]]:
    """Return [(paper_id, cosine_distance)] for the k nearest embeddings."""
    rows = conn.execute(
        "SELECT paper_id, distance FROM vec_papers "
        "WHERE embedding MATCH ? AND k = ? ORDER BY distance",
        (_vec_blob(vec), k),
    ).fetchall()
    return [(int(r["paper_id"]), float(r["distance"])) for r in rows]


def set_status(conn: sqlite3.Connection, paper_id: int, status: str) -> None:
    if status not in STATUSES:
        raise ValueError(f"unknown status {status!r}")
    conn.execute("UPDATE papers SET status=? WHERE id=?", (status, paper_id))
    conn.commit()


def get_paper(conn: sqlite3.Connection, paper_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM papers WHERE id=?", (paper_id,)).fetchone()


# --------------------------------------------------------------------------- #
# App state (key/value) — e.g. the email pause flag
# --------------------------------------------------------------------------- #
def get_state(conn: sqlite3.Connection, key: str, default: str | None = None) -> str | None:
    row = conn.execute("SELECT value FROM app_state WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def set_state(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO app_state(key, value) VALUES(?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )
    conn.commit()


def emails_paused(conn: sqlite3.Connection) -> bool:
    return get_state(conn, "email_paused", "0") == "1"


# --------------------------------------------------------------------------- #
# Job queue (broker) — producer/consumer for async UI work
# --------------------------------------------------------------------------- #
def enqueue_job(conn: sqlite3.Connection, paper_id: int, job_type: str) -> int:
    cur = conn.execute(
        "INSERT INTO jobs(paper_id, type) VALUES(?, ?)", (paper_id, job_type)
    )
    conn.commit()
    return int(cur.lastrowid)


def claim_next_job(conn: sqlite3.Connection) -> sqlite3.Row | None:
    """Atomically mark the oldest queued job 'running' and return it (or None)."""
    row = conn.execute(
        "UPDATE jobs SET status='running', updated_at=datetime('now') "
        "WHERE id = (SELECT id FROM jobs WHERE status='queued' ORDER BY id LIMIT 1) "
        "RETURNING id, paper_id, type"
    ).fetchone()
    conn.commit()
    return row


def finish_job(conn: sqlite3.Connection, job_id: int, result: str = "ok") -> None:
    conn.execute(
        "UPDATE jobs SET status='done', result=?, updated_at=datetime('now') WHERE id=?",
        (result, job_id),
    )
    conn.commit()


def fail_job(conn: sqlite3.Connection, job_id: int, error: str) -> None:
    conn.execute(
        "UPDATE jobs SET status='error', error=?, updated_at=datetime('now') WHERE id=?",
        (error[:500], job_id),
    )
    conn.commit()


def get_job(conn: sqlite3.Connection, job_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()


# --------------------------------------------------------------------------- #
# Spaced-repetition reviews (#1)
# --------------------------------------------------------------------------- #
REVIEW_INTERVALS = (1, 7, 30, 90, 180)  # days per stage


def schedule_review(
    conn: sqlite3.Connection, paper_id: int, stage: int, question: str | None = None
) -> None:
    import datetime as dt

    stage = max(0, min(stage, len(REVIEW_INTERVALS) - 1))
    due = (dt.date.today() + dt.timedelta(days=REVIEW_INTERVALS[stage])).isoformat()
    if question is not None:
        conn.execute(
            "INSERT INTO reviews(paper_id, stage, due_date, question) VALUES(?,?,?,?) "
            "ON CONFLICT(paper_id) DO UPDATE SET stage=excluded.stage, "
            "due_date=excluded.due_date, question=excluded.question",
            (paper_id, stage, due, question),
        )
    else:
        conn.execute(
            "INSERT INTO reviews(paper_id, stage, due_date) VALUES(?,?,?) "
            "ON CONFLICT(paper_id) DO UPDATE SET stage=excluded.stage, due_date=excluded.due_date",
            (paper_id, stage, due),
        )
    conn.commit()


def due_reviews(conn: sqlite3.Connection, limit: int = 5) -> list[sqlite3.Row]:
    import datetime as dt

    return conn.execute(
        "SELECT r.*, p.title FROM reviews r JOIN papers p ON p.id = r.paper_id "
        "WHERE r.due_date <= ? ORDER BY r.due_date LIMIT ?",
        (dt.date.today().isoformat(), limit),
    ).fetchall()


def get_review(conn: sqlite3.Connection, paper_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM reviews WHERE paper_id=?", (paper_id,)).fetchone()


# --------------------------------------------------------------------------- #
# Notes (#13)
# --------------------------------------------------------------------------- #
def add_note(conn: sqlite3.Connection, paper_id: int, text: str) -> None:
    conn.execute("INSERT INTO notes(paper_id, text) VALUES(?, ?)", (paper_id, text.strip()))
    conn.commit()


def notes_for(conn: sqlite3.Connection, paper_id: int) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM notes WHERE paper_id=? ORDER BY created_at DESC", (paper_id,)
    ).fetchall()


def recent_notes(conn: sqlite3.Connection, since_iso: str) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT n.*, p.title FROM notes n JOIN papers p ON p.id = n.paper_id "
        "WHERE n.created_at >= ? ORDER BY n.created_at DESC",
        (since_iso,),
    ).fetchall()


# --------------------------------------------------------------------------- #
# Concepts (#7)
# --------------------------------------------------------------------------- #
def concept_by_slug(conn: sqlite3.Connection, slug: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM concepts WHERE slug=?", (slug,)).fetchone()


def set_concept_definition(conn: sqlite3.Connection, concept_id: int, definition: str) -> None:
    conn.execute("UPDATE concepts SET definition=? WHERE id=?", (definition, concept_id))
    conn.commit()


def concepts_with_counts(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT c.id, c.slug, c.name, "
        "  SUM(CASE WHEN pc.relation='provides' THEN 1 ELSE 0 END) AS n_provides, "
        "  SUM(CASE WHEN pc.relation='requires' THEN 1 ELSE 0 END) AS n_requires "
        "FROM concepts c LEFT JOIN paper_concepts pc ON pc.concept_id = c.id "
        "GROUP BY c.id ORDER BY c.name"
    ).fetchall()


def papers_for_concept(conn: sqlite3.Connection, concept_id: int, relation: str) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT p.* FROM paper_concepts pc JOIN papers p ON p.id = pc.paper_id "
        "JOIN learning_path lp ON lp.paper_id = p.id "
        "WHERE pc.concept_id=? AND pc.relation=? ORDER BY lp.position",
        (concept_id, relation),
    ).fetchall()
