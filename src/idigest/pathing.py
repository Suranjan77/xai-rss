"""Learning-path ordering: the prerequisite graph and auto-slotting.

Seed papers carry explicit positions. Ingested papers are auto-slotted: placed
just after the latest paper that teaches a concept they require. Only concepts
that *some paper provides* gate ordering; concepts no paper provides (e.g.
'neural-networks') are assumed background and ignored for slotting.
"""

from __future__ import annotations

import sqlite3

POSITION_STEP = 10.0  # gap between seed positions, leaving room to insert


def ordered_path(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """All papers on the path, earliest-to-learn first."""
    return conn.execute(
        "SELECT p.*, lp.position FROM learning_path lp "
        "JOIN papers p ON p.id = lp.paper_id ORDER BY lp.position"
    ).fetchall()


def set_position(conn: sqlite3.Connection, paper_id: int, position: float) -> None:
    conn.execute(
        "INSERT INTO learning_path(paper_id, position) VALUES(?, ?) "
        "ON CONFLICT(paper_id) DO UPDATE SET position=excluded.position",
        (paper_id, position),
    )
    conn.commit()


def _provider_positions_for_requirements(
    conn: sqlite3.Connection, paper_id: int
) -> list[float]:
    """Positions of papers that PROVIDE a concept this paper REQUIRES."""
    rows = conn.execute(
        """
        SELECT lp.position
        FROM paper_concepts req
        JOIN paper_concepts prov
          ON prov.concept_id = req.concept_id AND prov.relation = 'provides'
        JOIN learning_path lp ON lp.paper_id = prov.paper_id
        WHERE req.paper_id = ? AND req.relation = 'requires'
          AND prov.paper_id != ?
        """,
        (paper_id, paper_id),
    ).fetchall()
    return [float(r["position"]) for r in rows]


def auto_slot(conn: sqlite3.Connection, paper_id: int) -> float:
    """Place a paper on the path after its latest satisfied prerequisite.

    If none of its requirements are provided by an existing paper, append to the
    end. Returns the assigned position.
    """
    provider_positions = _provider_positions_for_requirements(conn, paper_id)
    end = conn.execute("SELECT COALESCE(MAX(position), 0) FROM learning_path").fetchone()[0]
    end = float(end)

    if provider_positions:
        after = max(provider_positions)
        # next position currently on the path strictly after `after`
        nxt = conn.execute(
            "SELECT MIN(position) FROM learning_path WHERE position > ?", (after,)
        ).fetchone()[0]
        position = (after + float(nxt)) / 2.0 if nxt is not None else after + POSITION_STEP
    else:
        position = end + POSITION_STEP

    set_position(conn, paper_id, position)
    return position
