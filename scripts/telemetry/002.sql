BEGIN;
SELECT pg_advisory_xact_lock(82001);
-- Existing samples remain unknown: never invent provenance for historical rows.
ALTER TABLE usage_samples ADD COLUMN IF NOT EXISTS sample_key text;
ALTER TABLE usage_samples ADD COLUMN IF NOT EXISTS kind text CHECK (kind IN ('delta', 'unavailable'));
ALTER TABLE usage_samples ADD COLUMN IF NOT EXISTS provider text;
ALTER TABLE usage_samples ADD COLUMN IF NOT EXISTS client text;
ALTER TABLE usage_samples ADD COLUMN IF NOT EXISTS model text;
ALTER TABLE usage_samples ADD COLUMN IF NOT EXISTS source text;
ALTER TABLE usage_samples ADD COLUMN IF NOT EXISTS external_session_id text;
CREATE UNIQUE INDEX IF NOT EXISTS usage_samples_identity
    ON usage_samples(session_id, source, sample_key);
INSERT INTO schema_versions(version) VALUES (2) ON CONFLICT DO NOTHING;
COMMIT;
