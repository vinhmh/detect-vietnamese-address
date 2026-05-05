-- ─────────────────────────────────────────────────────────────────────────────
-- Core administrative units (province / district / ward)
-- Sources: dvhcvn (GSO Vietnam) — updated daily
-- ─────────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS admin_units (
    id          BIGSERIAL PRIMARY KEY,

    -- GSO official code (level1_id / level2_id / level3_id from dvhcvn)
    gso_code    TEXT NOT NULL UNIQUE,

    -- Full display name including prefix (e.g. "Tỉnh Khánh Hòa")
    full_name   TEXT NOT NULL,

    -- Short name without prefix (e.g. "Khánh Hòa")
    name        TEXT NOT NULL,

    -- Prefix / administrative type label in Vietnamese
    -- e.g. "Tỉnh", "Thành phố Trung ương", "Quận", "Phường", "Xã"
    name_prefix TEXT NOT NULL,

    -- Normalised type: 'province' | 'district' | 'ward'
    unit_type   TEXT NOT NULL CHECK (unit_type IN ('province', 'district', 'ward')),

    -- ASCII-stripped name for fuzzy matching (pg_trgm)
    ascii_name  TEXT NOT NULL,

    -- Hierarchy: parent admin_units.id (NULL for provinces)
    parent_id   BIGINT REFERENCES admin_units(id),

    -- Effective date range — supports pre/post merger time-travel
    valid_from  DATE NOT NULL DEFAULT '2000-01-01',
    valid_to    DATE,                -- NULL = currently active

    -- Additional alternate names / abbreviations (JSONB array of strings)
    aliases     JSONB NOT NULL DEFAULT '[]'::JSONB,

    -- Embedding for semantic / accent-free vector search (optional, populated later)
    embedding   VECTOR(768),

    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMENT ON TABLE admin_units IS
    'Vietnamese administrative units from GSO / dvhcvn. 3 levels: province, district, ward.';

-- ─────────────────────────────────────────────────────────────────────────────
-- 2025 province merger mapping (Nghị quyết 202/2024/QH15)
-- Maps dissolved units to their post-merger successors
-- ─────────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS admin_unit_mergers (
    id              BIGSERIAL PRIMARY KEY,
    old_unit_id     BIGINT NOT NULL REFERENCES admin_units(id),
    new_unit_id     BIGINT NOT NULL REFERENCES admin_units(id),
    effective_date  DATE NOT NULL,
    decree_ref      TEXT NOT NULL DEFAULT '202/2024/QH15',
    notes           TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMENT ON TABLE admin_unit_mergers IS
    '2025 administrative merger map. old_unit_id was dissolved into new_unit_id on effective_date.';

-- ─────────────────────────────────────────────────────────────────────────────
-- Alias / abbreviation lookup table
-- Covers: province shorthands, admin-type shorthands, alternate spellings
-- ─────────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS aliases (
    id          BIGSERIAL PRIMARY KEY,

    -- The raw alias as it might appear in user input (ascii-normalised, lowercase)
    alias       TEXT NOT NULL,

    -- The canonical full name or canonical code it expands to
    canonical   TEXT NOT NULL,

    -- 'province' | 'district' | 'ward' | 'unit_type' | 'street' | 'general'
    alias_type  TEXT NOT NULL DEFAULT 'general',

    -- Optional FK to admin_units if alias resolves to a specific unit
    unit_id     BIGINT REFERENCES admin_units(id),

    -- Confidence weight — higher = prefer this alias during resolution
    weight      FLOAT NOT NULL DEFAULT 1.0,

    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMENT ON TABLE aliases IS
    'Abbreviation and alternate-spelling lookup. e.g. tphcm → Thành phố Hồ Chí Minh';

-- ─────────────────────────────────────────────────────────────────────────────
-- Street names (populated later from OpenStreetMap)
-- ─────────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS streets (
    id              BIGSERIAL PRIMARY KEY,
    name            TEXT NOT NULL,
    ascii_name      TEXT NOT NULL,
    ward_id         BIGINT REFERENCES admin_units(id),
    district_id     BIGINT REFERENCES admin_units(id),
    province_id     BIGINT REFERENCES admin_units(id),
    osm_id          BIGINT,
    aliases         JSONB NOT NULL DEFAULT '[]'::JSONB,
    popularity_score FLOAT NOT NULL DEFAULT 0.0,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMENT ON TABLE streets IS 'Street names from OpenStreetMap. Populated in Phase 5.';

-- ─────────────────────────────────────────────────────────────────────────────
-- Postal codes (populated later from Vietnam Post)
-- ─────────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS postal_codes (
    id          BIGSERIAL PRIMARY KEY,
    code        TEXT NOT NULL UNIQUE,
    ward_id     BIGINT REFERENCES admin_units(id),
    district_id BIGINT REFERENCES admin_units(id),
    province_id BIGINT REFERENCES admin_units(id),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ─────────────────────────────────────────────────────────────────────────────
-- OCR confusion patterns (populated from correction logs over time)
-- ─────────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS ocr_confusions (
    id              BIGSERIAL PRIMARY KEY,
    wrong_token     TEXT NOT NULL,
    corrected_token TEXT NOT NULL,
    frequency       INT NOT NULL DEFAULT 1,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (wrong_token, corrected_token)
);

-- ─────────────────────────────────────────────────────────────────────────────
-- Human correction feedback loop
-- ─────────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS correction_logs (
    id                BIGSERIAL PRIMARY KEY,
    raw_input         TEXT NOT NULL,
    predicted_address JSONB,
    corrected_address JSONB,
    confidence        FLOAT,
    reviewer          TEXT,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMENT ON TABLE correction_logs IS
    'Stores raw input vs predicted vs human-corrected addresses for feedback learning.';
