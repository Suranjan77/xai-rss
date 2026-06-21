"""LLM content generation shared by the email and ingestion pipelines.

All generation goes through the single running llama-server (one model loaded at
a time). Functions are deliberately small and return plain dicts.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from . import llm, store

# Directive shared rules. Small models are capable but need explicit, specific
# instructions about role, faithfulness, math formatting, and output shape.
_MATH_RULE = (
    "Write ALL mathematics in LaTeX: inline as $...$ and display equations as "
    "$$...$$. Use it for every symbol, variable, or formula (e.g. $x_i$, "
    r"$\nabla_x f$, $a \neq b$, $\sum_i w_i$). Never write math as plain text or "
    "Unicode symbols (no ≠, Σ, ∇, subscripts-as-text)."
)
_FIDELITY_RULE = (
    "Be strictly faithful to the paper. Never invent results, numbers, or claims. "
    "If the abstract is thin, stay general rather than fabricating specifics."
)
_JSON_RULE = (
    "Output a SINGLE valid JSON object and nothing else: no markdown code fences, "
    "no commentary before or after, no trailing text. Use straight double quotes."
)

_EXPLAIN_SYS = (
    "You are a research tutor who specialises in machine-learning interpretability "
    "(XAI, mechanistic interpretability, model transparency). You make hard papers "
    "genuinely easy to grasp for a busy researcher WITHOUT sacrificing accuracy: "
    "plain language, vivid concrete analogies, and one clear idea at a time. "
    "You define any unavoidable jargon in-line. " + _FIDELITY_RULE
)

_EXPLAIN_USER = """Explain this interpretability paper. The summary/intuition/key_insight
go in a short email and MUST be free of notation; the depth is the technical section.

Title: {title}
Authors: {authors}
Abstract: {abstract}

Return ONLY a JSON object with exactly these string fields:
- "summary": 2-3 sentences, plain language, the gist. NO equations or LaTeX — words only.
- "intuition": 4-6 sentences that build intuition with ONE concrete analogy or worked
  example. Describe any math in words; avoid notation here.
- "key_insight": ONE punchy sentence — the single most important takeaway. No notation.
- "depth": a markdown technical explanation with these exact headings, each followed by
  1-2 short paragraphs or bullets:
    ## The problem
    ## How it works
    ## Why it matters
    ## Caveats & limitations
  In "depth", {math_rule} Put the central equation(s) as display math.

{json_rule}""".format(title="{title}", authors="{authors}", abstract="{abstract}",
                      math_rule=_MATH_RULE, json_rule=_JSON_RULE)


def write_explanations(paper: sqlite3.Row) -> dict[str, str]:
    """Generate summary / intuition / key_insight / depth for one paper."""
    authors = paper["authors"] or "[]"
    msg = [
        {"role": "system", "content": _EXPLAIN_SYS},
        {
            "role": "user",
            "content": _EXPLAIN_USER.format(
                title=paper["title"],
                authors=authors,
                abstract=paper["abstract"] or "(abstract unavailable)",
            ),
        },
    ]
    # generous budget: Gemma-4 always "thinks" first, then emits the JSON answer
    data = llm.chat_json(msg, temperature=0.4, max_tokens=3072)
    return {
        "summary_md": str(data.get("summary", "")).strip(),
        "intuition_md": str(data.get("intuition", "")).strip(),
        "key_insight": str(data.get("key_insight", "")).strip(),
        "depth_md": str(data.get("depth", "")).strip(),
    }


def ensure_explanations(conn: sqlite3.Connection, paper_id: int) -> sqlite3.Row:
    """Generate and persist explanations + a key figure if missing; return the row."""
    paper = store.get_paper(conn, paper_id)
    if not (paper["summary_md"] and paper["depth_md"]):
        fields = write_explanations(paper)
        store.upsert_paper(conn, arxiv_id=paper["arxiv_id"], title=paper["title"], **fields)
        paper = store.get_paper(conn, paper_id)
    ensure_figure(conn, paper_id)
    return store.get_paper(conn, paper_id)


def ensure_figure(conn: sqlite3.Connection, paper_id: int) -> None:
    """Extract and persist a key figure for the paper if not already attempted."""
    from . import figures

    paper = store.get_paper(conn, paper_id)
    if paper["figure_path"] or paper["figure_caption"] == "__none__":
        return  # already have one, or already tried and found nothing
    path, caption = figures.extract_figure(
        paper["arxiv_id"], paper["pdf_url"], paper["title"]
    )
    store.upsert_paper(
        conn,
        arxiv_id=paper["arxiv_id"],
        title=paper["title"],
        figure_path=path,
        figure_caption=caption if path else "__none__",
    )


def explore_deeper(paper: sqlite3.Row) -> str:
    """On-demand deeper technical dive for the UI 'Explore deeper' button."""
    msg = [
        {"role": "system", "content": _EXPLAIN_SYS},
        {
            "role": "user",
            "content": f"Title: {paper['title']}\nAbstract: {paper['abstract']}\n\n"
            "The reader has already read the summary. Write a DEEPER, more technical "
            "markdown explanation that does not repeat the basics. Cover: the precise "
            "mechanism/derivation (show the key equations, not just describe them), the "
            "experimental setup, and how this relates to neighbouring methods. Use "
            "markdown subheadings. " + _MATH_RULE,
        },
    ]
    return llm.chat(msg, temperature=0.4, max_tokens=2048).strip()


def relevance_score(title: str, abstract: str) -> int:
    """0-10: how central is this paper to interpretability/explainability/transparency?"""
    msg = [
        {
            "role": "system",
            "content": "You are a strict relevance grader for a reading list focused on "
            "ML interpretability, explainability (XAI), mechanistic interpretability, and "
            "model transparency. You judge ONLY topical relevance, not paper quality.",
        },
        {
            "role": "user",
            "content": f"Title: {title}\nAbstract: {abstract}\n\n"
            "Score topical relevance on this rubric:\n"
            "  9-10 = the paper's primary contribution IS an interpretability/"
            "explainability/transparency method or analysis.\n"
            "  6-8  = interpretability is a substantial component or evaluation.\n"
            "  3-5  = tangentially related (mentions interpretability in passing).\n"
            "  0-2  = unrelated.\n"
            'Return ONLY JSON: {"score": <integer 0-10>}. ' + _JSON_RULE,
        },
    ]
    try:
        data = llm.chat_json(msg, temperature=0.0, max_tokens=512)
        return int(data.get("score", 0))
    except (ValueError, KeyError, TypeError):
        return 0


def summarize_and_extract(title: str, abstract: str) -> dict[str, Any]:
    """For ingestion: short summary + difficulty + concept edges for the graph."""
    msg = [
        {
            "role": "system",
            "content": "You are an expert who maps ML interpretability papers into a "
            "prerequisite knowledge graph so learners can be taught in the right order. "
            + _FIDELITY_RULE,
        },
        {
            "role": "user",
            "content": f"""Title: {title}
Abstract: {abstract}

Return ONLY a JSON object with:
- "summary": 2-3 sentence plain-language gist (no notation).
- "difficulty": integer 1 (gentle intro) .. 5 (requires deep background).
- "topics": array of 1-3 short lowercase topic tags.
- "provides": array of [slug, name] — the concepts THIS paper teaches.
- "requires": array of [slug, name] — concepts a reader must ALREADY know first.

Concept rules: slugs are lowercase-hyphenated, specific but reusable across papers
(e.g. "shapley-values", "attention-mechanism", "sparse-autoencoders"). Prefer
established names over inventing new ones. List 1-4 items per side; do not list a
concept as both provided and required. Assume general ML/deep-learning background
is already known — only list interpretability-relevant prerequisites.

{_JSON_RULE}""",
        },
    ]
    data = llm.chat_json(msg, temperature=0.2, max_tokens=1536)
    return {
        "summary": str(data.get("summary", "")).strip(),
        "difficulty": int(data.get("difficulty", 3)),
        "topics": list(data.get("topics", [])),
        "provides": [tuple(x) for x in data.get("provides", []) if len(x) == 2],
        "requires": [tuple(x) for x in data.get("requires", []) if len(x) == 2],
    }
