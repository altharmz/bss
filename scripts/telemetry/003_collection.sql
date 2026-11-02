BEGIN;
SELECT pg_advisory_xact_lock(82001);
-- Coverage is independent of imported samples and pricing. No source paths or content.
CREATE TABLE IF NOT EXISTS usage_collections (
    task_id text NOT NULL REFERENCES tasks,
    client text NOT NULL CHECK (client IN ('codex', 'claude', 'kimi')),
    external_session_id text NOT NULL,
    source text NOT NULL,
    bound_at timestamptz NOT NULL DEFAULT now(),
    closed_at timestamptz,
    checked_at timestamptz,
    baseline_tokens jsonb,
    latest_tokens jsonb,
    status text NOT NULL DEFAULT 'unavailable'
        CHECK (status IN ('unavailable', 'partial', 'complete')),
    reason text NOT NULL DEFAULT 'not_collected' CHECK (reason IN (
        'not_collected', 'session_identity_missing', 'source_missing', 'source_ambiguous',
        'source_invalid', 'counters_missing', 'counter_reset', 'session_open',
        'final_counter_pending', 'collected', 'unsupported_client', 'storage_failed')),
    PRIMARY KEY(task_id, client, external_session_id),
    CHECK (closed_at IS NULL OR closed_at >= bound_at)
);
CREATE INDEX IF NOT EXISTS usage_collections_session
    ON usage_collections(client, external_session_id);
CREATE TABLE IF NOT EXISTS usage_issue_snapshots (
    task_id text NOT NULL REFERENCES tasks,
    phase text NOT NULL CHECK (phase IN ('start', 'pr')),
    captured_at timestamptz NOT NULL DEFAULT now(),
    stats jsonb NOT NULL,
    PRIMARY KEY(task_id, phase)
);
INSERT INTO schema_versions(version) VALUES (3) ON CONFLICT DO NOTHING;
COMMIT;
