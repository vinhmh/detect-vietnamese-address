"""
transform.py
────────────
Parse sorted.json from dvhcvn into a flat list of AdminUnit dicts
ready for PostgreSQL insertion.

sorted.json format:
  Each entry is an array: [id, short_name, prefix, ascii_name, [children]]

  Level 1 (province):  children = list of level-2 arrays
  Level 2 (district):  children = list of level-3 arrays
  Level 3 (ward):      children = [] (empty)

Output: list of dicts matching admin_units table columns.
"""

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

DATA_DIR = Path(__file__).parent.parent / "data"

# ── Prefix → normalised unit_type ────────────────────────────────────────────

PROVINCE_PREFIXES = {
    "thành phố trung ương",
    "tỉnh",
}

DISTRICT_PREFIXES = {
    "quận",
    "huyện",
    "thành phố",
    "thị xã",
}

WARD_PREFIXES = {
    "phường",
    "xã",
    "thị trấn",
}


def prefix_to_unit_type(prefix: str) -> str:
    key = prefix.strip().lower()
    if key in PROVINCE_PREFIXES:
        return "province"
    if key in DISTRICT_PREFIXES:
        return "district"
    if key in WARD_PREFIXES:
        return "ward"
    raise ValueError(f"Unknown prefix: {prefix!r}")


# ── Row dataclass ─────────────────────────────────────────────────────────────

@dataclass
class AdminUnit:
    gso_code: str
    full_name: str
    name: str
    name_prefix: str
    unit_type: str
    ascii_name: str
    parent_gso_code: str | None = None  # resolved to parent_id in loader


# ── Parser ────────────────────────────────────────────────────────────────────

def parse_node(
    node: list[Any],
    parent_gso_code: str | None,
) -> list[AdminUnit]:
    """
    Recursively parse one node and its children.

    node = [gso_code, short_name, prefix, ascii_name, children]
    """
    gso_code: str = str(node[0])
    short_name: str = node[1]
    prefix: str = node[2]
    # Strip apostrophes/special punctuation from ascii_name so that
    # "Ea H'leo" → "Ea Hleo", improving trigram similarity with user input "eahleo"
    ascii_name: str = re.sub(r"['\u2019\u2018\u02bc\u0060]", "", node[3]).strip()
    children: list[Any] = node[4] if len(node) > 4 else []

    # Top-level nodes (no parent) are always provinces regardless of prefix.
    # Some municipalities use "Thành phố" without "Trung ương" in newer data.
    if parent_gso_code is None:
        unit_type = "province"
    else:
        unit_type = prefix_to_unit_type(prefix)
    full_name = f"{prefix} {short_name}" if prefix else short_name

    unit = AdminUnit(
        gso_code=gso_code,
        full_name=full_name,
        name=short_name,
        name_prefix=prefix,
        unit_type=unit_type,
        ascii_name=ascii_name,
        parent_gso_code=parent_gso_code,
    )

    result: list[AdminUnit] = [unit]
    for child in children:
        result.extend(parse_node(child, parent_gso_code=gso_code))

    return result


def parse_sorted_json(path: Path) -> list[AdminUnit]:
    raw: list[Any] = json.loads(path.read_bytes())
    units: list[AdminUnit] = []
    for province_node in raw:
        units.extend(parse_node(province_node, parent_gso_code=None))
    return units


# ── Stats helper ──────────────────────────────────────────────────────────────

def print_stats(units: list[AdminUnit]) -> None:
    by_type: dict[str, int] = {}
    for u in units:
        by_type[u.unit_type] = by_type.get(u.unit_type, 0) + 1
    print(f"  Parsed {len(units)} total units:")
    for t, count in sorted(by_type.items()):
        print(f"    {t}: {count}")


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    path = DATA_DIR / "sorted.json"
    if not path.exists():
        print(f"ERROR: {path} not found. Run fetch_dvhcvn.py first.")
        raise SystemExit(1)

    units = parse_sorted_json(path)
    print_stats(units)

    # Spot-check: Ho Chi Minh City
    hcm = next((u for u in units if "Ho Chi Minh" in u.ascii_name and u.unit_type == "province"), None)
    if hcm:
        print(f"\n  Spot-check province: {hcm.full_name!r} | ascii: {hcm.ascii_name!r} | code: {hcm.gso_code}")
    else:
        print("\n  WARNING: Could not find Ho Chi Minh City province — check data")
