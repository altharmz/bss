BEGIN;
SELECT pg_advisory_xact_lock(82001);
-- The issue's board Priority field (P0-P3) when its facts were captured; the P0-P3
-- labels are gone (#468). Null = empty field, not on the board, or captured before.
ALTER TABLE task_facts ADD COLUMN IF NOT EXISTS priority text
    CHECK (priority IN ('P0', 'P1', 'P2', 'P3'));
INSERT INTO schema_versions(version) VALUES (6) ON CONFLICT DO NOTHING;
COMMIT;
