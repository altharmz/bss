-- The ids k6 samples from (#281, loadtest/run.py writes them to <run>/data.json): random
-- entities of the restored dataset, one JSON document. Plan changes and terminations get
-- disjoint pools of active postpaid lines with no change planned, so no line is changed
-- twice in one run. Ids only: nothing secret.
WITH lines AS (
  SELECT p.id, p.billing_account_id, p.product_offering_id,
         row_number() OVER (ORDER BY random()) AS n
  FROM product p JOIN billing_account a ON a.id = p.billing_account_id
  WHERE a.rating_type = 'postpaid' AND a.name LIKE 'lt-%' AND p.status = 'active'
    AND p.termination_date IS NULL AND p.parent_product_id IS NULL
    AND NOT EXISTS (SELECT FROM product_offering_change c WHERE c.product_id = p.id)
)
SELECT json_build_object(
  'customers', (SELECT json_agg(id) FROM (SELECT id FROM customer ORDER BY random() LIMIT 5000) c),
  'accounts', (SELECT json_agg(id) FROM (SELECT id FROM billing_account ORDER BY random() LIMIT 5000) a),
  'products', (SELECT json_agg(id) FROM (SELECT id FROM product WHERE status = 'active'
                ORDER BY random() LIMIT 5000) p),
  'bills', (SELECT json_agg(id) FROM (SELECT id FROM customer_bill ORDER BY random() LIMIT 5000) b),
  'buckets', (SELECT json_agg(json_build_array(b.id, b.billing_account_id)) FROM (
                SELECT b.id, b.billing_account_id FROM prepaid_bucket b
                WHERE EXISTS (SELECT FROM product p WHERE p.billing_account_id = b.billing_account_id
                              AND p.status = 'active')
                ORDER BY random() LIMIT 5000) b),
  'postpaid', (SELECT json_agg(id) FROM (SELECT a.id FROM billing_account a
                WHERE a.rating_type = 'postpaid' AND a.name LIKE 'lt-%'
                AND EXISTS (SELECT FROM product p WHERE p.billing_account_id = a.id
                            AND p.status = 'active')
                ORDER BY random() LIMIT 5000) a),
  'changes', (SELECT json_agg(json_build_array(id, billing_account_id, product_offering_id))
                FROM lines WHERE n % 3 <> 0 AND n <= 30000),
  'terminations', (SELECT json_agg(json_build_array(id, billing_account_id))
                FROM lines WHERE n % 3 = 0 AND n <= 30000)
);
