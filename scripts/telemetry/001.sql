BEGIN;
SELECT pg_advisory_xact_lock(82001);
CREATE TABLE IF NOT EXISTS schema_versions (
    version integer PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS tasks (
    task_id text PRIMARY KEY,
    issue_number integer NOT NULL CHECK (issue_number > 0),
    branch text NOT NULL,
    started_at timestamptz NOT NULL DEFAULT now(),
    ended_at timestamptz,
    outcome text CHECK (outcome IN ('completed', 'failed', 'cancelled'))
);
CREATE TABLE IF NOT EXISTS agent_sessions (
    session_id text PRIMARY KEY,
    task_id text NOT NULL REFERENCES tasks,
    provider text NOT NULL,
    model text,
    external_session_id text,
    started_at timestamptz NOT NULL DEFAULT now(),
    ended_at timestamptz
);
CREATE TABLE IF NOT EXISTS workflow_events (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    task_id text NOT NULL REFERENCES tasks,
    session_id text REFERENCES agent_sessions,
    event_type text NOT NULL,
    occurred_at timestamptz NOT NULL DEFAULT now(),
    result integer NOT NULL,
    duration_ms bigint CHECK (duration_ms >= 0),
    metadata jsonb NOT NULL DEFAULT '{}' CHECK (
        jsonb_typeof(metadata) = 'object' AND octet_length(metadata::text) <= 1024)
);
CREATE TABLE IF NOT EXISTS usage_samples (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    session_id text NOT NULL REFERENCES agent_sessions,
    sampled_at timestamptz NOT NULL DEFAULT now(),
    input_tokens bigint CHECK (input_tokens >= 0),
    cached_input_tokens bigint CHECK (cached_input_tokens >= 0),
    output_tokens bigint CHECK (output_tokens >= 0),
    total_tokens bigint CHECK (total_tokens >= 0),
    estimated_cost numeric(20,10) CHECK (estimated_cost >= 0),
    currency char(3),
    pricing_metadata jsonb CHECK (
        jsonb_typeof(pricing_metadata) = 'object' AND
        octet_length(pricing_metadata::text) <= 1024)
);
CREATE INDEX IF NOT EXISTS workflow_events_task_time ON workflow_events(task_id, occurred_at);
CREATE INDEX IF NOT EXISTS agent_sessions_task ON agent_sessions(task_id);
INSERT INTO schema_versions(version) VALUES (1) ON CONFLICT DO NOTHING;
COMMIT;
