BEGIN;
SELECT pg_advisory_xact_lock(82001);
-- How a session reached a task: 'explicit' (it ran make start-task, or an operator named
-- it), 'delegated' (a child spawned inside a bound parent's window). Legacy rows: unknown.
ALTER TABLE usage_collections ADD COLUMN IF NOT EXISTS binding text
    CHECK (binding IN ('explicit', 'delegated'));
-- Content-free effectiveness counters per bound window. Null = not measured.
ALTER TABLE usage_collections ADD COLUMN IF NOT EXISTS human_prompts integer
    CHECK (human_prompts >= 0);
ALTER TABLE usage_collections ADD COLUMN IF NOT EXISTS active_ms bigint
    CHECK (active_ms >= 0);
-- Cache writes are billed differently from reads; kept apart from cached_input_tokens
-- (reads). input_tokens stays inclusive of both. Legacy rows: unknown.
ALTER TABLE usage_samples ADD COLUMN IF NOT EXISTS cache_creation_tokens bigint
    CHECK (cache_creation_tokens >= 0);
-- One machine sample belongs to at most one task. Earlier overlapping windows stored a
-- few twice; the first stored copy is kept.
DELETE FROM usage_samples u USING usage_samples v
 WHERE u.sample_key IS NOT NULL AND u.source = v.source
   AND u.external_session_id = v.external_session_id AND u.sample_key = v.sample_key
   AND u.id > v.id;
CREATE UNIQUE INDEX IF NOT EXISTS usage_samples_owner
    ON usage_samples(source, external_session_id, sample_key) WHERE sample_key IS NOT NULL;
-- Hand-maintained list prices (scripts/telemetry/prices.json, make telemetry-prices).
CREATE TABLE IF NOT EXISTS model_prices (
    model text NOT NULL,
    effective_from date NOT NULL,
    currency char(3) NOT NULL,
    input_per_mtok numeric(12,4) NOT NULL CHECK (input_per_mtok >= 0),
    cache_read_per_mtok numeric(12,4) NOT NULL CHECK (cache_read_per_mtok >= 0),
    cache_write_per_mtok numeric(12,4) NOT NULL CHECK (cache_write_per_mtok >= 0),
    output_per_mtok numeric(12,4) NOT NULL CHECK (output_per_mtok >= 0),
    source text NOT NULL CHECK (length(source) <= 200),
    PRIMARY KEY (model, effective_from)
);
-- GitHub facts captured at merge and refreshed later (follow-ups, reverts).
CREATE TABLE IF NOT EXISTS task_facts (
    task_id text PRIMARY KEY REFERENCES tasks,
    pr_number integer CHECK (pr_number > 0),
    additions integer CHECK (additions >= 0),
    deletions integer CHECK (deletions >= 0),
    changed_files integer CHECK (changed_files >= 0),
    commits integer CHECK (commits >= 0),
    labels jsonb CHECK (labels IS NULL OR jsonb_typeof(labels) = 'array'),
    epic integer CHECK (epic > 0),
    merged_at timestamptz,
    followups jsonb CHECK (followups IS NULL OR jsonb_typeof(followups) = 'array'),
    reverted boolean,
    captured_at timestamptz NOT NULL DEFAULT now()
);
INSERT INTO schema_versions(version) VALUES (5) ON CONFLICT DO NOTHING;
COMMIT;
