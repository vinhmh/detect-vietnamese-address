#!/usr/bin/env bash
# run_all.sh — full dvhcvn ingestion pipeline in one command
# Idempotent: safe to re-run after upstream data updates.
#
# Usage:
#   bash etl/run_all.sh [--force-fetch]
#
# Options:
#   --force-fetch   Skip ETag check and re-download data files

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"
FORCE_FETCH=""

for arg in "$@"; do
  case $arg in
    --force-fetch) FORCE_FETCH="--force" ;;
  esac
done

echo "========================================"
echo " dvhcvn Ingestion Pipeline"
echo "========================================"
echo ""

# ── 0. Load env ───────────────────────────────────────────────────────────────
if [ -f "$ROOT_DIR/.env" ]; then
  set -a
  # shellcheck disable=SC1091
  source "$ROOT_DIR/.env"
  set +a
fi

POSTGRES_USER="${POSTGRES_USER:-postgres}"
POSTGRES_PASSWORD="${POSTGRES_PASSWORD:-postgres}"
POSTGRES_DB="${POSTGRES_DB:-vn_address}"
POSTGRES_HOST="${POSTGRES_HOST:-localhost}"
POSTGRES_PORT="${POSTGRES_PORT:-5432}"
CONTAINER_NAME="${CONTAINER_NAME:-vn_address_pg}"
DATABASE_URL="${DATABASE_URL:-postgresql://${POSTGRES_USER}:${POSTGRES_PASSWORD}@${POSTGRES_HOST}:${POSTGRES_PORT}/${POSTGRES_DB}}"
export DATABASE_URL

# ── Helper: run SQL via docker exec (no local psql needed) ────────────────────
pg_exec() {
  docker exec -i "$CONTAINER_NAME" \
    psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -q "$@"
}

pg_exec_file() {
  docker exec -i "$CONTAINER_NAME" \
    psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -q < "$1"
}

# ── 1. Wait for PostgreSQL ────────────────────────────────────────────────────
echo "Step 1/5 — Waiting for PostgreSQL..."
RETRIES=20
until docker exec "$CONTAINER_NAME" pg_isready -U "$POSTGRES_USER" -d "$POSTGRES_DB" -q 2>/dev/null; do
  RETRIES=$((RETRIES - 1))
  if [ $RETRIES -le 0 ]; then
    echo "ERROR: PostgreSQL container '$CONTAINER_NAME' not reachable after 20 attempts"
    echo "  Make sure the container is running: docker compose up -d"
    exit 1
  fi
  echo "  PostgreSQL not ready, retrying in 2s..."
  sleep 2
done
echo "  PostgreSQL is up."
echo ""

# ── 2. Run migrations ─────────────────────────────────────────────────────────
echo "Step 2/5 — Running migrations..."
pg_exec_file "$ROOT_DIR/migrations/001_extensions.sql"
echo "  001_extensions.sql OK"
pg_exec_file "$ROOT_DIR/migrations/002_schema.sql"
echo "  002_schema.sql OK"
pg_exec_file "$ROOT_DIR/migrations/003_indexes.sql"
echo "  003_indexes.sql OK"
echo ""

# ── Resolve Python interpreter (prefer venv) ──────────────────────────────────
PYTHON="${ROOT_DIR}/.venv/bin/python"
if [ ! -f "$PYTHON" ]; then
  PYTHON="$(which python3 || which python)"
fi

# ── 3. Fetch data ─────────────────────────────────────────────────────────────
echo "Step 3/5 — Fetching dvhcvn data..."
cd "$SCRIPT_DIR"
"$PYTHON" fetch_dvhcvn.py $FORCE_FETCH
echo ""

# ── 4. Load to PostgreSQL ─────────────────────────────────────────────────────
echo "Step 4/5 — Loading to PostgreSQL..."
"$PYTHON" load_to_pg.py
echo ""

# ── 5. Seed aliases ───────────────────────────────────────────────────────────
echo "Step 5/5 — Seeding aliases..."
"$PYTHON" seed_aliases.py
echo ""

echo "========================================"
echo " Pipeline complete!"
echo "========================================"
echo ""
echo "Quick smoke test:"
echo "  psql \$DATABASE_URL -c \"SELECT unit_type, count(*) FROM admin_units GROUP BY 1\""
echo "  psql \$DATABASE_URL -c \"SELECT similarity('ho chi minh', ascii_name), full_name FROM admin_units WHERE unit_type='province' ORDER BY 1 DESC LIMIT 3\""
