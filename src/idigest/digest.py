"""Weekly digest email (#3): a Sunday summary of the week's learning.

Read this week, what's next on the path, your "interesting" shortlist, notable new
arrivals, and recent notes — to give momentum and a sense of a curriculum.
"""

from __future__ import annotations

import datetime as dt

from . import email_send, pathing, store
from .config import load_config


def _section(title: str, items: list[str]) -> str:
    if not items:
        return ""
    lis = "".join(f"<li>{i}</li>" for i in items)
    return f'<h3 style="margin:18px 0 4px;font-size:15px">{title}</h3><ul>{lis}</ul>'


def build(conn) -> tuple[str, str, str]:
    cfg = load_config()
    ui = cfg["email"]["ui_base_url"].rstrip("/")
    week_ago = (dt.date.today() - dt.timedelta(days=7)).isoformat()

    read = conn.execute(
        "SELECT p.id, p.title FROM email_log e JOIN papers p ON p.id=e.paper_id "
        "WHERE e.sent_date >= ? ORDER BY e.sent_date", (week_ago,)
    ).fetchall()
    interesting = conn.execute(
        "SELECT id, title FROM papers WHERE status='interesting' ORDER BY id DESC LIMIT 8"
    ).fetchall()
    new_arrivals = conn.execute(
        "SELECT id, title FROM papers WHERE source!='seed' AND created_at >= ? "
        "ORDER BY created_at DESC LIMIT 8", (week_ago,)
    ).fetchall()
    notes = store.recent_notes(conn, week_ago)

    upcoming = []
    for p in pathing.ordered_path(conn):
        if not conn.execute("SELECT 1 FROM email_log WHERE paper_id=?", (p["id"],)).fetchone():
            upcoming.append(p)
        if len(upcoming) >= 3:
            break

    def link(pid, title):
        return f'<a href="{ui}/paper/{pid}">{title}</a>'

    body = (
        f'<div style="max-width:620px;margin:0 auto;font-family:-apple-system,Segoe UI,'
        f'Roboto,sans-serif;color:#1a1a1a;line-height:1.55">'
        f"<h2>Your week in interpretability</h2>"
        + _section("📬 Studied this week", [link(p["id"], p["title"]) for p in read])
        + _section("⏭️ Up next", [link(p["id"], p["title"]) for p in upcoming])
        + _section("⭐ Your interesting shortlist", [link(p["id"], p["title"]) for p in interesting])
        + _section("🆕 Notable new arrivals", [link(p["id"], p["title"]) for p in new_arrivals])
        + _section("📝 Recent notes", [f'{n["text"]} <em>— {n["title"]}</em>' for n in notes])
        + f'<p style="margin-top:20px"><a href="{ui}/" '
          f'style="color:#1a73e8">Open idigest →</a></p></div>'
    )
    text = "Your week in interpretability — open the UI for links: " + ui
    subject = "🗓️ Your week in interpretability"
    return subject, text, body


def run(dry_run: bool = False) -> int:
    cfg = load_config()
    with store.connect(cfg["paths"]["db"]) as conn:
        subject, text, html = build(conn)
    if dry_run:
        print(f"--- SUBJECT ---\n{subject}\n\n--- HTML ({len(html)} chars) ---\n{html[:600]}")
        return 0
    msg = email_send.build_message(subject, text, html)
    email_send._send_smtp(msg)
    print(f"sent weekly digest -> {cfg['email']['to']}")
    return 0
