-- The results of the billing run of :'day' without ids or wall times (#283): its charges
-- (ABRs), bills and account outcomes, as counts and one md5 each. Two measured runs on
-- the same restored dataset must give the same digest, whatever the code path did.
WITH r AS (SELECT id FROM billing_run WHERE kind = 'daily' AND run_date = :'day'),
abrs AS (
  SELECT a.billing_account_id::text || coalesce(a.product_id::text, '') || a.charge_type::text
         || a.amount::text || a.currency || a.period::text || a.tax_amount::text || a.applied_tax::text
         || (a.customer_bill_id IS NOT NULL)::text AS line
    FROM applied_customer_billing_rate a WHERE a.billing_run_id = (SELECT id FROM r)),
bills AS (
  SELECT b.billing_account_id::text || b.bill_date::text || b.bill_period::text
         || b.amount_due::text || b.run_type::text
         || (SELECT count(*) FROM applied_customer_billing_rate x
              WHERE x.customer_bill_id = b.id)::text AS line
    FROM customer_bill b WHERE b.billing_run_id = (SELECT id FROM r)),
outcomes AS (
  SELECT o.billing_account_id::text || o.status::text || coalesce(o.error, '') AS line
    FROM billing_run_account o WHERE o.run_id = (SELECT id FROM r))
SELECT 'abrs', count(*), md5(coalesce(string_agg(line, '|' ORDER BY line), '')) FROM abrs
UNION ALL
SELECT 'bills', count(*), md5(coalesce(string_agg(line, '|' ORDER BY line), '')) FROM bills
UNION ALL
SELECT 'outcomes', count(*), md5(coalesce(string_agg(line, '|' ORDER BY line), '')) FROM outcomes;
