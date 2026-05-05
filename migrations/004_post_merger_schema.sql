-- Migration 004: support pre/post-merger coexistence
-- GSO codes are recycled after the 2025 merger, so (gso_code) alone is no
-- longer unique — the combination (gso_code, valid_from) is.

-- Drop the old single-column unique constraint
ALTER TABLE admin_units DROP CONSTRAINT IF EXISTS admin_units_gso_code_key;

-- Add composite unique key: same code can appear in different validity windows
ALTER TABLE admin_units
    ADD CONSTRAINT admin_units_gso_code_valid_from_key UNIQUE (gso_code, valid_from);

-- Add an era column for quick filtering without date arithmetic
ALTER TABLE admin_units
    ADD COLUMN IF NOT EXISTS era text NOT NULL DEFAULT 'pre_merger'
    CHECK (era IN ('pre_merger', 'post_merger'));

-- Mark all currently loaded (pre-merger) rows explicitly
UPDATE admin_units SET era = 'pre_merger' WHERE era IS DISTINCT FROM 'pre_merger';

CREATE INDEX IF NOT EXISTS idx_admin_era ON admin_units (era);
