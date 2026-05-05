# Vietnamese Address Detection — Build Context & Progress Log

> **Last updated:** 2026-05-05  
> **Status:** Active development — post-merger (2025) data fully operational

---

## Project Goal

Build a **production-grade Vietnamese address detection system** that handles:
- Acronyms and abbreviations (`NB` → Ninh Bình, `tphcm` → Hồ Chí Minh)
- No-accent / noisy input (`dak lak`, `eahleo`, `quangr binh`)
- Spelling mistakes and OCR corruption
- Slight gaps in information
- **Both pre-merger (≤ 2025-06-30) and post-merger (≥ 2025-07-01) addresses**

---

## System Architecture

```
Raw Input
   │
   ▼
Normalize           (unicode, apostrophes, abbreviation expansion, accent-strip)
   │
   ▼
House Number        (regex: "so 6", "s.10", "#12", "12/4a")
Extract
   │
   ▼
Positional          last comma-segment → try as province anchor first
Province Hint       (e.g. "...., NB" → Ninh Bình with score 1.0)
   │
   ▼
Auto-Era Detect     try post-merger (today) AND pre-merger (2025-06-30)
                    → pick higher confidence result (+5% margin favoring post-merger)
   │
   ├── POST-MERGER (≥ 2025-07-01)          PRE-MERGER (≤ 2025-06-30)
   │   Province → Ward (2-tier)             Province → District → Ward (3-tier)
   │
   ▼
Fuzzy Cascade       GREATEST(trigram_sim) CTE + alias IN-list → 2 SQL queries/level
   │
   ▼
Score & Rank        province×10 + district×8 + ward×6 + depth bonuses
   │
   ▼
Confidence          max_score varies by era (40 pre / 26 post)
Calibrate           penalise ambiguity + shallow depth
   │
   ▼
Street Extract      remaining tokens after admin units + house number removed
   │
   ▼
ResolveResult       {province, district?, ward, house_number, street_raw, era, confidence}
```

---

## Data Sources

| Source | Used For | Status |
|--------|----------|--------|
| [daohoangson/dvhcvn](https://github.com/daohoangson/dvhcvn) | Pre-merger data (63 provinces, 696 districts, 10 047 wards) as of 01/03/2025 | ✅ Loaded |
| [ThangLeQuoc/vietnamese-provinces-database](https://github.com/ThangLeQuoc/vietnamese-provinces-database) | Post-merger data — decree 19/2025/QĐ-TTg effective **2025-07-01** (34 provinces, 3 321 wards) | ✅ Loaded |

---

## Database: PostgreSQL 16 + pgvector

### Connection
```
Host:     localhost:5432
DB:       vn_address
User:     postgres
Password: postgres
Container: vn_address_pg  (docker-compose)
```

### Unit counts by era
```
era          | unit_type | count
-------------|-----------|-------
post_merger  | province  |    34
post_merger  | ward      |  3321
pre_merger   | district  |   696
pre_merger   | province  |    63
pre_merger   | ward      | 10047
```

### Key schema design
- `(gso_code, valid_from)` — composite unique key (GSO codes are recycled post-merger)
- `valid_from` / `valid_to` — date range for validity filtering
- `era` — `'pre_merger'` | `'post_merger'` (fast filter column)
- `ascii_name` — accent-stripped, apostrophe-cleaned for trigram indexing
- `parent_id` — links ward→district→province (pre-merger) or ward→province (post-merger)
- Indexes: GIN trigram on `ascii_name`/`full_name`, HNSW for embeddings, BTREE on era/parent/dates

### Migrations
```
001_extensions.sql   — pg_trgm, vector, unaccent
002_schema.sql       — core tables: admin_units, admin_unit_mergers, aliases, streets
003_indexes.sql      — GIN trgm, HNSW, BTREE indexes
004_post_merger_schema.sql — drop UNIQUE(gso_code), add UNIQUE(gso_code, valid_from), add era column
```

---

## Project File Structure

```
detect-vietnamese-address/
├── docker-compose.yml          PostgreSQL 16 + pgvector container
├── .env.example                DATABASE_URL and other env vars
├── migrations/
│   ├── 001_extensions.sql
│   ├── 002_schema.sql
│   ├── 003_indexes.sql
│   └── 004_post_merger_schema.sql
├── etl/
│   ├── requirements.txt        httpx, psycopg[binary], unidecode, fastapi, uvicorn, etc.
│   ├── fetch_dvhcvn.py         Download dvhcvn sorted.json / dvhcvn.json (ETag-cached)
│   ├── transform.py            Parse sorted.json → AdminUnit dataclasses
│   ├── load_to_pg.py           UPSERT pre-merger data into admin_units
│   ├── load_post_merger.py     Fetch ThangLeQuoc JSON → insert 34 provinces + 3321 wards
│   ├── seed_aliases.py         Province abbreviations, unit-type shorthands, q1-q12
│   ├── verify_counts.py        Sanity checks after ETL
│   └── run_all.sh              Full pipeline orchestrator
├── api/
│   ├── __init__.py
│   ├── resolver.py             Core resolution logic (see Architecture above)
│   ├── main.py                 FastAPI server — /resolve, /search, /health
│   └── ui/
│       └── index.html          Web UI for testing
└── data/                       Downloaded raw JSON files (gitignored)
```

---

## How to Run

### 1. Start database
```bash
cd detect-vietnamese-address
docker compose up -d
```

### 2. Install Python dependencies
```bash
python3 -m venv .venv
.venv/bin/pip install -r etl/requirements.txt
```

### 3. Run migrations
```bash
docker exec -i vn_address_pg psql -U postgres -d vn_address < migrations/001_extensions.sql
docker exec -i vn_address_pg psql -U postgres -d vn_address < migrations/002_schema.sql
docker exec -i vn_address_pg psql -U postgres -d vn_address < migrations/003_indexes.sql
docker exec -i vn_address_pg psql -U postgres -d vn_address < migrations/004_post_merger_schema.sql
```

### 4. Load data (all eras)
```bash
# Pre-merger (dvhcvn — 63 provinces)
.venv/bin/python etl/fetch_dvhcvn.py
.venv/bin/python etl/transform.py    # writes data/admin_units.json
.venv/bin/python etl/load_to_pg.py
.venv/bin/python etl/seed_aliases.py

# Post-merger (ThangLeQuoc — 34 provinces)
.venv/bin/python etl/load_post_merger.py
```

### 5. Start API server
```bash
.venv/bin/uvicorn api.main:app --reload --port 8000
```

### 6. Open UI
Visit: http://localhost:8000

### API usage
```bash
# Resolve (auto-detects era)
curl -X POST http://localhost:8000/resolve \
  -H "Content-Type: application/json" \
  -d '{"address": "nhà hàng minh đức, nam lý, NB"}'

# Force pre-merger (for old addresses)
curl -X POST http://localhost:8000/resolve \
  -H "Content-Type: application/json" \
  -d '{"address": "dong da ha noi", "as_of_date": "2025-06-30"}'

# Health / counts by era
curl http://localhost:8000/health

# Fuzzy search (autocomplete)
curl "http://localhost:8000/search?q=binh+phuoc&type=province"
```

---

## API Response Shape

```json
{
  "raw_input": "nhà hàng minh đức, nam lý, NB",
  "normalized": "nha hang minh duc nam ly ninh binh",
  "confidence": 0.85,
  "action": "auto_accept",
  "era": "post_merger",
  "as_of_date": "2026-05-05",
  "house_number": null,
  "street_raw": "nha hang minh duc",
  "top": {
    "score": 26.0,
    "province": { "full_name": "Tỉnh Ninh Bình", "score": 1.0, ... },
    "district": null,
    "ward":     { "full_name": "Xã Nam Lý", "score": 0.85, ... }
  },
  "candidates": [ ... ]
}
```

**`action` values:**
| Value | Meaning | Threshold |
|-------|---------|-----------|
| `auto_accept` | High confidence, use directly | ≥ 80% |
| `review` | Moderate confidence, human check advised | 60–79% |
| `low_confidence` | Weak match, likely needs correction | < 60% |

**`era` values:**
| Value | Period | Structure |
|-------|--------|-----------|
| `post_merger` | 2025-07-01 → present | 34 provinces, Province → Ward |
| `pre_merger` | up to 2025-06-30 | 63 provinces, Province → District → Ward |

---

## Resolver Key Design Decisions

### 1. Strict top-down cascade
Province is always identified first, then district (pre-merger only), then ward — each level restricted to `parent_id` of the level above. Guarantees hierarchy validity without post-hoc checks.

### 2. Auto-era detection
Both eras are tried and the higher-confidence result wins (pre-merger must beat post-merger by ≥5% to avoid flip-flopping). This means users can enter old province names ("Bình Phước", "Bình Dương") and still get correct results even after 2025 restructuring.

### 3. Positional province anchoring
When input has commas, the **last comma-segment** is tried as province with threshold 0.5. If matched, it becomes the sole province anchor for the cascade. This captures the Vietnamese address convention of writing province last.

### 4. Province-token filtering
Tokens belonging to an identified province are removed from district/ward variant search to prevent cross-level false positives (e.g. "lak" in "dak lak" matching "Huyện Lắk").

### 5. Two SQL queries per level
`GREATEST(similarity(ascii_name, v1), sim(v2), ...)` in a single CTE + alias `IN (...)` list — no matter how many variants are generated, it costs exactly 2 DB round-trips per level.

### 6. Apostrophe stripping
Ethnic minority place names like `Ea H'leo` → `ascii_name = "ea hleo"` so trigrams align with user input `eahleo`.

---

## Province Abbreviation Map (Key additions)

Beyond common ones (`tphcm`, `hn`, `dn`), the following were added to handle real-world inputs:

| Code | Province |
|------|---------|
| `nb` | Ninh Bình |
| `bp` | Bình Phước |
| `bg` | Bắc Giang |
| `bn` | Bắc Ninh |
| `bt` | Bình Thuận |
| `na` | Nghệ An |
| `qb` | Quảng Bình |
| `qt` | Quảng Trị |
| `th` | Thanh Hóa |
| `tn` | Thái Nguyên |
| `hy` | Hưng Yên |
| `hb` | Hòa Bình |
| `ld` | Lâm Đồng |
| `vl` | Vĩnh Long |
| `ag` | An Giang |
| `kg` | Kiên Giang |
| `tg` | Tiền Giang |
| `dlk` | Đắk Lắk |
| ... | (50 total in PROVINCE_MAP) |

---

## Known Limitations / Pending Work

| Item | Status |
|------|--------|
| `admin_unit_mergers` table — old code → new code mappings | ⬜ Not populated |
| Embeddings (`pgvector`) — semantic fallback for very noisy input | ⬜ Not loaded |
| Street database (OSM / Vietnam Post) | ⬜ Not loaded |
| Postal codes | ⬜ Not loaded |
| OCR confusion pairs (`ocr_confusions` table) | ⬜ Not seeded |
| Bulk batch API endpoint | ⬜ Not implemented |
| Rate limiting / auth | ⬜ Not implemented |

---

## Key Bugs Fixed During Development

| Bug | Root Cause | Fix |
|-----|-----------|-----|
| `Ea H'leo` not matching `eahleo` | Apostrophe in `ascii_name` breaking trigrams | Strip `'` in `transform.py` + `normalize()` |
| Province tokens bleeding into district/ward search | `lak` from `dak lak` matching `Huyện Lắk` | Province-token filter on sub-variants |
| Central cities (Hà Nội) classified as district | dvhcvn top-level node had no explicit province type | Force `unit_type='province'` for parentless nodes |
| `Unknown prefix: 'Thị Trấn'` | Case-sensitive prefix matching | Lowercase prefix before matching |
| Post-merger province code collision | `UNIQUE(gso_code)` constraint; codes recycled | Changed to `UNIQUE(gso_code, valid_from)` |
| Old address "Bình Phước" matched wrong province | Post-merger: Bình Phước doesn't exist; fell back to unrelated ward | Auto-era detection (try both, pick higher confidence) |
| `NB` (Ninh Bình) not recognized | Not in PROVINCE_MAP | Added 50 plate/common abbreviations |
| Last token province ignored | No positional heuristic | Positional anchoring: last comma-segment → province anchor |

---

## Test Cases

```bash
# Post-merger (default today)
curl -sX POST localhost:8000/resolve -H 'Content-Type: application/json' \
  -d '{"address":"Đoàn Kết, Xã Đoài Phương, Hà Nội"}'
# → ERA: post_merger | Hà Nội → Xã Đoài Phương

# Pre-merger auto-detected (Bình Phước merged away)
curl -sX POST localhost:8000/resolve -H 'Content-Type: application/json' \
  -d '{"address":"Trung tâm y tế thị xã PHƯỚC LONG, BÌNH PHƯỚC"}'
# → ERA: pre_merger | Tỉnh Bình Phước → Thị xã Phước Long → Phường Long Phước

# Province abbreviation + positional anchor
curl -sX POST localhost:8000/resolve -H 'Content-Type: application/json' \
  -d '{"address":"nhà hàng minh đức, nam lý, NB"}'
# → ERA: post_merger | Tỉnh Ninh Bình → Xã Nam Lý | street: nha hang minh duc

# No-accent + ethnic minority name
curl -sX POST localhost:8000/resolve -H 'Content-Type: application/json' \
  -d '{"address":"so 6 db phu thi tran eadrang huyen eahleo dak lak"}'
# → ERA: pre_merger | Đắk Lắk → Huyện Ea H'\''leo → Thị trấn Ea Drăng

# Force pre-merger explicitly
curl -sX POST localhost:8000/resolve -H 'Content-Type: application/json' \
  -d '{"address":"dong da ha noi", "as_of_date":"2025-06-30"}'
# → ERA: pre_merger | Hà Nội → Quận Đống Đa
```
