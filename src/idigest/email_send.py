"""Daily byte-size email: pick the next paper to learn, generate prose, send.

Selection = lowest learning-path position among papers not yet emailed. The email
stays short (summary + intuition + one key insight) and links to the local UI for
full depth. Sending uses Gmail SMTP with an app password from config.local.toml.
"""

from __future__ import annotations

import datetime as dt
import smtplib
import sqlite3
from email.message import EmailMessage

from html import escape

from . import generate, llm, mathmd, store
from .config import load_config


def pick_next(conn: sqlite3.Connection) -> sqlite3.Row | None:
    """Next paper on the path that has never been emailed (by learning order)."""
    return conn.execute(
        "SELECT p.* FROM learning_path lp "
        "JOIN papers p ON p.id = lp.paper_id "
        "LEFT JOIN email_log e ON e.paper_id = p.id "
        "WHERE e.paper_id IS NULL "
        "ORDER BY lp.position LIMIT 1"
    ).fetchone()


def _render(paper: sqlite3.Row) -> tuple[str, str, str]:
    """Return (subject, text_body, html_body). HTML may reference cid:figure."""
    cfg = load_config()
    ui = cfg["email"]["ui_base_url"].rstrip("/")
    depth_url = f"{ui}/paper/{paper['id']}"
    audio_url = f"{ui}/audio/{paper['id']}.mp3"
    subject = f"📄 {paper['title']}"

    has_fig = bool(paper["figure_path"])
    fig_caption = paper["figure_caption"] if has_fig else ""
    has_audio = bool(paper["audio_path"]) and paper["audio_path"] != "__none__"
    # HTML-escape model/paper text so a stray '<' or '&' can't break the markup
    e_title = escape(paper["title"])
    e_summary = escape(paper["summary_md"] or "")
    e_intuition = escape(paper["intuition_md"] or "")
    e_insight = escape(paper["key_insight"] or "")
    e_caption = escape(fig_caption or "")

    full_depth = cfg["email"].get("full_depth", True)

    text = f"""{paper['title']}

{paper['summary_md']}

WHY IT CLICKS
{paper['intuition_md']}

KEY INSIGHT
{paper['key_insight']}
"""
    if has_audio:
        text += f"\n🎧 Listen (streams from your machine): {audio_url}\n"
    if has_fig:
        text += f"\nFIGURE: {fig_caption}\n"
    if full_depth and paper["depth_md"]:
        text += f"\nDEPTH\n{paper['depth_md']}\n"
    text += f"\nOpen in browser → {depth_url}\nPaper PDF → {paper['pdf_url'] or '(n/a)'}\n"

    fig_html = ""
    if has_fig:
        fig_html = f"""
  <figure style="margin:20px 0;text-align:center">
    <img src="cid:figure" alt="key figure" style="max-width:100%;border:1px solid #eee;border-radius:6px"/>
    <figcaption style="color:#666;font-size:13px;margin-top:6px">{e_caption}</figcaption>
  </figure>"""

    depth_html = ""
    math_images: list[tuple[str, bytes]] = []
    if full_depth and paper["depth_md"]:
        rendered, math_images = mathmd.render_email_depth(paper["depth_md"])
        depth_html = (
            '\n  <h3 style="margin:22px 0 4px;font-size:14px;text-transform:uppercase;'
            'letter-spacing:.5px;color:#555">Depth</h3>\n  <div style="font-size:15px">'
            + rendered
            + "</div>"
        )

    html = f"""\
<div style="max-width:620px;margin:0 auto;font-family:-apple-system,Segoe UI,Roboto,sans-serif;color:#1a1a1a;line-height:1.55">
  <h2 style="margin:0 0 4px">{e_title}</h2>
  <p style="color:#666;margin:0 0 16px;font-size:13px">{paper['published'] or ''} · difficulty {paper['difficulty'] or '?'}/5</p>
  {f'<p style="margin:6px 0"><a href="{audio_url}" style="display:inline-block;background:#1e7e34;color:#fff;text-decoration:none;border-radius:6px;padding:10px 16px;font-size:15px">🎧 Listen — stream it on your walk</a></p>' if has_audio else ''}
  <p style="font-size:16px">{e_summary}</p>{fig_html}
  <h3 style="margin:20px 0 4px;font-size:14px;text-transform:uppercase;letter-spacing:.5px;color:#555">Why it clicks</h3>
  <p>{e_intuition}</p>
  <blockquote style="margin:16px 0;padding:10px 16px;background:#f4f6f8;border-left:3px solid #4a7;font-style:italic">{e_insight}</blockquote>{depth_html}
  <p style="margin-top:24px">
    <a href="{depth_url}" style="display:inline-block;padding:10px 18px;background:#1a73e8;color:#fff;text-decoration:none;border-radius:6px">Open in browser →</a>
    &nbsp;<a href="{paper['pdf_url'] or '#'}" style="color:#1a73e8">Paper PDF</a>
  </p>
</div>"""
    return subject, text, html, math_images


def build_message(
    subject: str,
    text: str,
    html: str,
    figure_path: str | None = None,
    math_images: list[tuple[str, bytes]] | None = None,
) -> EmailMessage:
    """Assemble the email: inline figure + math images. Audio is streamed from the
    server via a link, not attached."""
    from pathlib import Path

    cfg = load_config()["email"]
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = cfg["from"]
    msg["To"] = cfg["to"]
    msg.set_content(text)
    msg.add_alternative(html, subtype="html")
    html_part = msg.get_payload()[1]
    if figure_path:
        html_part.add_related(
            Path(figure_path).read_bytes(), maintype="image", subtype="png", cid="figure"
        )
    for cid, png in math_images or []:
        html_part.add_related(png, maintype="image", subtype="png", cid=cid)
    return msg


def _send_smtp(msg: EmailMessage) -> None:
    cfg = load_config()["email"]
    user = cfg.get("smtp_user")
    password = cfg.get("smtp_password")
    if not user or not password:
        raise RuntimeError(
            "smtp_user/smtp_password not set. Add them to config.local.toml "
            "(use a Gmail App Password, not your account password)."
        )
    with smtplib.SMTP(cfg["smtp_host"], cfg["smtp_port"]) as s:
        s.starttls()
        s.login(user, password)
        s.send_message(msg)


def run(dry_run: bool = False) -> int:
    cfg = load_config()
    if not cfg["email"].get("enabled", True) and not dry_run:
        print("email disabled in config")
        return 0
    with store.connect(cfg["paths"]["db"]) as conn:
        if store.emails_paused(conn) and not dry_run:
            print("emails are paused (resume with: idigest email-resume)")
            return 0
        paper = pick_next(conn)
        if paper is None:
            print("no unsent papers left on the learning path")
            return 0
        if not llm.is_up():
            print("llama-server is not running; start scripts/serve_llm.sh first")
            return 1
        paper = generate.ensure_explanations(conn, paper["id"])
        try:
            generate.ensure_audio(conn, paper["id"])  # nice-to-have; never block send
        except Exception as e:
            print(f"audio generation failed (sending without audio): {e}")
        paper = store.get_paper(conn, paper["id"])
        subject, text, html, math_images = _render(paper)
        msg = build_message(
            subject, text, html, figure_path=paper["figure_path"],
            math_images=math_images,
        )
        if dry_run:
            fig = paper["figure_path"] or "(none)"
            print(f"--- SUBJECT ---\n{subject}\n\n--- FIGURE ---\n{fig}\n"
                  f"caption: {paper['figure_caption']}\n"
                  f"--- MATH IMAGES ---\n{len(math_images)} rendered\n\n--- TEXT BODY ---\n{text}")
            return 0
        _send_smtp(msg)
        today = dt.date.today().isoformat()
        conn.execute(
            "INSERT OR IGNORE INTO email_log(paper_id, sent_date) VALUES(?, ?)",
            (paper["id"], today),
        )
        store.set_status(conn, paper["id"], "queued")
        conn.commit()
        print(f"sent: {paper['title']}  ->  {cfg['email']['to']}")
    return 0
