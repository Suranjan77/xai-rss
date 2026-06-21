"""Offline tests for idigest core logic (no network, no LLM required).

Run: pytest -q
"""

from __future__ import annotations

import pytest

from idigest import config, embeddings, figures, importer, llm, pathing, store

DIM = 384


@pytest.fixture
def conn(tmp_path):
    with store.connect(tmp_path / "t.sqlite3") as c:
        store.init_db(c, dim=DIM)
        yield c


# --------------------------------------------------------------------------- #
# config
# --------------------------------------------------------------------------- #
def test_deep_merge_overrides_and_preserves():
    base = {"a": {"x": 1, "y": 2}, "b": 3}
    over = {"a": {"y": 20}, "c": 4}
    out = config._deep_merge(base, over)
    assert out == {"a": {"x": 1, "y": 20}, "b": 3, "c": 4}


# --------------------------------------------------------------------------- #
# llm JSON parsing
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "raw,expected",
    [
        ('{"score": 7}', {"score": 7}),
        ('```json\n{"score": 7}\n```', {"score": 7}),
        ('Sure, here it is: {"score": 7} done', {"score": 7}),
        ("[1, 2, 3]", [1, 2, 3]),
    ],
)
def test_parse_json_variants(raw, expected):
    assert llm._parse_json(raw) == expected


def test_parse_json_failure_raises():
    with pytest.raises(ValueError):
        llm._parse_json("no json at all")


# --------------------------------------------------------------------------- #
# store
# --------------------------------------------------------------------------- #
def test_paper_upsert_update_and_fts(conn):
    pid = store.upsert_paper(conn, arxiv_id="1", title="LIME explains", abstract="local surrogate")
    assert store.get_paper(conn, pid)["title"] == "LIME explains"
    # update in place (same arxiv_id) keeps one row, refreshes FTS
    pid2 = store.upsert_paper(conn, arxiv_id="1", title="LIME revised", abstract="surrogate model")
    assert pid == pid2
    assert store.get_paper(conn, pid)["title"] == "LIME revised"
    hits = [r["rowid"] for r in conn.execute(
        "SELECT rowid FROM papers_fts WHERE papers_fts MATCH 'surrogate'")]
    assert hits == [pid]
    # stale FTS term gone
    assert conn.execute(
        "SELECT COUNT(*) FROM papers_fts WHERE papers_fts MATCH 'explains'").fetchone()[0] == 0


def test_status_validation_and_pause(conn):
    pid = store.upsert_paper(conn, arxiv_id="1", title="x", abstract="y")
    store.set_status(conn, pid, "interesting")
    assert store.get_paper(conn, pid)["status"] == "interesting"
    with pytest.raises(ValueError):
        store.set_status(conn, pid, "bogus")
    assert store.emails_paused(conn) is False
    store.set_state(conn, "email_paused", "1")
    assert store.emails_paused(conn) is True


def test_vector_nearest_orders_by_similarity(conn):
    p_shap = store.upsert_paper(conn, arxiv_id="s", title="SHAP", abstract="shapley feature attribution")
    p_cnn = store.upsert_paper(conn, arxiv_id="c", title="CNN", abstract="convolutional image classifier")
    vs, vc = embeddings.embed(["shapley feature attribution", "convolutional image classifier"])
    store.set_embedding(conn, p_shap, vs)
    store.set_embedding(conn, p_cnn, vc)
    q = embeddings.embed_one("shapley values for attribution")
    nearest = store.nearest(conn, q, k=2)
    assert nearest[0][0] == p_shap  # SHAP ranked first


# --------------------------------------------------------------------------- #
# pathing / prerequisite graph
# --------------------------------------------------------------------------- #
def test_auto_slot_after_prerequisite(conn):
    a = store.upsert_paper(conn, arxiv_id="a", title="A teaches foo", abstract="")
    b = store.upsert_paper(conn, arxiv_id="b", title="B teaches bar", abstract="")
    store.set_paper_concepts(conn, a, provides=[("foo", "Foo")])
    store.set_paper_concepts(conn, b, provides=[("bar", "Bar")])
    pathing.set_position(conn, a, 10.0)
    pathing.set_position(conn, b, 20.0)
    # new paper requires foo -> must land after A (10), before B (20)
    n = store.upsert_paper(conn, arxiv_id="n", title="needs foo", abstract="")
    store.set_paper_concepts(conn, n, requires=[("foo", "Foo")])
    pos = pathing.auto_slot(conn, n)
    assert 10.0 < pos < 20.0
    order = [r["id"] for r in pathing.ordered_path(conn)]
    assert order.index(a) < order.index(n)


def test_auto_slot_appends_when_no_prereq(conn):
    a = store.upsert_paper(conn, arxiv_id="a", title="A", abstract="")
    pathing.set_position(conn, a, 10.0)
    n = store.upsert_paper(conn, arxiv_id="n", title="N", abstract="")  # no requires
    pos = pathing.auto_slot(conn, n)
    assert pos > 10.0


# --------------------------------------------------------------------------- #
# importer dedup
# --------------------------------------------------------------------------- #
def test_duplicate_detection(conn):
    pid = store.upsert_paper(conn, arxiv_id="x", title="Grad-CAM", abstract="gradient class activation maps")
    store.set_embedding(conn, pid, embeddings.embed_one("Grad-CAM gradient class activation maps"))
    near_dup = embeddings.embed_one("Grad-CAM: gradient-weighted class activation maps")
    assert importer._duplicate_of(conn, near_dup, threshold=0.85) == pid
    unrelated = embeddings.embed_one("a recipe for chocolate cake")
    assert importer._duplicate_of(conn, unrelated, threshold=0.85) is None


# --------------------------------------------------------------------------- #
# figures: non-PDF sources skip cleanly (offline)
# --------------------------------------------------------------------------- #
def test_figure_skips_non_pdf_sources():
    assert figures.extract_figure(None, "https://distill.pub/2020/circuits/zoom-in/", "Zoom In") == (None, None)
    assert figures.extract_figure(None, None, "No URL paper") == (None, None)
