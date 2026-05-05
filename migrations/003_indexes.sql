-- ─────────────────────────────────────────────────────────────────────────────
-- Trigram indexes (pg_trgm GIN) — core of fuzzy address lookup
-- ─────────────────────────────────────────────────────────────────────────────

-- Fuzzy match on admin unit ASCII name (province / district / ward)
CREATE INDEX IF NOT EXISTS idx_admin_ascii_trgm
    ON admin_units USING GIN (ascii_name gin_trgm_ops);

-- Fuzzy match on full display name (catches accented queries)
CREATE INDEX IF NOT EXISTS idx_admin_full_name_trgm
    ON admin_units USING GIN (full_name gin_trgm_ops);

-- Fuzzy match on street ASCII name
CREATE INDEX IF NOT EXISTS idx_street_ascii_trgm
    ON streets USING GIN (ascii_name gin_trgm_ops);

-- Alias lookup
CREATE INDEX IF NOT EXISTS idx_alias_alias
    ON aliases (alias);

CREATE INDEX IF NOT EXISTS idx_alias_type
    ON aliases (alias_type);

-- ─────────────────────────────────────────────────────────────────────────────
-- Hierarchy / parent traversal indexes
-- ─────────────────────────────────────────────────────────────────────────────

CREATE INDEX IF NOT EXISTS idx_admin_type
    ON admin_units (unit_type);

CREATE INDEX IF NOT EXISTS idx_admin_parent
    ON admin_units (parent_id);

CREATE INDEX IF NOT EXISTS idx_admin_type_parent
    ON admin_units (unit_type, parent_id);

-- ─────────────────────────────────────────────────────────────────────────────
-- Date gating — for merger time-travel queries
-- ─────────────────────────────────────────────────────────────────────────────

CREATE INDEX IF NOT EXISTS idx_admin_valid_range
    ON admin_units (valid_from, valid_to);

-- ─────────────────────────────────────────────────────────────────────────────
-- GSO code lookup (primary business key)
-- ─────────────────────────────────────────────────────────────────────────────

CREATE INDEX IF NOT EXISTS idx_admin_gso_code
    ON admin_units (gso_code);

-- ─────────────────────────────────────────────────────────────────────────────
-- Vector search (pgvector HNSW) — populated after embeddings are generated
-- ─────────────────────────────────────────────────────────────────────────────

CREATE INDEX IF NOT EXISTS idx_admin_embedding_hnsw
    ON admin_units USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);

-- ─────────────────────────────────────────────────────────────────────────────
-- Merger table indexes
-- ─────────────────────────────────────────────────────────────────────────────

CREATE INDEX IF NOT EXISTS idx_merger_old_unit
    ON admin_unit_mergers (old_unit_id);

CREATE INDEX IF NOT EXISTS idx_merger_effective
    ON admin_unit_mergers (effective_date);

-- ─────────────────────────────────────────────────────────────────────────────
-- Street hierarchy indexes
-- ─────────────────────────────────────────────────────────────────────────────

CREATE INDEX IF NOT EXISTS idx_street_district
    ON streets (district_id);

CREATE INDEX IF NOT EXISTS idx_street_province
    ON streets (province_id);

-- ─────────────────────────────────────────────────────────────────────────────
-- Postal code index
-- ─────────────────────────────────────────────────────────────────────────────

CREATE INDEX IF NOT EXISTS idx_postal_code
    ON postal_codes (code);
