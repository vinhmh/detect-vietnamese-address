"""
resolver.py
───────────
Core address resolution logic — top-down cascade.

Priority: Province → District → Ward → Street/House-number

Pipeline:
  1. Normalize      (unicode cleanup, ascii-strip, abbreviation expansion)
  2. House number   (regex extraction: "so 6", "số 3b", "s.10", …)
  3. Variants       (sliding token windows as search queries)
  4. Province       (global fuzzy search + alias lookup)
  5. District       (fuzzy search RESTRICTED to parent province)     [pre-merger only]
  6. Ward           (fuzzy search RESTRICTED to parent district/province)
  7. Street extract (remainder after removing all identified admin tokens)
  8. Score          (multi-signal formula, depth bonus)
  9. Rank           (sort, calibrate confidence, return)

The DB-level parent restriction (WHERE parent_id = ?) guarantees the hierarchy
is always valid — no post-hoc validation needed, no cross-level false positives.

Post-2025 merger: districts were abolished.  The cascade becomes Province → Ward.
Pass  as_of_date >= date(2025,7,1)  to use the new 34-province structure.
Default as_of_date is today (always post-merger from 2025-07-01 onward).
"""

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date

import psycopg
from psycopg.rows import dict_row

MERGER_DATE = date(2025, 7, 1)


# ── Scoring weights ───────────────────────────────────────────────────────────

W_PROVINCE = 10.0   # province similarity weight
W_DISTRICT =  8.0   # district similarity weight
W_WARD     =  6.0   # ward similarity weight

# Bonus for each additional level successfully resolved
BONUS_DISTRICT = 6.0   # earned when district is found within province
BONUS_WARD     = 10.0  # earned when ward is found within district


# ── Vietnamese accent-strip ───────────────────────────────────────────────────

def to_ascii(text: str) -> str:
    nfd = unicodedata.normalize("NFD", text)
    return "".join(c for c in nfd if unicodedata.category(c) != "Mn").lower()


# ── Normalization ─────────────────────────────────────────────────────────────

# Province shorthand → ascii canonical (applied before tokenization)
# Covers common 2-3 letter plate codes and colloquial abbreviations.
PROVINCE_MAP: dict[str, str] = {
    # Major cities
    "hcm":      "ho chi minh",
    "hcmc":     "ho chi minh",
    "sg":       "ho chi minh",
    "saigon":   "ho chi minh",
    "tphcm":    "ho chi minh",
    "hn":       "ha noi",
    "hanoi":    "ha noi",
    "dn":       "da nang",
    "danang":   "da nang",
    "hp":       "hai phong",
    "ct":       "can tho",
    # Provinces — plate/common codes
    "ag":       "an giang",
    "bk":       "bac kan",
    "bg":       "bac giang",
    "bl":       "bac lieu",
    "bn":       "bac ninh",
    "bt":       "binh thuan",
    "bd":       "binh duong",
    "bp":       "binh phuoc",
    "bte":      "ben tre",
    "brvt":     "ba ria vung tau",
    "vung tau": "ba ria vung tau",
    "cb":       "cao bang",
    "cbb":      "cao bang",
    "dlk":      "dak lak",
    "dnai":     "dong nai",
    "dt":       "dong thap",
    "gl":       "gia lai",
    "hb":       "hoa binh",
    "hg":       "ha giang",
    "hnam":     "ha nam",
    "ht":       "ha tinh",
    "hd":       "hai duong",
    "hy":       "hung yen",
    "hue":      "hue",
    "tth":      "hue",
    "kg":       "kien giang",
    "kh":       "khanh hoa",
    "kont":     "kon tum",
    "la":       "long an",
    "ld":       "lam dong",
    "ls":       "lang son",
    "lc":       "lai chau",
    "lci":      "lao cai",
    "mb":       "moc bai",
    "na":       "nghe an",
    "nb":       "ninh binh",
    "nt":       "ninh thuan",
    "pt":       "phu tho",
    "py":       "phu yen",
    "qb":       "quang binh",
    "qn":       "quang nam",
    "qng":      "quang ngai",
    "qni":      "quang ngai",
    "qt":       "quang tri",
    "sc":       "soc trang",
    "sl":       "son la",
    "tb":       "thai binh",
    "tg":       "tien giang",
    "th":       "thanh hoa",
    "tn":       "thai nguyen",
    "tv":       "tra vinh",
    "vl":       "vinh long",
    "vp":       "vinh phuc",
    "yb":       "yen bai",
}

# Unit-type prefix tokens to expand (these are structural, not place names)
UNIT_TYPE_MAP: dict[str, str] = {
    "tp":   "thanh pho",
    "q":    "quan",
    "h":    "huyen",
    "p":    "phuong",
    "x":    "xa",
    "tx":   "thi xa",
    "tt":   "thi tran",
    "kp":   "khu pho",
    "dg":   "duong",
    "d":    "duong",
    "hxh":  "hem",
    "hem":  "hem",
    "ql":   "quoc lo",
    "tl":   "tinh lo",
    # Numbered districts: q1 → quan 1, q.1 → quan 1, etc.
    **{f"q{i}":  f"quan {i}" for i in range(1, 13)},
    **{f"q.{i}": f"quan {i}" for i in range(1, 13)},
}

# Structural tokens that should be stripped before place-name matching
# (they add noise to trigram similarity)
STRUCTURAL_TOKENS = {
    "thanh", "pho", "quan", "huyen", "phuong", "xa", "thi", "xa",
    "tran", "khu", "duong", "hem", "ngo", "ngach",
}


def normalize(text: str) -> str:
    """Full normalization pipeline → returns lowercase ascii string."""
    # Unicode NFC + strip zero-width chars
    text = unicodedata.normalize("NFC", text)
    text = re.sub(r"[\u200b\u200c\u200d\ufeff]", "", text)

    # Strip apostrophes / special punctuation used in ethnic minority place names
    # e.g. "Ea H'leo" → "Ea Hleo", so trigrams align with user input "eahleo"
    text = re.sub(r"['\u2019\u2018\u02bc\u0060]", "", text)

    # ASCII-strip accents
    text = to_ascii(text)

    # Collapse whitespace, replace separators
    text = re.sub(r"[,/\-–—]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()

    # Expand province shorthands (whole-word, longer first to avoid partial matches)
    for abbr in sorted(PROVINCE_MAP, key=len, reverse=True):
        text = re.sub(rf"\b{re.escape(abbr)}\b", PROVINCE_MAP[abbr], text)

    # Expand unit-type shorthands (whole-word)
    for abbr in sorted(UNIT_TYPE_MAP, key=len, reverse=True):
        text = re.sub(rf"\b{re.escape(abbr)}\b", UNIT_TYPE_MAP[abbr], text)

    return re.sub(r"\s+", " ", text).strip()


def strip_structural(text: str) -> str:
    """Remove structural prefix tokens to improve place-name trigram similarity."""
    words = [w for w in text.split() if w not in STRUCTURAL_TOKENS]
    return " ".join(words) if words else text


def build_query_variants(normalized: str) -> list[str]:
    """
    Build all useful search variants from a normalized address string.

    Since _best_match uses GREATEST(similarity(...)) in a single CTE,
    adding more variants costs zero extra SQL round-trips — only minor
    CPU in the DB. No hard cap; just deduplication.

    Order: full string first, then stripped, then sliding windows
    from smallest to largest (individual tokens → pairs → triples → 4-grams),
    so the most specific matches appear first in the ranked result.
    """
    tokens = normalized.split()
    stripped = strip_structural(normalized)

    variants: list[str] = [normalized]
    if stripped and stripped != normalized:
        variants.append(stripped)

    stop = STRUCTURAL_TOKENS
    # 1-token: skip pure stop words and single chars
    for tok in tokens:
        if tok not in stop and len(tok) >= 2:
            variants.append(tok)

    # 2-token sliding windows
    for i in range(len(tokens) - 1):
        variants.append(f"{tokens[i]} {tokens[i+1]}")

    # 3-token sliding windows (important: "Ho Chi Minh", "Ba Ria Vung Tau")
    for i in range(len(tokens) - 2):
        variants.append(" ".join(tokens[i:i+3]))

    # 4-token sliding windows ("Ba Ria Vung Tau" edge case)
    for i in range(len(tokens) - 3):
        variants.append(" ".join(tokens[i:i+4]))

    # Deduplicate preserving order
    seen: set[str] = set()
    result: list[str] = []
    for v in variants:
        v = v.strip()
        if v and v not in seen:
            seen.add(v)
            result.append(v)

    return result


# ── Data models ───────────────────────────────────────────────────────────────

@dataclass
class UnitMatch:
    id: int
    gso_code: str
    full_name: str
    name: str
    unit_type: str
    ascii_name: str
    score: float
    parent_id: int | None = None


@dataclass
class Candidate:
    province: UnitMatch | None = None
    district: UnitMatch | None = None
    ward:     UnitMatch | None = None
    score:    float = 0.0
    reasons:  list[str] = field(default_factory=list)

    @property
    def depth(self) -> int:
        return sum(1 for u in (self.province, self.district, self.ward) if u)


@dataclass
class ResolveResult:
    raw_input:    str
    normalized:   str
    candidates:   list[Candidate]
    top:          Candidate | None
    confidence:   float
    action:       str   # 'auto_accept' | 'review' | 'low_confidence'
    era:          str   # 'pre_merger' | 'post_merger'
    house_number: str | None = None   # e.g. "6", "3b", "12/4"
    street_raw:   str | None = None   # remaining tokens after admin units removed


# ── SQL validity helper ────────────────────────────────────────────────────────

def _validity_clause(as_of_date: date) -> str:
    """
    Return a SQL fragment that filters admin_units rows valid on `as_of_date`.
    A row is valid when:  valid_from <= as_of_date  AND  (valid_to IS NULL OR valid_to >= as_of_date)
    """
    d = as_of_date.isoformat()
    return f"valid_from <= '{d}' AND (valid_to IS NULL OR valid_to >= '{d}')"


def _row_to_unit(row: dict) -> UnitMatch:
    return UnitMatch(
        id=row["id"], gso_code=row["gso_code"], full_name=row["full_name"],
        name=row["name"], unit_type=row["unit_type"], ascii_name=row["ascii_name"],
        score=float(row["score"]), parent_id=row["parent_id"],
    )


# ── Retrieval functions ───────────────────────────────────────────────────────

def _best_match(
    conn: psycopg.Connection,
    variants: list[str],
    unit_type: str,
    parent_id: int | None,
    threshold: float = 0.1,
    top_k: int = 5,
    as_of_date: date | None = None,
) -> list[UnitMatch]:
    """
    Search all variants in exactly TWO SQL queries (alias + trgm),
    regardless of how many variants are provided.

    - Alias query:  alias IN (v1, v2, ...) → highest weight wins
    - Trigram query: GREATEST(sim(name, v1), sim(name, v2), ...) computed in a CTE

    parent_id=None → province (global).
    parent_id set  → district/ward restricted to that parent.
    as_of_date     → defaults to today; used to select pre/post-merger rows.
    """
    if as_of_date is None:
        as_of_date = date.today()

    validity = _validity_clause(as_of_date)
    lower_variants = [v.lower().strip() for v in variants]
    seen: dict[int, UnitMatch] = {}

    parent_clause_au = "AND au.parent_id = %s" if parent_id is not None else ""
    parent_clause    = "AND parent_id = %s"    if parent_id is not None else ""
    parent_params    = [parent_id] if parent_id is not None else []

    # ── Query 1: Alias exact match (IN list) ─────────────────────────────────
    placeholders = ", ".join(["%s"] * len(lower_variants))
    alias_sql = f"""
        SELECT au.id, au.gso_code, au.full_name, au.name, au.unit_type,
               au.ascii_name, au.parent_id, MAX(a.weight) AS score
        FROM aliases a
        JOIN admin_units au ON au.id = a.unit_id
        WHERE a.alias IN ({placeholders})
          AND au.unit_type = %s
          AND au.{validity}
          {parent_clause_au}
        GROUP BY au.id, au.gso_code, au.full_name, au.name, au.unit_type,
                 au.ascii_name, au.parent_id
        ORDER BY score DESC
        LIMIT %s
    """
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(alias_sql, lower_variants + [unit_type] + parent_params + [top_k])
        for row in cur.fetchall():
            u = _row_to_unit(row)
            seen[u.id] = u

    # ── Query 2: Trigram via GREATEST across all variants (CTE) ──────────────
    greatest = "GREATEST(" + ", ".join(["similarity(ascii_name, %s)"] * len(lower_variants)) + ")"
    trgm_sql = f"""
        WITH scored AS (
            SELECT id, gso_code, full_name, name, unit_type, ascii_name, parent_id,
                   {greatest} AS score
            FROM admin_units
            WHERE unit_type = %s
              AND {validity}
              {parent_clause}
        )
        SELECT * FROM scored
        WHERE score > %s
        ORDER BY score DESC
        LIMIT %s
    """
    trgm_params = lower_variants + [unit_type] + parent_params + [threshold, top_k]
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(trgm_sql, trgm_params)
        for row in cur.fetchall():
            u = _row_to_unit(row)
            if u.id not in seen or u.score > seen[u.id].score:
                seen[u.id] = u

    ranked = sorted(seen.values(), key=lambda u: u.score, reverse=True)
    return ranked[:top_k]


# ── Scoring ───────────────────────────────────────────────────────────────────

def _score_candidate(c: Candidate) -> tuple[float, list[str]]:
    """
    Compute final score and human-readable reason list for a candidate.

    score = province_sim × W_PROVINCE
          + district_sim × W_DISTRICT  (if found)
          + ward_sim     × W_WARD      (if found)
          + BONUS_DISTRICT             (if district found within province)
          + BONUS_WARD                 (if ward found within district)
    """
    score = 0.0
    reasons: list[str] = []

    if c.province:
        score += c.province.score * W_PROVINCE
        reasons.append(f"province={c.province.name} ({c.province.score:.2f})")

    if c.district:
        score += c.district.score * W_DISTRICT + BONUS_DISTRICT
        reasons.append(f"district={c.district.name} ({c.district.score:.2f})")

    if c.ward:
        score += c.ward.score * W_WARD + BONUS_WARD
        reasons.append(f"ward={c.ward.name} ({c.ward.score:.2f})")

    return round(score, 3), reasons


# ── House number & street extraction ─────────────────────────────────────────

# Matches: "so 6", "so.6", "số 3b", "s6", "s.10", "#12", "12/4a"
_HOUSE_RE = re.compile(
    r"""
    (?:                         # prefix forms
        \bso\.?\s*              #  so / so. / so<space>
      | \bs\.?\s*               #  s / s.
      | \#\s*                   #  #
    )?
    (\d+[a-z]?(?:[/\-]\d+[a-z]?)*)   # the actual number, e.g. 6 / 3b / 12/4a
    """,
    re.VERBOSE | re.IGNORECASE,
)

# Tokens that are purely structural and should always be stripped from street
_STREET_NOISE = STRUCTURAL_TOKENS | {
    "so", "s", "db", "dl",           # common abbreviation fragments alone
}

# Street abbreviation expansions (applied to the raw street string for display)
_STREET_ABBR: dict[str, str] = {
    "db":   "Dien Bien",
    "dl":   "Dai Lo",
    "ql":   "Quoc Lo",
    "tl":   "Tinh Lo",
    "nt":   "Nguyen Trai",
    "ck":   "Cach Mang",
    "le":   "Le",
    "ng":   "Nguyen",
}


def extract_house_number(normalized: str) -> tuple[str | None, str]:
    """
    Pull the house/plot number out of a normalized address string.
    Returns (house_number, text_with_number_removed).

    Examples:
      "so 6 db phu thi tran ..."  → ("6", "db phu thi tran ...")
      "12 b nguyen hue"           → ("12b", "nguyen hue")
      "duong so 5"                → ("5", "duong")
    """
    best: re.Match | None = None
    best_len = 0

    for m in _HOUSE_RE.finditer(normalized):
        num = m.group(1)
        if num and len(m.group(0).strip()) > best_len:
            best = m
            best_len = len(m.group(0).strip())

    if not best:
        return None, normalized

    house_number = best.group(1).lower()
    # Remove the matched span from the string
    cleaned = (normalized[: best.start()] + " " + normalized[best.end() :]).strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    return house_number, cleaned


def extract_street(
    normalized_no_number: str,
    top: "Candidate | None",
) -> str | None:
    """
    Extract the street/road name by removing all identified admin-unit tokens
    and structural prefixes from the normalized string.

    What remains after stripping province + district + ward tokens is treated
    as the street address line.
    """
    text = normalized_no_number

    # Collect all token sets to remove: admin unit ascii names + structural words
    tokens_to_remove: set[str] = set(STRUCTURAL_TOKENS)
    if top:
        for unit in (top.province, top.district, top.ward):
            if unit:
                name_tokens = unit.ascii_name.lower().split()
                for tok in name_tokens:
                    tokens_to_remove.add(tok)
                # Also add the no-space concatenation (e.g. "eahleo" for "Ea Hleo")
                tokens_to_remove.add("".join(name_tokens))

    remaining = [
        tok for tok in text.split()
        if tok not in tokens_to_remove and len(tok) > 1
    ]

    if not remaining:
        return None

    street = " ".join(remaining)

    # Expand common abbreviations for readability
    parts = street.split()
    expanded = [_STREET_ABBR.get(p, p) for p in parts]
    return " ".join(expanded)


# ── Confidence calibration ────────────────────────────────────────────────────

# Maximum possible score by era
# pre_merger:  province + district + ward full depth = 10+8+6+6+10 = 40
# post_merger: province + ward (no district)         = 10+6+10     = 26
_MAX_SCORE_PRE  = W_PROVINCE + W_DISTRICT + BONUS_DISTRICT + W_WARD + BONUS_WARD
_MAX_SCORE_POST = W_PROVINCE + W_WARD + BONUS_WARD


def calibrate_confidence(
    candidates: list[Candidate],
    era: str = "post_merger",
) -> tuple[float, str]:
    if not candidates:
        return 0.0, "low_confidence"

    top = candidates[0]
    max_score = _MAX_SCORE_PRE if era == "pre_merger" else _MAX_SCORE_POST
    raw = min(top.score / max_score, 1.0)

    # Penalise ambiguity: if runner-up is very close, we're less certain
    if len(candidates) > 1:
        gap = top.score - candidates[1].score
        if gap < 3.0:
            raw *= 0.85

    # Penalise shallow depth
    # post-merger max depth is 2 (province+ward); pre-merger max is 3
    max_depth = 2 if era == "post_merger" else 3
    if top.depth == 1:
        raw *= 0.70
    elif top.depth < max_depth:
        raw *= 0.88

    raw = round(min(raw, 1.0), 3)

    if raw >= 0.80:
        action = "auto_accept"
    elif raw >= 0.60:
        action = "review"
    else:
        action = "low_confidence"

    return raw, action


# ── Top-down cascade resolver ─────────────────────────────────────────────────

def _resolve_single_era(
    raw_input: str,
    conn: psycopg.Connection,
    as_of_date: date,
) -> ResolveResult:
    """
    Resolve against one specific era (determined by as_of_date).
    Internal — callers should use resolve() which handles auto-detection.

    Pre-merger  (as_of_date <  2025-07-01): Province → District → Ward  (3-tier)
    Post-merger (as_of_date >= 2025-07-01): Province → Ward directly    (2-tier)
    """
    era = "post_merger" if as_of_date >= MERGER_DATE else "pre_merger"

    normalized = normalize(raw_input)

    # ── Extract house number first (before admin-unit search) ────────────────
    house_number, normalized_no_number = extract_house_number(normalized)

    variants = build_query_variants(normalized_no_number)

    # ── Step 1: Province ──────────────────────────────────────────────────────
    # Positional hint: in Vietnamese addresses the last comma-segment is almost
    # always the province.  Try that segment first with a tighter threshold;
    # if it produces a high-confidence match, use it as the sole province anchor
    # so the cascade doesn't wander to unrelated provinces.
    raw_segs = [s.strip() for s in raw_input.split(",")]
    province_anchor: UnitMatch | None = None
    if len(raw_segs) >= 2:
        last_seg = normalize(raw_segs[-1])
        if last_seg:
            last_variants = build_query_variants(last_seg)
            anchor_hits = _best_match(
                conn, last_variants, "province",
                parent_id=None, threshold=0.3, top_k=3,
                as_of_date=as_of_date,
            )
            if anchor_hits and anchor_hits[0].score >= 0.5:
                province_anchor = anchor_hits[0]

    if province_anchor:
        # High-confidence positional match: use only this province
        provinces = [province_anchor]
    else:
        provinces = _best_match(
            conn, variants, "province",
            parent_id=None, threshold=0.1, top_k=5,
            as_of_date=as_of_date,
        )

    candidates: list[Candidate] = []

    if not provinces:
        return ResolveResult(
            raw_input=raw_input, normalized=normalized,
            candidates=[], top=None, confidence=0.0, action="low_confidence",
            era=era, house_number=house_number, street_raw=None,
        )

    # ── Steps 2+: cascade down for each province candidate ───────────────────
    for prov in provinces[:3]:

        # Remove province tokens from sub-queries to avoid false-positive matches
        prov_tokens = set(prov.ascii_name.lower().split())
        sub_variants = [
            v for v in variants
            if not set(v.lower().split()).issubset(prov_tokens)
        ]
        if not sub_variants:
            sub_variants = variants

        if era == "post_merger":
            # ── Post-merger: Province → Ward directly (no districts) ─────────
            ward_variants = sub_variants
            wards = _best_match(
                conn, ward_variants, "ward",
                parent_id=prov.id, threshold=0.08, top_k=5,
                as_of_date=as_of_date,
            )

            if not wards:
                c = Candidate(province=prov)
                c.score, c.reasons = _score_candidate(c)
                candidates.append(c)
                continue

            for ward in wards[:3]:
                c = Candidate(province=prov, district=None, ward=ward)
                c.score, c.reasons = _score_candidate(c)
                candidates.append(c)

            # Also keep province-only for comparison
            c2 = Candidate(province=prov)
            c2.score, c2.reasons = _score_candidate(c2)
            candidates.append(c2)

        else:
            # ── Pre-merger: Province → District → Ward ────────────────────────
            districts = _best_match(
                conn, sub_variants, "district",
                parent_id=prov.id, threshold=0.08, top_k=5,
                as_of_date=as_of_date,
            )

            if not districts:
                c = Candidate(province=prov)
                c.score, c.reasons = _score_candidate(c)
                candidates.append(c)
                continue

            for dist in districts[:3]:
                dist_tokens = set(dist.ascii_name.lower().split())
                ward_variants = [
                    v for v in sub_variants
                    if not set(v.lower().split()).issubset(dist_tokens)
                ]
                if not ward_variants:
                    ward_variants = sub_variants

                wards = _best_match(
                    conn, ward_variants, "ward",
                    parent_id=dist.id, threshold=0.08, top_k=5,
                    as_of_date=as_of_date,
                )

                if not wards:
                    c = Candidate(province=prov, district=dist)
                    c.score, c.reasons = _score_candidate(c)
                    candidates.append(c)
                    continue

                for ward in wards[:3]:
                    c = Candidate(province=prov, district=dist, ward=ward)
                    c.score, c.reasons = _score_candidate(c)
                    candidates.append(c)

            # Also keep province+best-district for comparison
            c2 = Candidate(province=prov, district=districts[0])
            c2.score, c2.reasons = _score_candidate(c2)
            candidates.append(c2)

    # Sort by score desc, deduplicate by (province, district, ward) key
    candidates.sort(key=lambda c: c.score, reverse=True)
    seen_keys: set[tuple] = set()
    deduped: list[Candidate] = []
    for c in candidates:
        key = (
            c.province.id if c.province else None,
            c.district.id if c.district else None,
            c.ward.id     if c.ward     else None,
        )
        if key not in seen_keys:
            seen_keys.add(key)
            deduped.append(c)

    top5 = deduped[:5]
    confidence, action = calibrate_confidence(top5, era=era)

    # ── Extract street name from remaining tokens ─────────────────────────────
    street_raw = extract_street(normalized_no_number, top5[0] if top5 else None)

    return ResolveResult(
        raw_input=raw_input,
        normalized=normalized,
        candidates=top5,
        top=top5[0] if top5 else None,
        confidence=confidence,
        action=action,
        era=era,
        house_number=house_number,
        street_raw=street_raw,
    )


PRE_MERGER_SNAPSHOT = date(2025, 6, 30)


def resolve(
    raw_input: str,
    conn: psycopg.Connection,
    as_of_date: date | None = None,
) -> ResolveResult:
    """
    Resolve a raw Vietnamese address string.

    If as_of_date is given, resolves strictly against that era.

    If as_of_date is None (default), uses auto-detection:
      1. Try post-merger (today).
      2. Also try pre-merger (2025-06-30).
      3. Return whichever has higher confidence — unless post-merger wins
         by at least MIN_ERA_MARGIN (avoid flip-flopping on ambiguous inputs).

    This lets users enter old province names ("Bình Phước", "Bình Dương") and
    still get correct results even after the 2025 restructuring.
    """
    if as_of_date is not None:
        return _resolve_single_era(raw_input, conn, as_of_date)

    # ── Auto-detect ───────────────────────────────────────────────────────────
    MIN_ERA_MARGIN = 0.05   # pre-merger must beat post-merger by this much to win

    post = _resolve_single_era(raw_input, conn, date.today())
    pre  = _resolve_single_era(raw_input, conn, PRE_MERGER_SNAPSHOT)

    # Prefer post-merger unless pre-merger is meaningfully more confident
    if pre.confidence > post.confidence + MIN_ERA_MARGIN:
        return pre
    return post
