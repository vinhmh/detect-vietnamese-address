"""
geocoder.py
───────────
Looks up a place name (hospital, university, hotel…) to get a real address string,
which is then fed into the heuristic resolver.

Priority:
  1. Gemini + Google Search grounding (requires GEMINI_API_KEY)
       → Gemini searches the web in real-time, reads results, extracts the address.
       → Most accurate for well-known Vietnamese POIs.
  2. Nominatim (OpenStreetMap) — free fallback, no key required.
       → Good coverage for registered POIs.

Trigger conditions (checked in resolver.py):
  - Input contains Vietnamese place-type keywords (bệnh viện, trường đại học…)
  - OR heuristic confidence < GEOCODER_CONFIDENCE_THRESHOLD (0.65)
"""

import os
import time
from dataclasses import dataclass

import httpx

# ── Env ───────────────────────────────────────────────────────────────────────

GEMINI_API_KEY  = os.getenv("GEMINI_API_KEY", "")
NOMINATIM_URL   = "https://nominatim.openstreetmap.org/search"
NOMINATIM_HEADERS = {
    "User-Agent": "VietnamAddressResolver/1.0 (https://github.com/vinhmh/detect-vietnamese-address)"
}

_http: httpx.Client | None = None

def _get_http() -> httpx.Client:
    global _http
    if _http is None or _http.is_closed:
        _http = httpx.Client(timeout=15.0, follow_redirects=True)
    return _http


# ── Result model ──────────────────────────────────────────────────────────────

@dataclass
class GeocoderResult:
    address:      str          # address string to re-feed into resolver
    source:       str          # "gemini" | "nominatim"
    display_name: str          # raw result (for UI banner)
    reasoning:    str | None = None   # Gemini's explanation (optional, for UI)
    lat:          float | None = None
    lon:          float | None = None


# ── Vietnamese place-type keywords ────────────────────────────────────────────

PLACE_KEYWORDS = {
    # Healthcare
    "bệnh viện", "benh vien", "phòng khám", "phong kham", "trạm y tế", "tram y te",
    # Education
    "trường", "truong", "đại học", "dai hoc", "học viện", "hoc vien",
    "trung học", "tiểu học", "mầm non", "mam non",
    # Hospitality / food
    "khách sạn", "khach san", "nhà hàng", "nha hang", "resort", "hotel",
    # Commerce
    "siêu thị", "sieu thi", "trung tâm thương mại", "trung tam",
    "chợ", "cho", "công ty", "cong ty", "chi nhánh", "chi nhanh",
    # Religious / landmarks
    "chùa", "chua", "nhà thờ", "nha tho", "đình", "dinh", "đền", "den",
    "tháp", "thap", "cầu", "cau",
    # Government / public
    "ủy ban", "uy ban", "tòa án", "toa an", "bưu điện", "buu dien",
    "công an", "cong an", "sân bay", "san bay", "ga ",
}


def is_place_name(text: str) -> bool:
    """Return True if the input looks like a POI name rather than a raw address."""
    lower = text.lower()
    return any(kw in lower for kw in PLACE_KEYWORDS)


# ── Gemini + Google Search grounding ─────────────────────────────────────────

_GEMINI_PROMPT = """\
You are a Vietnamese address extraction assistant.

The user is looking for the full address of the following place in Vietnam:
"{query}"

Use Google Search to find its real address. Then return a JSON object with exactly these fields:
{{
  "address": "<full address in Vietnamese: street number + street name, ward/commune, district (if applicable), province/city — do NOT include postal code or 'Việt Nam'>",
  "reasoning": "<1-2 sentences explaining how you found it>"
}}

Rules:
- address must be in Vietnamese
- End with the province/city name — do NOT append postal code, country name, or 'Việt Nam'
- If you cannot find a reliable address, set address to null
- Return ONLY valid JSON, no markdown, no explanation outside the JSON
"""


def _gemini_search(query: str) -> GeocoderResult | None:
    """Call Gemini with Google Search grounding to find a place's address."""
    if not GEMINI_API_KEY:
        return None

    try:
        from google import genai
        from google.genai import types
    except ImportError:
        return None

    client = genai.Client(api_key=GEMINI_API_KEY)

    prompt = _GEMINI_PROMPT.format(query=query)

    try:
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt,
            config=types.GenerateContentConfig(
                tools=[types.Tool(google_search=types.GoogleSearch())],
                temperature=0.0,
            ),
        )
    except Exception:
        return None

    raw = response.text.strip() if response.text else ""

    # Strip markdown code fences if present
    if raw.startswith("```"):
        lines = raw.splitlines()
        raw = "\n".join(
            l for l in lines
            if not l.strip().startswith("```")
        ).strip()

    try:
        import json
        parsed = json.loads(raw)
    except Exception:
        # Gemini returned free text — treat whole response as address
        address = raw.strip()
        if not address or len(address) > 300:
            return None
        return GeocoderResult(
            address=address,
            source="gemini",
            display_name=address,
            reasoning=None,
        )

    address = parsed.get("address")
    if not address:
        return None

    return GeocoderResult(
        address=str(address),
        source="gemini",
        display_name=str(address),
        reasoning=parsed.get("reasoning"),
    )


# ── Nominatim fallback ────────────────────────────────────────────────────────

_last_nominatim_call: float = 0.0


def _nominatim(query: str) -> GeocoderResult | None:
    global _last_nominatim_call

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
        resp = _get_http().get(NOMINATIM_URL, params=params, headers=NOMINATIM_HEADERS)
        _last_nominatim_call = time.monotonic()
        resp.raise_for_status()
        results = resp.json()
    except Exception:
        return None

    if not results:
        return None

    best = results[0]
    addr = best.get("address", {})

    # Build a clean address string: road → ward → district → province
    # Excludes country and postal code so positional province anchor works correctly.
    parts = []
    for key in ("road", "neighbourhood", "suburb", "quarter",
                "village", "town", "city_district", "city", "state"):
        val = addr.get(key)
        if val and val not in parts:
            parts.append(val)
    clean_address = ", ".join(parts) if parts else best.get("display_name", "")

    return GeocoderResult(
        address=clean_address,
        source="nominatim",
        display_name=best.get("display_name", ""),   # full string for UI banner
        lat=float(best["lat"]) if "lat" in best else None,
        lon=float(best["lon"]) if "lon" in best else None,
    )


# ── Public API ────────────────────────────────────────────────────────────────

def geocode(query: str) -> GeocoderResult | None:
    """
    Look up a place name → address string.

    Uses Gemini + Google Search if GEMINI_API_KEY is set (primary).
    Falls back to Nominatim (free, keyless) otherwise.
    Returns None if both fail.
    """
    if GEMINI_API_KEY:
        result = _gemini_search(query)
        if result and result.address:
            return result

    return _nominatim(query)
