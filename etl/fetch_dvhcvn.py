"""
fetch_dvhcvn.py
───────────────
Download dvhcvn data files from GitHub (raw.githubusercontent.com).

Files fetched:
  - data/sorted.json   Primary: flat array format with ascii_name already included
  - data/dvhcvn.json   Supplement: full nested hierarchy with level1/2/3 ids

Usage:
  python fetch_dvhcvn.py [--force]

  --force   Skip ETag check and re-download even if local files are fresh.

Output: writes files to  ../data/  (sibling of etl/)
"""

import argparse
import json
import sys
from pathlib import Path

import httpx

BASE_URL = "https://raw.githubusercontent.com/daohoangson/dvhcvn/master/data"
DATA_DIR = Path(__file__).parent.parent / "data"

FILES = [
    "sorted.json",
    "dvhcvn.json",
    "date.txt",
]


def fetch_file(client: httpx.Client, filename: str, force: bool) -> Path:
    url = f"{BASE_URL}/{filename}"
    dest = DATA_DIR / filename
    etag_file = DATA_DIR / f".etag_{filename}"

    headers: dict[str, str] = {}
    if not force and etag_file.exists() and dest.exists():
        cached_etag = etag_file.read_text().strip()
        if cached_etag:
            headers["If-None-Match"] = cached_etag

    print(f"  GET {url} ...", end=" ", flush=True)
    response = client.get(url, headers=headers, follow_redirects=True)

    if response.status_code == 304:
        print("cached (ETag match)")
        return dest

    response.raise_for_status()

    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(response.content)

    etag = response.headers.get("ETag", "")
    etag_file.write_text(etag)

    size_kb = len(response.content) / 1024
    print(f"saved ({size_kb:.1f} KB)")
    return dest


def validate_sorted_json(path: Path) -> None:
    data = json.loads(path.read_bytes())
    assert isinstance(data, list), "sorted.json root must be a list"
    assert len(data) > 0, "sorted.json is empty"

    first = data[0]
    assert isinstance(first, list) and len(first) == 5, (
        f"Expected [id, name, prefix, ascii_name, children], got {first!r}"
    )
    print(f"  sorted.json: {len(data)} provinces, format OK")


def validate_dvhcvn_json(path: Path) -> None:
    raw = json.loads(path.read_bytes())
    # Root may be {"data": [...]} or a bare list
    data = raw["data"] if isinstance(raw, dict) and "data" in raw else raw
    assert isinstance(data, list), "dvhcvn.json: expected list under root or root['data']"
    assert len(data) > 0, "dvhcvn.json is empty"

    first = data[0]
    assert "level1_id" in first, "Missing level1_id in dvhcvn.json"
    assert "level2s" in first, "Missing level2s in dvhcvn.json"
    print(f"  dvhcvn.json: {len(data)} provinces, format OK")


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch dvhcvn data from GitHub")
    parser.add_argument("--force", action="store_true", help="Force re-download even if cached")
    args = parser.parse_args()

    DATA_DIR.mkdir(parents=True, exist_ok=True)

    print("Fetching dvhcvn data files...")
    with httpx.Client(timeout=60.0) as client:
        for filename in FILES:
            try:
                fetch_file(client, filename, force=args.force)
            except httpx.HTTPStatusError as exc:
                print(f"ERROR: HTTP {exc.response.status_code} for {filename}")
                sys.exit(1)
            except Exception as exc:
                print(f"ERROR: {exc}")
                sys.exit(1)

    print("\nValidating downloaded files...")
    validate_sorted_json(DATA_DIR / "sorted.json")
    validate_dvhcvn_json(DATA_DIR / "dvhcvn.json")

    date_str = (DATA_DIR / "date.txt").read_text().strip()
    print(f"  Data as of: {date_str}")
    print("\nDone. Files saved to:", DATA_DIR)


if __name__ == "__main__":
    main()
