-- make loadtest-pg-stats: the top 20 statements by total time since the last reset.
CREATE EXTENSION IF NOT EXISTS pg_stat_statements;
SELECT calls,
       round(total_exec_time::numeric, 1) AS total_ms,
       round(mean_exec_time::numeric, 2) AS mean_ms,
       rows,
       left(regexp_replace(query, '\s+', ' ', 'g'), 160) AS query
  FROM pg_stat_statements
 ORDER BY total_exec_time DESC
 LIMIT 20;
