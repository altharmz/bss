BEGIN;
SELECT pg_advisory_xact_lock(82001);
-- Before the storage guard, harness tests wrote real rows for fixture tasks. These ids
-- never had a branch or PR on GitHub; their names come from the test fixtures. Runs once.
DO $$
DECLARE fixtures text[] := ARRAY[
    'issue-10-task-10', 'issue-12-task-12', 'issue-81-subscriber-search',
    'issue-82-explicit-request', 'issue-82-task-82', 'issue-95-task-95', 'issue-99-task-99'];
BEGIN
    IF EXISTS (SELECT 1 FROM schema_versions WHERE version = 4) THEN RETURN; END IF;
    DELETE FROM usage_samples WHERE session_id IN
        (SELECT session_id FROM agent_sessions WHERE task_id = ANY(fixtures));
    DELETE FROM workflow_events WHERE task_id = ANY(fixtures);
    DELETE FROM usage_collections WHERE task_id = ANY(fixtures);
    DELETE FROM usage_issue_snapshots WHERE task_id = ANY(fixtures);
    DELETE FROM agent_sessions WHERE task_id = ANY(fixtures);
    DELETE FROM tasks WHERE task_id = ANY(fixtures);
END $$;
INSERT INTO schema_versions(version) VALUES (4) ON CONFLICT DO NOTHING;
COMMIT;
