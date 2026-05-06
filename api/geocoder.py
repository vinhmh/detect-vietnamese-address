"""
geocoder.py
───────────
Looks up a place name (hospital, university, hotel…) via external geocoding APIs
to obtain a real address string, which is then fed into the heuristic resolver.

Priority:
  1. Nominatim (OpenStreetMap) — free, no key required, good Vietnam coverage
  2. Google Places API          — richer POI data, requires GOOGLE_API_KEY env var

Trigger conditions (checked in resolver.py before calling):
  - Input contains Vietnamese place-type keywords (bệnh viện, trường đại học…)
  - OR heuristic confidence < GEOCODER_CONFIDENCE_THRESHOLD

Usage:
    from api.geocoder import geocode
    result = geocode("bệnh viện đa khoa Đông Anh")
    # GeocoderResult(address="Bệnh viện Đa khoa Đông Anh, Đông Anh, Hà Nội",
    #                source="nominatim", lat=21.03, lon=105.84)
"""

import os
import time
from dataclasses import dataclass

import httpx

# ── Config ────────────────────────────────────────────────────────────────────

NOMINATIM_URL   = "https://nominatim.openstreetmap.org/search"
GOOGLE_PLACES_URL = "https://maps.googleapis.com/maps/api/place/findplacefromtext/json"
GOOGLE_API_KEY  = os.getenv("GOOGLE_API_KEY", "")

# Nominatim asks for a descriptive User-Agent
NOMINATIM_HEADERS = {
    "User-Agent": "VietnamAddressResolver/1.0 (https://github.com/vinhmh/detect-vietnamese-address)"
}

# Shared HTTP client with reasonable timeouts
_client: httpx.Client | None = None

def _get_client() -> httpx.Client:
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.Client(timeout=8.0, follow_redirects=True)
    return _client


# ── Result model ──────────────────────────────────────────────────────────────

@dataclass
class GeocoderResult:
    address:     str          # full address string to feed back into resolver
    source:      str          # "nominatim" | "google"
    display_name: str         # raw result from geocoder (for UI display)
    lat:         float | None = None
    lon:         float | None = None


# ── Vietnamese place-type keywords ────────────────────────────────────────────
# If the input contains any of these, geocoder is triggered proactively
# (before even running the heuristic) since they're POI names, not addresses.

PLACE_KEYWORDS = {
    # Healthcare
    "bệnh viện", "benh vien", "phòng khám", "phong kham", "trạm y tế", "tram y te",
    # Education
    "trường", "truong", "đại học", "dai hoc", "học viện", "hoc vien",
    "trung học", "tiểu học", "mầm non", "mam non",
    # Hospitality / food
    "khách sạn", "khach san", "nhà hàng", "nha hang", "quán", "quan",
    "resort", "hotel",
    # Commerce
    "siêu thị", "sieu thi", "trung tâm thương mại", "trung tam", "chợ", "cho",
    "công ty", "cong ty", "chi nhánh", "chi nhanh",
    # Religious / landmarks
    "chùa", "chua", "nhà thờ", "nha tho", "đình", "dinh", "đền", "den",
    "tháp", "thap", "cầu", "cau", "hồ", "ho",
    # Government / public
    "ủy ban", "uy ban", "tòa án", "toa an", "bưu điện", "buu dien",
    "công an", "cong an", "sân bay", "san bay", "ga ",
}


def is_place_name(text: str) -> bool:
    """Return True if the input looks like a POI name rather than a raw address."""
    lower = text.lower()
    return any(kw in lower for kw in PLACE_KEYWORDS)


# ── Nominatim ─────────────────────────────────────────────────────────────────

_last_nominatim_call: float = 0.0   # enforce 1 req/s rate limit


def _nominatim(query: str) -> GeocoderResult | None:
    global _last_nominatim_call

    # Respect Nominatim's 1 req/s policy
    elapsed = time.monotonic() - _last_nominatim_call
    if elapsed < 1.1:
        time.sleep(1.1 - elapsed)

    params = {
        "q":              query,
        "countrycodes":   "vn",
        "format":         "json",
        "addressdetails": 1,
        "limit":          3,
        "accept-language": "vi",
    }
    try:
        resp = _get_client().get(NOMINATIM_URL, params=params, headers=NOMINATIM_HEADERS)
        _last_nominatim_call = time.monotonic()
        resp.raise_for_status()
        results = resp.json()
    except Exception:
        return None

    if not results:
        return None

    best = results[0]
    addr = best.get("address", {})

    # Build a clean address string from structured components
    parts = []
    for key in ("road", "neighbourhood", "suburb", "quarter",
                "village", "town", "city_district", "city", "state"):
        val = addr.get(key)
        if val and val not in parts:
            parts.append(val)

    address_str = best.get("display_name", ", ".join(parts))

    return GeocoderResult(
        address=address_str,
        source="nominatim",
        display_name=best.get("display_name", ""),
        lat=float(best["lat"]) if "lat" in best else None,
        lon=float(best["lon"]) if "lon" in best else None,
    )


# ── Google Places ─────────────────────────────────────────────────────────────

def _google_places(query: str) -> GeocoderResult | None:
    if not GOOGLE_API_KEY:
        return None
    params = {
        "input":          query,
        "inputtype":      "textquery",
        "fields":         "formatted_address,geometry,name",
        "key":            GOOGLE_API_KEY,
        "locationbias":   "country:vn",
        "language":       "vi",
    }
    try:
        resp = _get_client().get(GOOGLE_PLACES_URL, params=params)
        resp.raise_for_status()
        data = resp.json()
    except Exception:
        return None

    candidates = data.get("candidates", [])
    if not candidates:
        return None

    best = candidates[0]
    address = best.get("formatted_address", "")
    loc = best.get("geometry", {}).get("location", {})

    return GeocoderResult(
        address=address,
        source="google",
        display_name=f"{best.get('name', '')} — {address}",
        lat=loc.get("lat"),
        lon=loc.get("lng"),
    )


# ── Public API ────────────────────────────────────────────────────────────────

def geocode(query: str) -> GeocoderResult | None:
    """
    Look up a place name and return an address string.

    Tries Google Places first if GOOGLE_API_KEY is set (richer POI data),
    then falls back to Nominatim (free, no key).
    Returns None if both fail.
    """
    if GOOGLE_API_KEY:
        result = _google_places(query)
        if result:
            return result

    return _nominatim(query)
