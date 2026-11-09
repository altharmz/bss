-- make loadtest-pg-stats-reset: start a measurement from zero.
CREATE EXTENSION IF NOT EXISTS pg_stat_statements;
SELECT pg_stat_statements_reset();
