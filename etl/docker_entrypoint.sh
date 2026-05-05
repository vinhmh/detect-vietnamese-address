#!/usr/bin/env bash
# docker_entrypoint.sh — runs inside the ETL container
# Idempotent: safe to re-run after upstream data updates.
#
# Differences from run_all.sh (host script):
#   - Uses psql via DATABASE_URL directly (no docker exec needed)
#   - Connects to postgres service by hostname ("postgres" / POSTGRES_HOST)
#   - Runs migration 004 in addition to 001-003

set -euo pipefail

POSTGRES_USER="${POSTGRES_USER:-postgres}"
POSTGRES_PASSWORD="${POSTGRES_PASSWORD:-postgres}"
POSTGRES_DB="${POSTGRES_DB:-vn_address}"
POSTGRES_HOST="${POSTGRES_HOST:-postgres}"
POSTGRES_PORT="${POSTGRES_PORT:-5432}"
export DATABASE_URL="${DATABASE_URL:-postgresql://${POSTGRES_USER}:${POSTGRES_PASSWORD}@${POSTGRES_HOST}:${POSTGRES_PORT}/${POSTGRES_DB}}"
export PGPASSWORD="$POSTGRES_PASSWORD"

PSQL="psql -h $POSTGRES_HOST -p $POSTGRES_PORT -U $POSTGRES_USER -d $POSTGRES_DB -q"

echo "========================================"
echo " Vietnamese Address ETL Pipeline"
echo "========================================"

# ── 1. Wait for PostgreSQL ────────────────────────────────────────────────────
echo ""
echo "Step 1/6 — Waiting for PostgreSQL at $POSTGRES_HOST:$POSTGRES_PORT ..."
RETRIES=30
until pg_isready -h "$POSTGRES_HOST" -p "$POSTGRES_PORT" -U "$POSTGRES_USER" -q; do
  RETRIES=$((RETRIES - 1))
  if [ $RETRIES -le 0 ]; then
    echo "ERROR: PostgreSQL not reachable after 30 attempts"
    exit 1
  fi
  echo "  Not ready, retrying in 2s..."
  sleep 2
done
echo "  PostgreSQL is up."

# ── 2. Run migrations ─────────────────────────────────────────────────────────
echo ""
echo "Step 2/6 — Running migrations..."
$PSQL < /app/migrations/001_extensions.sql && echo "  001 OK"
$PSQL < /app/migrations/002_schema.sql     && echo "  002 OK"
$PSQL < /app/migrations/003_indexes.sql    && echo "  003 OK"
$PSQL < /app/migrations/004_post_merger_schema.sql && echo "  004 OK"

# ── 3. Fetch pre-merger data ──────────────────────────────────────────────────
echo ""
echo "Step 3/6 — Fetching dvhcvn (pre-merger) data..."
cd /app/etl
python fetch_dvhcvn.py ${FORCE_FETCH:+--force}

# ── 4. Transform + load pre-merger ───────────────────────────────────────────
echo ""
echo "Step 4/6 — Loading pre-merger data..."
python transform.py
python load_to_pg.py

# ── 5. Seed aliases ───────────────────────────────────────────────────────────
echo ""
echo "Step 5/6 — Seeding aliases..."
python seed_aliases.py

# ── 6. Load post-merger data ──────────────────────────────────────────────────
echo ""
echo "Step 6/6 — Loading post-merger data (34 provinces, ThangLeQuoc)..."
python load_post_merger.py

echo ""
echo "========================================"
echo " Pipeline complete!"
echo "========================================"
echo ""
python -c "
import psycopg, os
conn = psycopg.connect(os.environ['DATABASE_URL'])
with conn.cursor() as cur:
    cur.execute('SELECT era, unit_type, COUNT(*) FROM admin_units GROUP BY era, unit_type ORDER BY era, unit_type')
    rows = cur.fetchall()
print('ERA             TYPE        COUNT')
print('-' * 35)
for era, utype, cnt in rows:
    print(f'{era:<15} {utype:<10} {cnt:>6}')
"
