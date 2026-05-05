"""
seed_aliases.py
───────────────
Seed the aliases table with:
  1. Province abbreviations  (tphcm → Thành phố Hồ Chí Minh, etc.)
  2. Admin-type shorthands   (tp → thành phố, q → quận, p → phường, etc.)
  3. Auto-generated aliases  from admin_units rows (short lowercased names)

Usage:
  python seed_aliases.py

Idempotent: uses INSERT ... ON CONFLICT DO NOTHING.
"""

import os
import sys
from pathlib import Path

import psycopg
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")


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


# ── 1. Province / city abbreviations ─────────────────────────────────────────
#
# alias → canonical full_name (must match admin_units.full_name exactly)
# Weight 2.0 = high confidence shorthands

PROVINCE_ALIASES: list[tuple[str, str, float]] = [
    # Hồ Chí Minh
    ("tphcm",        "Thành phố Hồ Chí Minh", 2.0),
    ("tp hcm",       "Thành phố Hồ Chí Minh", 2.0),
    ("tp.hcm",       "Thành phố Hồ Chí Minh", 2.0),
    ("hcm",          "Thành phố Hồ Chí Minh", 1.8),
    ("hcmc",         "Thành phố Hồ Chí Minh", 1.8),
    ("sg",           "Thành phố Hồ Chí Minh", 1.5),
    ("sai gon",      "Thành phố Hồ Chí Minh", 1.5),
    ("saigon",       "Thành phố Hồ Chí Minh", 1.5),
    ("sài gòn",      "Thành phố Hồ Chí Minh", 1.5),
    # Hà Nội
    ("hn",           "Thành phố Hà Nội", 2.0),
    ("ha noi",       "Thành phố Hà Nội", 1.8),
    ("hanoi",        "Thành phố Hà Nội", 1.8),
    # Đà Nẵng
    ("dn",           "Thành phố Đà Nẵng", 2.0),
    ("da nang",      "Thành phố Đà Nẵng", 1.8),
    ("danang",       "Thành phố Đà Nẵng", 1.8),
    # Hải Phòng
    ("hp",           "Thành phố Hải Phòng", 2.0),
    ("hai phong",    "Thành phố Hải Phòng", 1.8),
    # Cần Thơ
    ("ct",           "Thành phố Cần Thơ", 1.8),
    ("can tho",      "Thành phố Cần Thơ", 1.8),
    # Popular provinces
    ("qn",           "Tỉnh Quảng Nam",    1.5),
    ("qng",          "Tỉnh Quảng Ngãi",   1.5),
    ("qb",           "Tỉnh Quảng Bình",   1.5),
    ("qt",           "Tỉnh Quảng Trị",    1.5),
    ("qni",          "Tỉnh Quảng Ninh",   1.5),
    ("kh",           "Tỉnh Khánh Hòa",    1.5),
    ("bd",           "Tỉnh Bình Dương",   1.5),
    ("bl",           "Tỉnh Bình Long",    1.2),
    ("brvt",         "Tỉnh Bà Rịa - Vũng Tàu", 2.0),
    ("vung tau",     "Tỉnh Bà Rịa - Vũng Tàu", 1.5),
    ("vt",           "Tỉnh Bà Rịa - Vũng Tàu", 1.3),
    ("ld",           "Tỉnh Lâm Đồng",     1.5),
    ("dalat",        "Tỉnh Lâm Đồng",     1.3),
    ("da lat",       "Tỉnh Lâm Đồng",     1.3),
    ("ag",           "Tỉnh An Giang",     1.5),
    ("dt",           "Tỉnh Đồng Tháp",    1.5),
    ("la",           "Tỉnh Long An",      1.3),
    ("tg",           "Tỉnh Tiền Giang",   1.5),
    ("btre",         "Tỉnh Bến Tre",      1.5),
    ("tv",           "Tỉnh Trà Vinh",     1.5),
    ("vl",           "Tỉnh Vĩnh Long",    1.5),
    ("hg",           "Tỉnh Hậu Giang",    1.5),
    ("kg",           "Tỉnh Kiên Giang",   1.5),
    ("cm",           "Tỉnh Cà Mau",       1.5),
    ("st",           "Tỉnh Sóc Trăng",    1.5),
    ("bg",           "Tỉnh Bắc Giang",    1.5),
    ("bk",           "Tỉnh Bắc Kạn",     1.5),
    ("bn",           "Tỉnh Bắc Ninh",     1.5),
    ("cb",           "Tỉnh Cao Bằng",     1.5),
    ("hb",           "Tỉnh Hòa Bình",     1.5),
    ("hd",           "Tỉnh Hải Dương",    1.5),
    ("hnam",         "Tỉnh Hà Nam",       1.5),
    ("hg2",          "Tỉnh Hà Giang",     1.3),
    ("ht",           "Tỉnh Hà Tĩnh",      1.5),
    ("hue",          "Tỉnh Thừa Thiên Huế", 1.5),
    ("tth",          "Tỉnh Thừa Thiên Huế", 1.8),
    ("lang son",     "Tỉnh Lạng Sơn",     1.5),
    ("ls",           "Tỉnh Lạng Sơn",     1.3),
    ("lc",           "Tỉnh Lai Châu",     1.5),
    ("nb",           "Tỉnh Ninh Bình",    1.5),
    ("nt",           "Tỉnh Ninh Thuận",   1.5),
    ("na",           "Tỉnh Nghệ An",      1.5),
    ("py",           "Tỉnh Phú Yên",      1.5),
    ("pt",           "Tỉnh Phú Thọ",      1.5),
    ("sb",           "Tỉnh Sơn La",       1.5),
    ("tbn",          "Tỉnh Thái Bình",    1.5),
    ("tn",           "Tỉnh Thái Nguyên",  1.5),
    ("th",           "Tỉnh Thanh Hóa",    1.5),
    ("tb",           "Tỉnh Tuyên Quang",  1.3),
    ("vp",           "Tỉnh Vĩnh Phúc",    1.5),
    ("yb",           "Tỉnh Yên Bái",      1.5),
    ("gl",           "Tỉnh Gia Lai",      1.5),
    ("dak lak",      "Tỉnh Đắk Lắk",      1.5),
    ("dl",           "Tỉnh Đắk Lắk",      1.3),
    ("dak nong",     "Tỉnh Đắk Nông",     1.5),
    ("kon tum",      "Tỉnh Kon Tum",      1.5),
    ("bp",           "Tỉnh Bình Phước",   1.5),
    ("bt",           "Tỉnh Bình Thuận",   1.5),
    ("br",           "Tỉnh Bình Phước",   1.2),
    ("dn2",          "Tỉnh Đồng Nai",     1.5),
    ("tay ninh",     "Tỉnh Tây Ninh",     1.5),
]

# ── 2. Admin-type shorthands ──────────────────────────────────────────────────
#
# These are generic expansions used by the normalization engine.
# alias_type = 'unit_type' — signals these are structural tokens, not place names.

UNIT_TYPE_ALIASES: list[tuple[str, str, float]] = [
    ("tp",    "thành phố",  2.0),
    ("tphcm", "thành phố",  1.5),
    ("q",     "quận",       2.0),
    ("qua",   "quận",       1.5),
    ("h",     "huyện",      1.8),
    ("hu",    "huyện",      1.5),
    ("p",     "phường",     2.0),
    ("phu",   "phường",     1.5),
    ("x",     "xã",         2.0),
    ("tx",    "thị xã",     2.0),
    ("tt",    "thị trấn",   2.0),
    ("kp",    "khu phố",    2.0),
    ("khu",   "khu phố",    1.5),
    ("dg",    "đường",      2.0),
    ("d",     "đường",      1.8),
    ("duong", "đường",      1.8),
    ("hxh",   "hẻm",        2.0),
    ("hem",   "hẻm",        2.0),
    ("ngo",   "ngõ",        1.8),
    ("nga",   "ngách",      1.8),
    ("tl",    "tỉnh lộ",    1.8),
    ("ql",    "quốc lộ",    1.8),
    ("cl",    "canal",      1.3),
]

INSERT_SQL = """
INSERT INTO aliases (alias, canonical, alias_type, weight)
VALUES (%(alias)s, %(canonical)s, %(alias_type)s, %(weight)s)
ON CONFLICT DO NOTHING
"""


def seed_province_aliases(conn: psycopg.Connection) -> int:
    count = 0
    with conn.cursor() as cur:
        for alias, canonical, weight in PROVINCE_ALIASES:
            cur.execute(INSERT_SQL, {
                "alias":      alias.lower().strip(),
                "canonical":  canonical,
                "alias_type": "province",
                "weight":     weight,
            })
            count += cur.rowcount
    return count


def seed_unit_type_aliases(conn: psycopg.Connection) -> int:
    count = 0
    with conn.cursor() as cur:
        for alias, canonical, weight in UNIT_TYPE_ALIASES:
            cur.execute(INSERT_SQL, {
                "alias":      alias.lower().strip(),
                "canonical":  canonical,
                "alias_type": "unit_type",
                "weight":     weight,
            })
            count += cur.rowcount
    return count


def seed_auto_aliases(conn: psycopg.Connection) -> int:
    """
    Auto-generate aliases from admin_units:
    - Insert lowercase ascii_name as alias → full_name as canonical
    - This gives free fuzzy alias coverage for all 11k+ units
    """
    count = 0
    with conn.cursor() as cur:
        cur.execute("""
            SELECT id, full_name, ascii_name, unit_type
            FROM admin_units
            WHERE ascii_name IS NOT NULL AND ascii_name != ''
        """)
        rows = cur.fetchall()
        for row in rows:
            unit_id, full_name, ascii_name, unit_type = row
            alias = ascii_name.lower().strip()
            cur.execute("""
                INSERT INTO aliases (alias, canonical, alias_type, unit_id, weight)
                VALUES (%s, %s, %s, %s, 1.0)
                ON CONFLICT DO NOTHING
            """, (alias, full_name, unit_type, unit_id))
            count += cur.rowcount
    return count


def main() -> None:
    dsn = get_dsn()
    print("Seeding aliases table...")

    with psycopg.connect(dsn, autocommit=False) as conn:
        n = seed_province_aliases(conn)
        print(f"  Province abbreviations: {n} inserted")

        n = seed_unit_type_aliases(conn)
        print(f"  Unit-type shorthands:   {n} inserted")

        n = seed_auto_aliases(conn)
        print(f"  Auto ascii_name aliases: {n} inserted")

        conn.commit()

    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT alias_type, COUNT(*) FROM aliases GROUP BY alias_type ORDER BY alias_type")
            print("\n  Alias counts by type:")
            for row in cur.fetchall():
                print(f"    {row[0]}: {row[1]}")

    print("\nSeed complete.")


if __name__ == "__main__":
    main()
