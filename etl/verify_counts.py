"""
verify_counts.py
────────────────
Sanity-check row counts after loading dvhcvn data.
Fails with exit code 1 if any assertion is violated.

Expected (based on Vietnam's administrative structure):
  provinces: 63   (pre-2025 merger; will drop to ~34 post-merger)
  districts:  ~700
  wards:     ~11,000
"""

import os
import sys
from pathlib import Path

import psycopg
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

EXPECTED = {
    "province": (60, 70),      # 63 expected, allow small range
    "district": (650, 750),    # ~700 expected
    "ward":     (10_000, 12_000),  # ~11,000 expected
}


def get_dsn() -> str:
    return os.getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/vn_address")


def main() -> None:
    dsn = get_dsn()
    failed = False

    print("Verifying admin_units row counts...")

    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT unit_type, COUNT(*) AS cnt
                FROM admin_units
                WHERE valid_to IS NULL
                GROUP BY unit_type
            """)
            actual: dict[str, int] = {row[0]: row[1] for row in cur.fetchall()}

    for unit_type, (lo, hi) in EXPECTED.items():
        count = actual.get(unit_type, 0)
        ok = lo <= count <= hi
        status = "OK" if ok else "FAIL"
        print(f"  {status}  {unit_type}: {count}  (expected {lo}–{hi})")
        if not ok:
            failed = True

    # Check orphan non-province units
    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT COUNT(*) FROM admin_units
                WHERE unit_type != 'province' AND parent_id IS NULL AND valid_to IS NULL
            """)
            orphans = cur.fetchone()[0]
            ok = orphans == 0
            print(f"  {'OK' if ok else 'FAIL'}  orphan non-province units: {orphans}  (expected 0)")
            if not ok:
                failed = True

    # Check aliases were seeded
    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM aliases WHERE alias_type = 'province'")
            alias_count = cur.fetchone()[0]
            ok = alias_count >= 10
            print(f"  {'OK' if ok else 'FAIL'}  province aliases: {alias_count}  (expected >= 10)")
            if not ok:
                failed = True

    if failed:
        print("\nVerification FAILED — check ETL output above.")
        sys.exit(1)
    else:
        print("\nAll checks passed.")


if __name__ == "__main__":
    main()
