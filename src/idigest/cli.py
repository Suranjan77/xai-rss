"""idigest command-line entry point.

Subcommands:
  init-db        create the SQLite schema
  load-seed      load curated seed papers + ordered learning path
  ingest         fetch/dedup/summarize/slot new papers
  email          generate and send today's byte-size email (--dry-run to print)
  serve-web      run the local browse UI
  path           print the current learning path (debug)
"""

from __future__ import annotations

import argparse
import sys

from .config import load_config
from . import store


def cmd_init_db(args: argparse.Namespace) -> int:
    cfg = load_config()
    with store.connect(cfg["paths"]["db"]) as conn:
        store.init_db(conn, dim=cfg["embeddings"]["dim"])
    print(f"initialized db at {cfg['paths']['db']}")
    return 0


def cmd_load_seed(args: argparse.Namespace) -> int:
    from . import seed

    n = seed.load_seed()
    print(f"loaded/updated {n} seed papers")
    return 0


def cmd_ingest(args: argparse.Namespace) -> int:
    from . import ingest

    n = ingest.run()
    print(f"ingested {n} new papers")
    return 0


def cmd_email(args: argparse.Namespace) -> int:
    from . import email_send

    return email_send.run(dry_run=args.dry_run)


def cmd_email_pause(args: argparse.Namespace) -> int:
    cfg = load_config()
    with store.connect(cfg["paths"]["db"]) as conn:
        store.set_state(conn, "email_paused", "1")
    print("daily emails paused")
    return 0


def cmd_email_resume(args: argparse.Namespace) -> int:
    cfg = load_config()
    with store.connect(cfg["paths"]["db"]) as conn:
        store.set_state(conn, "email_paused", "0")
    print("daily emails resumed")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    from . import email_send

    cfg = load_config()
    with store.connect(cfg["paths"]["db"]) as conn:
        total = conn.execute("SELECT COUNT(*) FROM learning_path").fetchone()[0]
        sent = conn.execute("SELECT COUNT(*) FROM email_log").fetchone()[0]
        paused = store.emails_paused(conn)
        nxt = email_send.pick_next(conn)
    print(f"emails:      {'PAUSED' if paused else 'active'}")
    print(f"path length: {total} papers")
    print(f"emailed:     {sent}")
    print(f"next up:     {nxt['title'] if nxt else '(none left)'}")
    return 0


def cmd_add_search(args: argparse.Namespace) -> int:
    from . import importer

    n = importer.add_from_search(args.query, limit=args.limit, auto=args.yes)
    print(f"imported {n} papers")
    return 0


def cmd_add_pdf(args: argparse.Namespace) -> int:
    from . import importer

    pid = importer.add_from_pdf(args.source)
    print(f"imported paper id {pid}")
    return 0


def cmd_add_citations(args: argparse.Namespace) -> int:
    from . import importer

    n = importer.add_from_citations(args.paper_id, limit=args.limit, direction=args.direction)
    print(f"imported {n} papers")
    return 0


def cmd_digest(args: argparse.Namespace) -> int:
    from . import digest

    return digest.run(dry_run=args.dry_run)


def cmd_serve_web(args: argparse.Namespace) -> int:
    import uvicorn

    cfg = load_config()["web"]
    uvicorn.run("idigest.web.app:app", host=cfg["host"], port=cfg["port"])
    return 0


def cmd_path(args: argparse.Namespace) -> int:
    from . import pathing

    cfg = load_config()
    with store.connect(cfg["paths"]["db"]) as conn:
        for i, row in enumerate(pathing.ordered_path(conn), 1):
            print(f"{i:3d}. [{row['status']:<11}] {row['title']}")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="idigest")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init-db").set_defaults(func=cmd_init_db)
    sub.add_parser("load-seed").set_defaults(func=cmd_load_seed)
    sub.add_parser("ingest").set_defaults(func=cmd_ingest)

    pe = sub.add_parser("email")
    pe.add_argument("--dry-run", action="store_true", help="print instead of sending")
    pe.set_defaults(func=cmd_email)

    sub.add_parser("email-pause").set_defaults(func=cmd_email_pause)
    sub.add_parser("email-resume").set_defaults(func=cmd_email_resume)
    sub.add_parser("status").set_defaults(func=cmd_status)

    ps = sub.add_parser("add-search", help="search the web for research and import")
    ps.add_argument("query")
    ps.add_argument("--limit", type=int, default=5)
    ps.add_argument("--yes", action="store_true", help="import all hits without prompting")
    ps.set_defaults(func=cmd_add_search)

    pp = sub.add_parser("add-pdf", help="import a paper from a PDF path or URL")
    pp.add_argument("source")
    pp.set_defaults(func=cmd_add_pdf)

    pc = sub.add_parser("add-citations", help="import a paper's references/citations")
    pc.add_argument("paper_id", type=int)
    pc.add_argument("--limit", type=int, default=5)
    pc.add_argument("--direction", choices=["references", "citations"], default="references")
    pc.set_defaults(func=cmd_add_citations)

    pd = sub.add_parser("digest", help="send the weekly digest email")
    pd.add_argument("--dry-run", action="store_true")
    pd.set_defaults(func=cmd_digest)

    sub.add_parser("serve-web").set_defaults(func=cmd_serve_web)
    sub.add_parser("path").set_defaults(func=cmd_path)

    args = p.parse_args(argv)
    try:
        return args.func(args)
    except (RuntimeError, ValueError, FileNotFoundError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
