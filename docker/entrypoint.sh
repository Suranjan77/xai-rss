#!/usr/bin/env bash
# Initialize the DB + seed on first run, then run the given command.
set -e

mkdir -p "$(dirname "${IDIGEST_DB:-/app/data/db/idigest.sqlite3}")" \
         /app/.cache/fastembed /app/.cache/hf

# Seed only when the database doesn't exist yet.
if [ ! -f "${IDIGEST_DB:-/app/data/db/idigest.sqlite3}" ]; then
  echo ">> first run: initializing database + loading seed corpus"
  idigest init-db
  idigest load-seed || echo ">> seed load skipped/failed (continuing)"
else
  idigest init-db   # idempotent migration
fi

exec "$@"
