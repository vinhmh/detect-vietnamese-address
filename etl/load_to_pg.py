"""
load_to_pg.py
─────────────
Load transformed AdminUnit rows into PostgreSQL admin_units table.

Strategy:
  - INSERT ... ON CONFLICT (gso_code) DO UPDATE
    → fully idempotent, safe to re-run on updated data
  - Two-pass load:
      Pass 1: insert all units with parent_id = NULL
      Pass 2: update parent_id by resolving parent_gso_code → id

Usage:
  python load_to_pg.py

Requires:
  .env (or environment) with DATABASE_URL set.
  Tables must exist — run migrations first.
"""

import os
import sys
from pathlib import Path

import psycopg
from dotenv import load_dotenv
from tqdm import tqdm

# local imports
sys.path.insert(0, str(Path(__file__).parent))
from transform import AdminUnit, parse_sorted_json, print_stats

DATA_DIR = Path(__file__).parent.parent / "data"
ENV_FILE = Path(__file__).parent.parent / ".env"

load_dotenv(ENV_FILE)


def get_dsn() -> str:
    dsn = os.getenv("DATABASE_URL")
    if not dsn:
        host = os.getenv("POSTGRES_HOST", "localhost")
        port = os.getenv("POSTGRES_PORT", "5432")
        db = os.getenv("POSTGRES_DB", "vn_address")
        user = os.getenv("POSTGRES_USER", "postgres")
        pw = os.getenv("POSTGRES_PASSWORD", "postgres")
        dsn = f"postgresql://{user}:{pw}@{host}:{port}/{db}"
    return dsn


UPSERT_SQL = """
INSERT INTO admin_units (
    gso_code, full_name, name, name_prefix, unit_type,
    ascii_name, parent_id, valid_from, valid_to
)
VALUES (
    %(gso_code)s, %(full_name)s, %(name)s, %(name_prefix)s, %(unit_type)s,
    %(ascii_name)s, NULL, '2000-01-01', NULL
)
ON CONFLICT (gso_code) DO UPDATE SET
    full_name   = EXCLUDED.full_name,
    name        = EXCLUDED.name,
    name_prefix = EXCLUDED.name_prefix,
    unit_type   = EXCLUDED.unit_type,
    ascii_name  = EXCLUDED.ascii_name,
    updated_at  = now()
"""

UPDATE_PARENT_SQL = """
UPDATE admin_units child
SET    parent_id = parent.id,
       updated_at = now()
FROM   admin_units parent
WHERE  parent.gso_code = %(parent_gso_code)s
  AND  child.gso_code  = %(gso_code)s
  AND  child.parent_id IS DISTINCT FROM parent.id
"""


def load_units(conn: psycopg.Connection, units: list[AdminUnit]) -> int:
    inserted = 0
    with conn.cursor() as cur:
        for unit in tqdm(units, desc="  Upserting admin_units", unit="rows"):
            cur.execute(UPSERT_SQL, {
                "gso_code":    unit.gso_code,
                "full_name":   unit.full_name,
                "name":        unit.name,
                "name_prefix": unit.name_prefix,
                "unit_type":   unit.unit_type,
                "ascii_name":  unit.ascii_name,
            })
            inserted += 1
    return inserted


def resolve_parents(conn: psycopg.Connection, units: list[AdminUnit]) -> int:
    children = [u for u in units if u.parent_gso_code is not None]
    updated = 0
    with conn.cursor() as cur:
        for unit in tqdm(children, desc="  Resolving parent_ids", unit="rows"):
            cur.execute(UPDATE_PARENT_SQL, {
                "parent_gso_code": unit.parent_gso_code,
                "gso_code":        unit.gso_code,
            })
            updated += cur.rowcount
    return updated


def verify(conn: psycopg.Connection) -> None:
    with conn.cursor() as cur:
        cur.execute("""
            SELECT unit_type, COUNT(*) as cnt
            FROM admin_units
            GROUP BY unit_type
            ORDER BY unit_type
        """)
        rows = cur.fetchall()
        print("\n  Row counts after load:")
        for row in rows:
            print(f"    {row[0]}: {row[1]}")

        cur.execute("SELECT COUNT(*) FROM admin_units WHERE parent_id IS NULL AND unit_type != 'province'")
        orphans = cur.fetchone()[0]
        if orphans > 0:
            print(f"\n  WARNING: {orphans} non-province units have NULL parent_id — check parent resolution")
        else:
            print("  parent_id resolution: OK (no orphans)")


def main() -> None:
    sorted_path = DATA_DIR / "sorted.json"
    if not sorted_path.exists():
        print(f"ERROR: {sorted_path} not found. Run fetch_dvhcvn.py first.")
        sys.exit(1)

    print("Parsing sorted.json...")
    units = parse_sorted_json(sorted_path)
    print_stats(units)

    dsn = get_dsn()
    print(f"\nConnecting to PostgreSQL...")

    with psycopg.connect(dsn, autocommit=False) as conn:
        print("Pass 1: Upsert all units...")
        inserted = load_units(conn, units)
        conn.commit()
        print(f"  {inserted} rows upserted")

        print("Pass 2: Resolve parent_ids...")
        updated = resolve_parents(conn, units)
        conn.commit()
        print(f"  {updated} parent links updated")

        verify(conn)

    print("\nLoad complete.")


if __name__ == "__main__":
    main()
