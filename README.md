# Vietnamese Address Resolution System — Production-Grade Architecture Plan

A detailed roadmap and architecture plan for building a scalable Vietnamese address normalization, retrieval, ranking, and validation system using:

- datasets
- indexed databases
- fuzzy retrieval
- hierarchy validation
- OCR repair
- abbreviation expansion
- candidate graph ranking
- optional LLM disambiguation

This document is designed for:
- production systems
- logistics
- shipping normalization
- OCR-corrupted addresses
- noisy user-generated address input

---

# 1. High-Level Goal

Convert:

```text
messy / abbreviated / OCR-corrupted Vietnamese addresses
```

into:

```text
validated structured addresses
```

with:
- confidence scoring
- hierarchy validation
- explainability
- uncertainty handling

---

# 2. Core Architectural Principle

DO NOT:

```text
raw address
↓
LLM
↓
final answer
```

Instead:

```text
raw address
↓
normalization
↓
candidate retrieval
↓
candidate ranking
↓
constraint validation
↓
LLM disambiguation (optional)
↓
confidence scoring
↓
final address
```

---

# 3. Recommended System Architecture

```text
Input Address
↓
Unicode Cleanup
↓
Normalization
↓
OCR Repair
↓
Abbreviation Expansion
↓
Tokenization
↓
Candidate Retrieval
↓
Candidate Graph Generation
↓
Hierarchy Validation
↓
Probabilistic Ranking
↓
LLM Disambiguation
↓
Geospatial Validation
↓
Confidence Calibration
↓
Final Output
```

---

# 4. System Components

| Component | Purpose |
|---|---|
| Normalization Engine | Clean raw text |
| OCR Repair Engine | Repair corrupted tokens |
| Alias Engine | Expand abbreviations |
| Retrieval Engine | Fetch candidates from DB |
| Candidate Generator | Build possible address graphs |
| Constraint Engine | Validate hierarchy |
| Ranking Engine | Score candidates |
| LLM Resolver | Resolve ambiguity |
| Confidence Engine | Predict correctness |
| Feedback Loop | Learn from corrections |

---

# 5. Recommended Tech Stack

| Layer | Recommended |
|---|---|
| Database | PostgreSQL |
| Fuzzy Search | pg_trgm |
| Fulltext | OpenSearch / ElasticSearch |
| Vector Search | pgvector |
| Cache | Redis |
| ML Ranking | XGBoost / LightGBM |
| LLM | GPT-4o-mini / local LLM |
| API | NestJS |
| Worker Queue | BullMQ |
| Storage | S3 / MinIO |

---

# 6. Database Design

---

# 6.1 Administrative Units Table

```sql
CREATE TABLE admin_units (
    id BIGSERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    ascii_name TEXT NOT NULL,
    type TEXT NOT NULL,
    parent_id BIGINT,
    province_code TEXT,
    district_code TEXT,
    ward_code TEXT,
    valid_from DATE,
    valid_to DATE,
    aliases JSONB,
    embedding VECTOR(768)
);
```

---

# 6.2 Streets Table

```sql
CREATE TABLE streets (
    id BIGSERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    ascii_name TEXT NOT NULL,
    ward_id BIGINT,
    district_id BIGINT,
    province_id BIGINT,
    aliases JSONB,
    popularity_score FLOAT DEFAULT 0
);
```

---

# 6.3 Alias Table

```sql
CREATE TABLE aliases (
    id BIGSERIAL PRIMARY KEY,
    alias TEXT NOT NULL,
    canonical TEXT NOT NULL,
    type TEXT,
    weight FLOAT DEFAULT 1.0
);
```

---

# 6.4 OCR Confusion Table

```sql
CREATE TABLE ocr_confusions (
    id BIGSERIAL PRIMARY KEY,
    wrong_token TEXT,
    corrected_token TEXT,
    frequency INT DEFAULT 1
);
```

---

# 6.5 Human Correction Logs

```sql
CREATE TABLE correction_logs (
    id BIGSERIAL PRIMARY KEY,
    raw_input TEXT,
    predicted_address TEXT,
    corrected_address TEXT,
    created_at TIMESTAMP DEFAULT now()
);
```

---

# 7. PostgreSQL Extensions

---

# 7.1 Trigram Search

```sql
CREATE EXTENSION pg_trgm;
```

---

# 7.2 Vector Search

```sql
CREATE EXTENSION vector;
```

---

# 8. Important Database Indexes

---

# 8.1 Trigram Index

```sql
CREATE INDEX idx_admin_ascii_trgm
ON admin_units
USING gin (ascii_name gin_trgm_ops);
```

---

# 8.2 Street Trigram Index

```sql
CREATE INDEX idx_street_ascii_trgm
ON streets
USING gin (ascii_name gin_trgm_ops);
```

---

# 8.3 Alias Index

```sql
CREATE INDEX idx_alias_lookup
ON aliases(alias);
```

---

# 9. Address Processing Pipeline

---

# 9.1 Unicode Cleanup

Normalize:

- NFC/NFD
- zero-width spaces
- homoglyphs
- smart quotes
- invisible Unicode chars

---

# 9.2 Normalization

Convert:

```text
tphcm
tp hcm
sg
sai gon
```

into:

```text
thanh pho ho chi minh
```

---

# 9.3 OCR Repair

Examples:

| Wrong | Correct |
|---|---|
| ngjeej | nghệ |
| btaan | tân |
| quangr | quảng |
| loj | lộ |

---

# 9.4 Abbreviation Expansion

Examples:

| Raw | Expanded |
|---|---|
| q | quận / quảng / quang |
| p | phường |
| tx | thị xã |
| kp | khu phố |
| dg | đường |

---

# 9.5 Tokenization

Split:
- words
- numbers
- slash patterns
- alley patterns
- administrative hints

---

# 10. Retrieval Engine

---

# IMPORTANT PRINCIPLE

DO NOT directly pick:
- one province
- one district

Retrieve TOP K candidates.

---

# 10.1 Province Retrieval

```sql
SELECT *,
       similarity(ascii_name, :query) AS score
FROM admin_units
WHERE type = 'province'
ORDER BY score DESC
LIMIT 5;
```

---

# 10.2 District Retrieval

```sql
SELECT *,
       similarity(ascii_name, :query) AS score
FROM admin_units
WHERE type = 'district'
ORDER BY score DESC
LIMIT 10;
```

---

# 10.3 Ward Retrieval

```sql
SELECT *,
       similarity(ascii_name, :query) AS score
FROM admin_units
WHERE type = 'ward'
ORDER BY score DESC
LIMIT 10;
```

---

# 10.4 Street Retrieval

```sql
SELECT *,
       similarity(ascii_name, :query) AS score
FROM streets
ORDER BY score DESC
LIMIT 20;
```

---

# 11. Candidate Graph Generation

Build all valid combinations:

```json
[
  {
    "street": "Bờ Bao Tân Thắng",
    "ward": "Sơn Kỳ",
    "district": "Tân Phú",
    "province": "Hồ Chí Minh"
  }
]
```

---

# 12. Hierarchy Validation Engine

Rules:

```text
ward ∈ district
district ∈ province
street near ward
```

Reject invalid combinations.

---

# 13. Candidate Ranking

---

# Recommended Scoring Formula

```text
final_score =
  exact_match_score * 10 +
  fuzzy_similarity * 8 +
  hierarchy_consistency * 12 +
  token_order_score * 5 +
  geo_consistency * 6 +
  alias_confidence * 4 +
  popularity_prior * 2
```

---

# 14. Recommended Fuzzy Algorithms

| Algorithm | Purpose |
|---|---|
| Levenshtein | Typo correction |
| Damerau-Levenshtein | Swapped chars |
| Jaro-Winkler | Short names |
| Trigram Cosine | OCR corruption |
| Token Set Ratio | Reordered words |
| Phonetic Match | Accent/dialect |

---

# 15. LLM Usage Strategy

---

# IMPORTANT

LLM is NOT:
- the search engine
- the database
- the ranking engine

LLM is ONLY:
- ambiguity resolver
- semantic reasoner

---

# Recommended LLM Flow

Only invoke LLM when:

```text
top1_score - top2_score < threshold
```

Examples:
- OCR-heavy input
- ambiguous abbreviations
- multiple plausible candidates

---

# 16. Recommended LLM Prompt

```text
You are a Vietnamese address resolution engine.

Raw input:
22 bờ báo btaan thang p son ky q tần phú

Retrieved candidates:
1. Bờ Bao Tân Thắng, Sơn Kỳ, Tân Phú
2. Tân Thắng, Bình Tân
3. Sơn Kỳ, Tân Phú

Hierarchy:
- Sơn Kỳ belongs to Tân Phú
- Bờ Bao Tân Thắng exists in Sơn Kỳ

Choose:
- best interpretation
- confidence
- reasoning
```

---

# 17. Confidence Calibration

Current heuristic confidence is insufficient.

Train a model using:

- candidate scores
- hierarchy quality
- OCR severity
- token completeness
- human corrections

---

# Recommended Models

- Logistic Regression
- XGBoost
- LightGBM

---

# 18. Explainability Engine

Every prediction should contain:

```json
{
  "province": {
    "candidate": "Quảng Bình",
    "score": 0.92,
    "reasons": [
      "contains dong hoi",
      "q binh fuzzy match"
    ]
  }
}
```

This is critical for:
- debugging
- operator trust
- auditing

---

# 19. Geospatial Validation

Use:
- coordinates
- province adjacency
- district clustering

Reject improbable combinations.

Example:

```text
Buôn Ma Thuột + Quảng Ninh
```

should be heavily penalized.

---

# 20. Feedback Learning Loop

Persist:

```json
{
  "raw": "...",
  "predicted": "...",
  "corrected": "..."
}
```

Use this to:
- improve aliases
- learn OCR mistakes
- build abbreviation DB
- improve ranking

---

# 21. Human Review Workflow

Low-confidence addresses should:

```text
flag for manual review
```

NOT auto-accept.

---

# 22. Recommended Confidence Thresholds

| Confidence | Action |
|---|---|
| > 0.95 | Auto accept |
| 0.80–0.95 | Accept with warning |
| 0.60–0.80 | LLM disambiguation |
| < 0.60 | Human review |

---

# 23. Batch Processing Architecture

```text
Upload XLSX
↓
Queue Job
↓
Worker Pool
↓
Normalization
↓
Retrieval
↓
Ranking
↓
Optional LLM
↓
Export XLSX
```

---

# 24. Worker Queue

Recommended:
- BullMQ
- Redis

Supports:
- retries
- rate limiting
- concurrency control

---

# 25. Caching Strategy

Cache:
- normalized tokens
- retrieval results
- LLM responses
- geocode results

Recommended:
- Redis
- request fingerprinting

---

# 26. Logging

Log:
- raw input
- normalization output
- candidates
- scores
- final prediction
- confidence
- corrections

This is critical.

---

# 27. Benchmark Dataset

Build:
- gold labeled addresses
- OCR-heavy samples
- shipping abbreviations
- historical admin units

Track:
- exact accuracy
- ward accuracy
- uncertainty precision
- hallucination rate

---

# 28. Recommended Roadmap

---

# Phase 1 — Core Retrieval System

Priority:
1. PostgreSQL + pg_trgm
2. Alias database
3. Candidate graph generation
4. Hierarchy validation

---

# Phase 2 — Better Ranking

1. Advanced fuzzy matching
2. Weighted scoring
3. Explainability engine
4. Street-level indexing

---

# Phase 3 — LLM Integration

1. Ambiguity resolver
2. OCR reasoning
3. Semantic inference
4. Final confidence fusion

---

# Phase 4 — Learning System

1. Human correction feedback
2. OCR confusion learning
3. Alias learning
4. ML confidence calibration

---

# Phase 5 — Advanced Features

1. Geospatial validation
2. Embedding retrieval
3. Semantic clustering
4. Active learning

---

# 29. Final Architectural Recommendation

The system should evolve from:

```text
regex + hardcoded heuristics
```

into:

```text
retrieval + ranking + reasoning architecture
```

Core principles:

- dataset-first
- retrieval-first
- hierarchy-aware
- uncertainty-aware
- explainable
- probabilistic
- learning-enabled
- LLM-assisted (not LLM-dependent)

This is the correct long-term direction for a scalable Vietnamese address resolution engine.