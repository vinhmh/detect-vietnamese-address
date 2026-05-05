"""
load_post_merger.py
───────────────────
Fetches the post-2025-merger administrative structure from:
  ThangLeQuoc/vietnamese-provinces-database (updated to decree 19/2025/QĐ-TTg,
  effective 2025-07-01).

Structure: 34 provinces → 3 321 wards (districts were abolished).

Steps:
  1. Download simplified JSON from GitHub.
  2. INSERT 34 provinces (valid_from=2025-07-01, era='post_merger').
  3. INSERT 3 321 wards directly under their province (no district).
  4. Mark all pre-merger rows  valid_to = '2025-06-30'
     (only touches rows still open, i.e. valid_to IS NULL with era='pre_merger').
"""

import json
import os
import sys
from datetime import date
from pathlib import Path

import httpx
import psycopg
from dotenv import load_dotenv
from unidecode import unidecode

load_dotenv(Path(__file__).parent.parent / ".env")

DSN = os.getenv(
    "DATABASE_URL",
    "postgresql://postgres:postgres@localhost:5432/vn_address",
)

SOURCE_URL = (
    "https://raw.githubusercontent.com/ThangLeQuoc/"
    "vietnamese-provinces-database/master/json/"
    "simplified_json_generated_data_vn_units_minified.json"
)

VALID_FROM = date(2025, 7, 1)
PRE_MERGER_VALID_TO = date(2025, 6, 30)


# ── Helpers ──────────────────────────────────────────────────────────────────

def _ascii(text: str) -> str:
    """Strip accents + apostrophes → lowercase ASCII for trigram indexing."""
    cleaned = text.replace("'", "").replace("\u2019", "").replace("\u02bc", "")
    return unidecode(cleaned).lower()


def _prefix_of(full_name: str) -> tuple[str, str]:
    """
    Split 'Thành phố Hà Nội' → ('Thành phố', 'Hà Nội').
    Handles standard Vietnamese admin prefixes.
    """
    PREFIXES = [
        "Thành phố", "Tỉnh",
        "Phường", "Xã", "Thị trấn", "Thị xã",
        "Quận", "Huyện",
    ]
    for p in sorted(PREFIXES, key=len, reverse=True):
        if full_name.startswith(p + " "):
            return p, full_name[len(p) + 1:]
    return "", full_name


def _unit_type(full_name: str, level: str) -> str:
    """Determine unit_type from the full name prefix."""
    PROVINCE_TYPES = {"Thành phố", "Tỉnh"}
    DISTRICT_TYPES = {"Quận", "Huyện", "Thị xã"}
    WARD_TYPES = {"Phường", "Xã", "Thị trấn"}

    prefix, _ = _prefix_of(full_name)
    if prefix in PROVINCE_TYPES or level == "province":
        return "province"
    if prefix in DISTRICT_TYPES or level == "district":
        return "district"
    if prefix in WARD_TYPES or level == "ward":
        return "ward"
    # fallback
    return level


# ── Fetch ─────────────────────────────────────────────────────────────────────

def fetch_data() -> list[dict]:
    print(f"Fetching post-merger data from ThangLeQuoc …")
    r = httpx.get(SOURCE_URL, follow_redirects=True, timeout=30)
    r.raise_for_status()
    return r.json()


# ── Load ──────────────────────────────────────────────────────────────────────

UPSERT_SQL = """
INSERT INTO admin_units
    (gso_code, full_name, name, name_prefix, unit_type, ascii_name,
     parent_id, valid_from, valid_to, era)
VALUES
    (%(gso_code)s, %(full_name)s, %(name)s, %(name_prefix)s, %(unit_type)s,
     %(ascii_name)s, %(parent_id)s, %(valid_from)s, NULL, 'post_merger')
ON CONFLICT (gso_code, valid_from) DO UPDATE SET
    full_name   = EXCLUDED.full_name,
    name        = EXCLUDED.name,
    name_prefix = EXCLUDED.name_prefix,
    unit_type   = EXCLUDED.unit_type,
    ascii_name  = EXCLUDED.ascii_name,
    parent_id   = EXCLUDED.parent_id,
    era         = 'post_merger',
    updated_at  = now()
RETURNING id
"""


def load(data: list[dict]) -> None:
    total_provinces = len(data)
    total_wards = sum(len(p.get("Wards", [])) for p in data)
    print(f"  Provinces: {total_provinces}  |  Wards: {total_wards}")

    with psycopg.connect(DSN) as conn:

        # ── Step 1: insert provinces ─────────────────────────────────────────
        print("Loading provinces …")
        prov_id_map: dict[str, int] = {}   # gso_code → db id

        for p in data:
            pcode = p["Code"]
            full_name = p["FullName"]
            prefix, name = _prefix_of(full_name)

            row = dict(
                gso_code=pcode,
                full_name=full_name,
                name=name,
                name_prefix=prefix,
                unit_type="province",
                ascii_name=_ascii(name),
                parent_id=None,
                valid_from=VALID_FROM,
            )
            with conn.cursor() as cur:
                cur.execute(UPSERT_SQL, row)
                prov_id_map[pcode] = cur.fetchone()[0]

        conn.commit()
        print(f"  ✓ {len(prov_id_map)} provinces inserted / updated")

        # ── Step 2: insert wards directly under provinces ────────────────────
        print("Loading wards …")
        ward_count = 0

        for p in data:
            pcode = p["Code"]
            parent_db_id = prov_id_map[pcode]

            for w in p.get("Wards", []):
                wcode = w["Code"]
                full_name = w["FullName"]
                prefix, name = _prefix_of(full_name)

                row = dict(
                    gso_code=wcode,
                    full_name=full_name,
                    name=name,
                    name_prefix=prefix,
                    unit_type="ward",
                    ascii_name=_ascii(name),
                    parent_id=parent_db_id,
                    valid_from=VALID_FROM,
                )
                with conn.cursor() as cur:
                    cur.execute(UPSERT_SQL, row)
                ward_count += 1

        conn.commit()
        print(f"  ✓ {ward_count} wards inserted / updated")

        # ── Step 3: close pre-merger records ────────────────────────────────
        print("Marking pre-merger units as valid_to = 2025-06-30 …")
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE admin_units
                SET valid_to = %s
                WHERE era = 'pre_merger' AND valid_to IS NULL
            """, (PRE_MERGER_VALID_TO,))
            closed = cur.rowcount
        conn.commit()
        print(f"  ✓ {closed} pre-merger rows closed (valid_to = 2025-06-30)")

    print("Done.")


# ── Main ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    data = fetch_data()
    print(f"  Downloaded {len(data)} provinces")
    load(data)
