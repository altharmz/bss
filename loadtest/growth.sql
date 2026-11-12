-- make loadtest-growth: the growth inventory of #282. Rows and bytes (table, indexes and
-- TOAST; a partitioned table with all its partitions) per table, largest first, then the
-- rows per month of the tables that grow with subscribers x time, by the application-clock
-- column (the seed's simulated date), never a database now() column.
SELECT t.relname AS table_name,
       coalesce(sum(p.reltuples) FILTER (WHERE p.relkind = 'r'), 0)::bigint AS rows_estimate,
       pg_size_pretty(sum(pg_total_relation_size(p.oid))) AS total,
       sum(pg_total_relation_size(p.oid)) AS bytes
  FROM pg_class t
  JOIN pg_namespace n ON n.oid = t.relnamespace AND n.nspname = 'public'
  -- pg_partition_tree is empty for a plain table: the table itself is added
  CROSS JOIN LATERAL (SELECT relid FROM pg_partition_tree(t.oid) UNION SELECT t.oid) tree
  JOIN pg_class p ON p.oid = tree.relid
 WHERE t.relkind IN ('r', 'p') AND NOT t.relispartition
 GROUP BY t.relname
 ORDER BY bytes DESC
 LIMIT 40;

SELECT 'usage_record' AS table_name, date_trunc('month', event_date)::date AS month, count(*)
  FROM usage_record GROUP BY 2
UNION ALL SELECT 'usage_charge', date_trunc('month', created_at)::date, count(*)
  FROM usage_charge GROUP BY 2
UNION ALL SELECT 'billing_run_account', date_trunc('month', r.run_date)::date, count(*)
  FROM billing_run_account a JOIN billing_run r ON r.id = a.run_id GROUP BY 2
UNION ALL SELECT 'applied_customer_billing_rate', date_trunc('month', applied_at)::date, count(*)
  FROM applied_customer_billing_rate GROUP BY 2
UNION ALL SELECT 'customer_bill_state_change', date_trunc('month', changed_at)::date, count(*)
  FROM customer_bill_state_change GROUP BY 2
UNION ALL SELECT 'event', date_trunc('month', occurred_at)::date, count(*) FROM event GROUP BY 2
UNION ALL SELECT 'event_delivery', date_trunc('month', e.occurred_at)::date, count(*)
  FROM event_delivery d JOIN event e ON e.id = d.event_id GROUP BY 2
UNION ALL SELECT 'idempotency_key', date_trunc('month', created_at)::date, count(*)
  FROM idempotency_key GROUP BY 2
UNION ALL SELECT 'notification_message', date_trunc('month', created_at)::date, count(*)
  FROM notification_message GROUP BY 2
UNION ALL SELECT 'app_session', date_trunc('month', created_at)::date, count(*)
  FROM app_session GROUP BY 2
UNION ALL SELECT 'worker_request', date_trunc('month', requested_at)::date, count(*)
  FROM worker_request GROUP BY 2
UNION ALL SELECT 'payment', date_trunc('month', received_at)::date, count(*) FROM payment GROUP BY 2
UNION ALL SELECT 'tax_transaction', date_trunc('month', committed_at)::date, count(*)
  FROM tax_transaction GROUP BY 2
UNION ALL SELECT 'balance_action', date_trunc('month', requested_at)::date, count(*)
  FROM balance_action GROUP BY 2
UNION ALL SELECT 'prepaid_command', date_trunc('month', created_at)::date, count(*)
  FROM prepaid_command GROUP BY 2
ORDER BY 1, 2;
