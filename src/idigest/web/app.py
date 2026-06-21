"""Local browse UI: learning path, paper depth, mark read/interesting, import.

Server-rendered (Jinja2), minimal JS. Runs at the configured web port; the email
"Read full depth ->" links point here.
"""

from __future__ import annotations

import base64
import json
import secrets
from pathlib import Path

from fastapi import FastAPI, Form, Request
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    RedirectResponse,
    Response,
)
from fastapi.templating import Jinja2Templates

from .. import generate, importer, mathmd, pathing, store, worker
from ..config import load_config

app = FastAPI(title="idigest")


@app.on_event("startup")
def _start_worker() -> None:
    worker.start_worker()


@app.middleware("http")
async def basic_auth(request: Request, call_next):
    """HTTP Basic auth, enabled when web.auth_user/auth_password are set.

    Off by default (local-only). Must be on before exposing the UI externally.
    """
    web = load_config()["web"]
    user, pw = web.get("auth_user", ""), web.get("auth_password", "")
    if user and pw:
        header = request.headers.get("authorization", "")
        ok = False
        if header.startswith("Basic "):
            try:
                u, _, p = base64.b64decode(header[6:]).decode().partition(":")
                ok = secrets.compare_digest(u, user) and secrets.compare_digest(p, pw)
            except Exception:
                ok = False
        if not ok:
            return Response(
                status_code=401,
                headers={"WWW-Authenticate": 'Basic realm="idigest"'},
            )
    return await call_next(request)
_TEMPLATES = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
# Disable Jinja's LRU template cache (trips a hashing bug under Python 3.14).
_TEMPLATES.env.cache = None


def _conn():
    return store.connect(load_config()["paths"]["db"])


def _authors(raw: str | None) -> list[str]:
    try:
        return json.loads(raw) if raw else []
    except json.JSONDecodeError:
        return []


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    with _conn() as conn:
        papers = pathing.ordered_path(conn)
        paused = store.emails_paused(conn)
        sent = {r["paper_id"] for r in conn.execute("SELECT paper_id FROM email_log")}
    return _TEMPLATES.TemplateResponse(
        request,
        "path.html",
        {"papers": papers, "paused": paused, "sent": sent},
    )


@app.get("/paper/{pid}", response_class=HTMLResponse)
def paper(request: Request, pid: int):
    with _conn() as conn:
        p = store.get_paper(conn, pid)
        if p is None:
            return HTMLResponse("Not found", status_code=404)
        # generate explanations lazily if a user opens a paper before its email
        if not (p["summary_md"] and p["depth_md"]):
            p = generate.ensure_explanations(conn, pid)
        concepts = conn.execute(
            "SELECT c.name, pc.relation FROM paper_concepts pc "
            "JOIN concepts c ON c.id = pc.concept_id WHERE pc.paper_id=? ORDER BY pc.relation",
            (pid,),
        ).fetchall()
    return _TEMPLATES.TemplateResponse(
        request,
        "paper.html",
        {
            "p": p,
            "authors": _authors(p["authors"]),
            "depth_html": mathmd.render_ui(p["depth_md"] or ""),
            "has_figure": bool(p["figure_path"]),
            "has_audio": bool(p["audio_path"]) and p["audio_path"] != "__none__",
            "provides": [c["name"] for c in concepts if c["relation"] == "provides"],
            "requires": [c["name"] for c in concepts if c["relation"] == "requires"],
        },
    )


@app.get("/figure/{pid}")
def figure(pid: int):
    with _conn() as conn:
        p = store.get_paper(conn, pid)
    if p is None or not p["figure_path"] or not Path(p["figure_path"]).exists():
        return HTMLResponse("no figure", status_code=404)
    return FileResponse(p["figure_path"], media_type="image/png")


@app.get("/audio/{pid}.mp3")
def audio(pid: int):
    with _conn() as conn:
        p = store.get_paper(conn, pid)
    ap = p["audio_path"] if p else None
    if not ap or ap == "__none__" or not Path(ap).exists():
        return HTMLResponse("no audio", status_code=404)
    return FileResponse(ap, media_type="audio/mpeg")


@app.post("/paper/{pid}/status")
def set_status(pid: int, status: str = Form(...)):
    with _conn() as conn:
        store.set_status(conn, pid, status)
    return RedirectResponse(f"/paper/{pid}", status_code=303)


@app.post("/paper/{pid}/explore")
def explore(pid: int):
    """Enqueue a deeper-dive job (async) and return its id; the UI polls /jobs."""
    with _conn() as conn:
        if store.get_paper(conn, pid) is None:
            return JSONResponse({"error": "not found"}, status_code=404)
        job_id = store.enqueue_job(conn, pid, "explore")
    return JSONResponse({"job_id": job_id, "status": "queued"})


@app.get("/jobs/{job_id}")
def job_status(job_id: int):
    """Poll a job's status: queued | running | done | error."""
    with _conn() as conn:
        job = store.get_job(conn, job_id)
    if job is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    return JSONResponse(
        {"job_id": job_id, "status": job["status"], "error": job["error"]}
    )


@app.post("/emails/{action}")
def emails(action: str):
    with _conn() as conn:
        store.set_state(conn, "email_paused", "1" if action == "pause" else "0")
    return RedirectResponse("/", status_code=303)


@app.post("/import/search")
def import_search(query: str = Form(...), limit: int = Form(3)):
    importer.add_from_search(query, limit=limit)
    return RedirectResponse("/", status_code=303)


@app.post("/import/pdf")
def import_pdf(source: str = Form(...)):
    importer.add_from_pdf(source)
    return RedirectResponse("/", status_code=303)
