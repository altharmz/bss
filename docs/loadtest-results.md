# Load-test results (#281)

How the runs are made: [loadtest.md](loadtest.md#the-k6-scenarios-281). Each scenario's
full report is `loadtest/out/<scenario>-<time>/report.md` on the workstation (not
committed); this page keeps the numbers. Run dates: 13 and 14 November 2026.

## Summary

| Scenario | Verdict | What it showed |
|---|---|---|
| `smoke` | pass | every request type once, 63 requests, no errors |
| `steady` (1x, 30 min) | pass | 100,502 requests, no errors, worst p99 115 ms, feed lag at most 81 s |
| `stress` (1x, 2x, 3x, 5x) | ceiling found | 1x and 2x meet every target; the first break is `read` at 3x; 5x collapses |
| `soak` (1x, 3 h) | targets held; 2 flags | 603,439 requests, no errors, worst p99 147 ms; flagged: the worker's memory grew 73 % (26 MiB an hour), and k6 dropped 6 of 474,951 arrivals |
| `billing-night` | pass | the daily run of 100,000 accounts in 22.4 min with 20 % of the mix; API p99 under 110 ms during it |
| `prod-shaped` | fails, as expected | at today's production limits the app serves about 16 requests/s of the 47 offered and is killed for memory after 103 s |

The targets hold at the 1x peak with a factor of two to spare, on the load-test budget
(4 logical CPUs of a laptop). Three things had to change first; the first steady run
failed on each of them ([what the first runs found](#what-the-first-runs-found)).

**Where the numbers come from.** A laptop CPU (AMD Ryzen 5 PRO 4650U, 6 cores / 12
threads) in WSL2 with 15 GB RAM. The system under test (lt-db, lt-app, lt-worker,
lt-mcp) shares a cpuset of 4 logical CPUs, emulating a 4-core host; k6 runs on 2 other
logical CPUs. These are not server numbers: a laptop core in WSL2 with SMT siblings is
slower and noisier than a dedicated server vCPU, and other containers ran on the same
machine. The dataset is `seed100k` (100,000 subscribers, 114,978 lines, 6.8M usage
records), aged nine days by the time of the runs.

**Targets:** per group p95 < 1 s and p99 < 1.5 s; < 0.1 % errors; the 100k daily run
< 90 min; the usage feed lag < 5 min at peak.

## Context

| | |
|---|---|
| SUT cpuset / k6 cpuset | `4-7` / `8-9`, recorded per run from `docker inspect`. `lscpu -e`: CPUs 4-5 are core 2, 6-7 core 3, 8-9 core 4, so the two sets share no core |
| Container limits | app 3.0 CPU / 1536m (4 processes, pool 15), worker 3.0 CPU / 1024m, mcp 0.5 / 256m, postgres 3g, `max_connections=80` |
| `shared_buffers` | 1GB. The programme's 2GB did not fit the 3g limit ([below](#what-the-first-runs-found)) |
| Usage feed | pinned to the peak hour: 70 records/s (4,200 a minute) in steady, stress, soak and prod-shaped; 8 records/s in billing-night |
| MCP | 2 calls/s over 3 read-only tokens (15 in stress): bss-mcp allows a principal 60 calls a minute |
| Dataset state | `seed100k` restored, then the worker's catch-up of the missed days' prepaid work, saved as a second dump; every measured run starts from that dump |
| Other containers | 21 other containers running, 12 % of one CPU in total when steady started; another agent's task stacks (their own databases and test runs) were active on the machine during some runs |
| Code | the task branch with `master` up to #339 merged, except smoke and the two first steady runs (`master` up to #337) |

## What the first runs found

The first 30-minute steady run at 1x (`steady-20261113T205316`, 13 November) failed:

| Group | Requests | RPS | p50 ms | p95 ms | p99 ms | Errors | In target |
|---|---|---|---|---|---|---|---|
| read | 44,838 | 24.58 | 21.2 | 213.2 | 5647.5 | 0.169% | no |
| list | 8,970 | 4.92 | 55.8 | 518.5 | 5512.7 | 0.279% | no |
| write | 16,040 | 8.79 | 48.3 | 335.0 | 3518.7 | 0.175% | no |
| auth | 40 | 0.02 | 99.9 | 310.6 | 349.3 | 0.000% | yes |
| ui | 17,085 | 9.36 | 41.3 | 182.1 | 1784.5 | 0.111% | no |
| mcp | 3,591 | 1.97 | 27.4 | 189.1 | 2989.7 | 0.000% | no |
| usage-post | 8,969 | 4.92 | 35.1 | 276.0 | 4806.1 | 0.201% | no |

p95 was inside the target in every group; p99 and the errors were not. The feed lag
reached 1,150 s. Three causes, each fixed
and measured again:

1. **The database was killed for memory, twice in 30 minutes.** `shared_buffers=2GB` in
   a 3g container: 2.0 GB of shared memory and 0.9 GB of backends (60 app connections,
   about 15 MB each, plus page tables) left nothing for the page cache. The kernel
   killed a backend at 18:11 and 18:32 UTC (`Memory cgroup out of memory`), PostgreSQL
   went through crash recovery each time, and for about 40 s every request waited or
   failed: 172 responses 5xx, 744 pool-timeout log
   lines, all in those two minutes. With `shared_buffers=1GB` the container peaks at
   2.8 GB including the page cache and nothing is killed.
2. **The feed took one batch of 500 per wake-up**, at most 8.3 records/s against 70/s.
   Taking batches until the feed is empty was not enough: about seven statements per
   record gave 80-125 records/s, the whole wake-up. A batch is now stored with one
   statement per step: about 770 records/s
   ([usage.md](decisions/usage.md#feed)).
3. **A wake-up took 4 to 6 minutes.** Besides the feed, the worker looked up about
   47,500 already-told notification events one by one, every wake-up. They are now
   dropped with one statement per detector
   ([notifications.md](decisions/notifications.md)). A wake-up under the 1x load takes
   about 24 s, so the feed is fetched every 85 s.

The earlier diagnostic of 12 November also had MCP failing 45 % of its calls: one
service token at 2 calls/s is twice the limit per principal. The runner now spreads
the calls over three tokens; the limit stays as in production.

## Scenarios

CPU percentages use Docker's convention (100 % = one logical CPU). RAM is Docker's
figure for the container (it includes the page cache for postgres). RPS counts HTTP
requests: a new subscriber is five write requests.

### smoke

`smoke-20261113T175028`: **pass**, 63 requests, no errors, k6 exit 0. Every request type
once, against the freshly restored dataset. The worker was still busy with its second
wake-up after the restore (48 % of a CPU), so these are not quiet-system latencies.

| Group | Requests | RPS | p50 ms | p95 ms | p99 ms | Errors | In target |
|---|---|---|---|---|---|---|---|
| read | 5 | 0.27 | 57.9 | 87.5 | 88.9 | 0.000% | yes |
| list | 5 | 0.27 | 68.2 | 137.7 | 147.5 | 0.000% | yes |
| write | 9 | 0.49 | 160.0 | 614.7 | 715.1 | 0.000% | yes |
| auth | 21 | 1.15 | 86.5 | 141.0 | 179.5 | 0.000% | yes |
| ui | 14 | 0.77 | 95.3 | 207.1 | 220.7 | 0.000% | yes |
| mcp | 5 | 0.27 | 27.1 | 183.5 | 207.9 | 0.000% | yes |
| usage-post | 1 | 0.05 | 92.2 | 92.2 | 92.2 | 0.000% | yes |

| Container | CPU avg / max % | RAM avg / max MiB | RAM, last tenth vs first |
|---|---|---|---|
| postgres | 42.4 / 48.5 | 2518.7 / 2534.4 | +1.0% |
| app | 1.4 / 1.9 | 318.5 / 339.1 | +10.1% |
| worker | 48.7 / 50.2 | 104.5 / 104.7 | -0.3% |
| mcp | 0.2 / 0.2 | 111.0 / 111.1 | +0.1% |

<details><summary>Top 20 statements by total time</summary>

| Calls | Total ms | Mean ms | Statement |
|---|---|---|---|
| 9,609 | 1,163 | 0.12 | `SELECT * FROM product WHERE billing_account_id = $1 AND parent_product_id IS NULL AND status NOT IN ` |
| 11,751 | 1,094 | 0.09 | `SELECT * FROM funded_interval WHERE product_id = $1 ORDER BY lower(period) DESC, authorized_at DESC ` |
| 9,613 | 1,082 | 0.11 | `SELECT id, currency, bill_cycle_day, details, rating_type FROM billing_account WHERE id = $1 FOR UPD` |
| 701 | 928 | 1.32 | `INSERT INTO product_order_item ("product_order_id", "item_id", "position", "action", "state", "produ` |
| 10,350 | 866 | 0.08 | `SELECT * FROM billing_account WHERE id = $1` |
| 707 | 767 | 1.08 | `UPDATE product_order_item SET state = $1 WHERE id = $2 RETURNING *` |
| 11,746 | 694 | 0.06 | `SELECT * FROM product_status_change WHERE product_id = $1 AND applied_at IS NULL` |
| 701 | 586 | 0.84 | `INSERT INTO prepaid_command (id, billing_account_id, kind, due_date, request, created_at) VALUES ($1` |
| 11,045 | 506 | 0.05 | `SELECT * FROM product_order_item WHERE product_id = $1 AND action = 'delete' AND state NOT IN ('comp` |
| 701 | 442 | 0.63 | `INSERT INTO product_status_change (product_id, change_date, status, changed_by_item_id, applied_at, ` |
| 704 | 363 | 0.52 | `INSERT INTO product_order (id, billing_account_id, state, order_date, requested_start_date) VALUES (` |
| 701 | 295 | 0.42 | `INSERT INTO prepaid_lifecycle_event (product_id, billing_account_id, action, business_date, outcome,` |
| 701 | 279 | 0.40 | `UPDATE prepaid_lifecycle_event SET product_order_id = $1, outcome = $2, reason = $3 WHERE id = $4` |
| 707 | 276 | 0.39 | `UPDATE product_order SET state = $1, completion_date = coalesce($2, completion_date) WHERE id = $3 R` |
| 9,612 | 177 | 0.02 | `SELECT * FROM prepaid_lifecycle_setting` |
| 1,405 | 126 | 0.09 | `SELECT * FROM product_status_change WHERE product_id = $1 ORDER BY change_date` |
| 2 | 122 | 61.27 | `SELECT status::text, count(*) FROM product GROUP BY 1` |
| 2 | 111 | 55.71 | `SELECT count(*) FILTER (WHERE state IN ($1, $2)), min(created_at) FILTER (WHERE state IN ($3, $4)), ` |
| 2 | 110 | 55.08 | `SELECT count(*) FILTER (WHERE state = $1), min(created_at) FILTER (WHERE state = $2), count(*) FILTE` |
| 705 | 102 | 0.14 | `SELECT coalesce(bool_or(valid_for @> $1::date), false) AS busy, coalesce(bool_or(effective_from > $1` |

</details>

### steady

`steady-20261114T064848`: **pass**, the 1x mix
for 30 min, 100,502 requests,
0.000% errors, k6 exit 0,
no dropped iterations, 0 pool timeouts.

| Group | Requests | RPS | p50 ms | p95 ms | p99 ms | Errors | In target |
|---|---|---|---|---|---|---|---|
| read | 45,001 | 24.68 | 13.1 | 29.5 | 48.8 | 0.000% | yes |
| list | 9,001 | 4.94 | 26.5 | 54.3 | 84.6 | 0.000% | yes |
| write | 16,225 | 8.9 | 30.3 | 69.3 | 105.0 | 0.000% | yes |
| auth | 40 | 0.02 | 65.6 | 94.3 | 114.6 | 0.000% | yes |
| ui | 17,630 | 9.67 | 23.4 | 50.3 | 73.6 | 0.000% | yes |
| mcp | 3,601 | 1.97 | 18.0 | 38.9 | 55.3 | 0.000% | yes |
| usage-post | 9,001 | 4.94 | 23.4 | 49.1 | 87.0 | 0.000% | yes |

| Container | CPU avg / max % | RAM avg / max MiB | RAM, last tenth vs first |
|---|---|---|---|
| postgres | 49.0 / 210.1 | 2567.6 / 2740.2 | +2.0% |
| app | 56.5 / 90.7 | 482.9 / 487.8 | +7.7% |
| worker | 10.9 / 62.0 | 112.4 / 129.9 | +28.6% |
| mcp | 1.6 / 5.9 | 106.5 / 107.1 | -0.0% |

The usage feed made 137,646 records in the window
(70/s). The oldest unacknowledged record was at most
81 s old (target 300 s), with at most
5,678 waiting.

The first passing run (`steady-20261113T215121`, before `master`'s batched bill and payment
lists were merged) had the same shape: 100,003 requests, no errors, worst p99 185 ms
(`list`), feed lag at most 126 s.

<details><summary>Top 20 statements by total time</summary>

| Calls | Total ms | Mean ms | Statement |
|---|---|---|---|
| 270 | 102,788 | 380.70 | `INSERT INTO usage_record (id, network_record_id, kind, quantity, roaming, throttled, msisdn, imsi, e` |
| 22 | 34,917 | 1587.14 | `SELECT c.* FROM prepaid_command c LEFT JOIN funded_interval f ON f.id::text = c.request->>$4 LEFT JO` |
| 407,000 | 32,104 | 0.08 | `SELECT n.value FROM number_assignment a JOIN subscriber_number n ON n.id = a.subscriber_number_id WH` |
| 12,772 | 17,443 | 1.37 | `SELECT balance_ledger_check(NEW.billing_account_id)` |
| 9,001 | 15,409 | 1.71 | `INSERT INTO usage_record (id, network_record_id, kind, quantity, roaming, throttled, msisdn, imsi, e` |
| 22 | 14,011 | 636.85 | `SELECT DISTINCT ON (c.id) c.id, c.billing_account_id, f.product_id, p.name, s.required - s.available` |
| 96,860 | 10,607 | 0.11 | `SELECT u.*, s.token_hash FROM app_session s JOIN app_user u USING (username) WHERE s.token_hash = $1` |
| 17,010 | 7,748 | 0.46 | `SELECT a.id, ( (SELECT coalesce(sum(amount), $2) FROM payment WHERE billing_account_id = a.id AND st` |
| 11,473 | 6,874 | 0.60 | `SELECT * FROM "product_order_item" WHERE id = ANY($1)` |
| 1,048 | 6,604 | 6.30 | `EXISTS ( SELECT FROM funding_conversion_bill t JOIN funding_hold_conversion c ON c.hold_id = t.hold_` |
| 3,650 | 6,279 | 1.72 | `INSERT INTO balance_action (kind, status, billing_account_id, currency, bucket_id, request, amount, ` |
| 9,001 | 5,974 | 0.66 | `INSERT INTO idempotency_key (operator, method, route, key, request_hash, status_code, response, loca` |
| 66 | 5,791 | 87.74 | `SELECT DISTINCT dedupe_key FROM notification_message WHERE dedupe_key = ANY($1)` |
| 9,726 | 5,462 | 0.56 | `SELECT * FROM customer_bill WHERE id = $1` |
| 270 | 4,696 | 17.39 | `SELECT i.product_id, i.day, product_offering_on(i.product_id, i.day) AS id FROM unnest($1::uuid[], $` |
| 9,369 | 4,689 | 0.50 | `SELECT b.id, b.bill_date, b.amount_due, b.state, coalesce((SELECT sum(amount) FROM bill_allocation W` |
| 5,862 | 4,416 | 0.75 | `INSERT INTO payment (billing_account_id, currency, amount, received_at, payment_method, status, paym` |
| 6,278 | 4,368 | 0.70 | `UPDATE product_order_item SET state = $1 WHERE id = $2 RETURNING *` |
| 11,370 | 4,087 | 0.36 | `SELECT ( (SELECT coalesce(sum(amount), $2) FROM payment WHERE billing_account_id = $1 AND status = $` |
| 22 | 3,973 | 180.58 | `SELECT g.id, g.product_id, g.billing_account_id, g.due FROM ( SELECT DISTINCT ON (product_id) id, pr` |

</details>

### stress

`stress-20261114T044633`: 1x, 2x, 3x and 5x for 10 min each (30 s ramps),
340,738 requests. **The first break is `read` at 3x**
(p95 1,664 ms); 1x and 2x meet every target. p95 / p99 in ms, **x** marks a missed
target:

| Group | 1x p95 / p99 | 2x p95 / p99 | 3x p95 / p99 | 5x p95 / p99 | 5x errors |
|---|---|---|---|---|---|
| read | 33 / 57 | 71 / 171 | 1,664 / 2,328 **x** | 27,396 / 28,754 **x** | 19.5% |
| list | 64 / 96 | 97 / 223 | 1,616 / 2,240 **x** | 27,485 / 28,920 **x** | 19.2% |
| write | 77 / 116 | 140 / 297 | 1,914 / 2,601 **x** | 28,185 / 29,506 **x** | 0.1% |
| auth | 125 / 150 | 171 / 176 | 2,499 / 2,687 **x** | 22,565 / 23,037 **x** | 0.0% |
| ui | 52 / 74 | 103 / 211 | 1,192 / 1,964 **x** | 19,395 / 25,612 **x** | 73.9% |
| mcp | 39 / 66 | 67 / 148 | 305 / 654 | 9,384 / 10,016 **x** | 15.3% |
| usage-post | 51 / 77 | 84 / 217 | 1,638 / 2,245 **x** | 27,495 / 28,874 **x** | 19.5% |

- At 2x (60 reads/s, 10 writes/s, 40 operators; the feed is not scaled and stays at
  70 records/s) the worst p99 is 297 ms (`write`).
- At 3x the app is the limit: its four processes use the whole 3.0 CPU allowance
  (285 % at the peak) while postgres averages one CPU. Requests queue in the app;
  nothing fails yet.
- At 5x requests wait about 28 s, 19 % of reads fail and most operators cannot sign in.
  The app's memory reaches 1,421 MiB of its 1,536 MiB: requests in flight are not
  bounded, so memory grows with the queue.
- The feed fell behind during the overload (lag up to
  990 s): the worker shares the four CPUs.

| Container | CPU avg / max % | RAM avg / max MiB | RAM, last tenth vs first |
|---|---|---|---|
| postgres | 97.8 / 265.7 | 2649.0 / 2814.0 | +0.3% |
| app | 175.4 / 284.9 | 821.7 / 1421.3 | +215.8% |
| worker | 8.6 / 52.9 | 119.2 / 131.5 | +22.6% |
| mcp | 4.1 / 12.5 | 107.4 / 109.6 | +1.5% |

<details><summary>Top 20 statements by total time</summary>

| Calls | Total ms | Mean ms | Statement |
|---|---|---|---|
| 30,955 | 948,504 | 30.64 | `INSERT INTO usage_record (id, network_record_id, kind, quantity, roaming, throttled, msisdn, imsi, e` |
| 37,534 | 786,554 | 20.96 | `SELECT balance_ledger_check(NEW.billing_account_id)` |
| 320 | 425,183 | 1328.70 | `INSERT INTO usage_record (id, network_record_id, kind, quantity, roaming, throttled, msisdn, imsi, e` |
| 51,734 | 343,558 | 6.64 | `SELECT a.id, ( (SELECT coalesce(sum(amount), $2) FROM payment WHERE billing_account_id = a.id AND st` |
| 3,058 | 316,761 | 103.58 | `EXISTS ( SELECT FROM funding_conversion_bill t JOIN funding_hold_conversion c ON c.hold_id = t.hold_` |
| 104,125 | 226,666 | 2.18 | `SELECT * FROM "billing_account" WHERE id = ANY($1)` |
| 301,231 | 193,944 | 0.64 | `SELECT u.*, s.token_hash FROM app_session s JOIN app_user u USING (username) WHERE s.token_hash = $1` |
| 10,573 | 190,776 | 18.04 | `INSERT INTO balance_action (kind, status, billing_account_id, currency, bucket_id, request, amount, ` |
| 18,324 | 179,609 | 9.80 | `UPDATE product_order_item SET state = $1 WHERE id = $2 RETURNING *` |
| 8,467 | 171,493 | 20.25 | `SELECT * FROM "billing_account" ORDER BY created_at, id OFFSET $1 LIMIT $2` |
| 38,856 | 163,438 | 4.21 | `WITH rates AS MATERIALIZED ( SELECT customer_bill_id, amount, applied_tax FROM applied_customer_bill` |
| 38,746 | 160,920 | 4.15 | `SELECT * FROM "product_order_item" WHERE id = ANY($1)` |
| 21,685 | 155,894 | 7.19 | `SELECT product_offering_on($1, $2) AS id` |
| 26,523 | 152,578 | 5.75 | `INSERT INTO idempotency_key (operator, method, route, key, request_hash, status_code, response, loca` |
| 6,212 | 144,932 | 23.33 | `SELECT * FROM "customer_bill" ORDER BY created_at DESC, id DESC OFFSET $1 LIMIT $2` |
| 49,147 | 142,902 | 2.91 | `SELECT * FROM "billing_account" WHERE "customer_id" = ANY($1) ORDER BY created_at, id` |
| 6,195 | 138,867 | 22.42 | `SELECT * FROM "product" ORDER BY created_at, id OFFSET $1 LIMIT $2` |
| 17,238 | 131,292 | 7.62 | `INSERT INTO payment (billing_account_id, currency, amount, received_at, payment_method, status, paym` |
| 38,746 | 123,333 | 3.18 | `SELECT DISTINCT ON (product_id) * FROM funded_interval WHERE product_id = ANY($1) ORDER BY product_i` |
| 17 | 122,921 | 7230.64 | `SELECT c.* FROM prepaid_command c LEFT JOIN funded_interval f ON f.id::text = c.request->>$4 LEFT JO` |

</details>

### soak

`soak-20261114T080929`: the 1x mix for 3 h, 603,439
requests, 0.000% errors,
0 pool timeouts. **Every latency and error target held
for the three hours; the run is flagged on two other checks.**

| Group | Requests | RPS | p50 ms | p95 ms | p99 ms | Errors | In target |
|---|---|---|---|---|---|---|---|
| read | 270,001 | 24.94 | 14.0 | 38.5 | 92.0 | 0.000% | yes |
| list | 54,001 | 4.99 | 31.4 | 70.9 | 137.3 | 0.000% | yes |
| write | 97,275 | 8.99 | 32.8 | 83.5 | 147.0 | 0.000% | yes |
| auth | 40 | 0.0 | 68.8 | 98.1 | 116.2 | 0.000% | yes |
| ui | 106,517 | 9.84 | 24.9 | 61.2 | 123.9 | 0.000% | yes |
| mcp | 21,601 | 2.0 | 20.3 | 53.0 | 113.1 | 0.000% | yes |
| usage-post | 54,001 | 4.99 | 30.0 | 70.0 | 138.7 | 0.000% | yes |

| Container | CPU avg / max % | RAM avg / max MiB | RAM, last tenth vs first |
|---|---|---|---|
| postgres | 57.3 / 281.9 | 2642.2 / 2821.1 | -1.8% |
| app | 64.0 / 239.1 | 493.6 / 497.3 | +4.5% |
| worker | 11.6 / 70.7 | 157.9 / 198.4 | +72.6% |
| mcp | 1.5 / 9.4 | 106.3 / 111.0 | +0.1% |

- **Flag: the worker's memory grew 73 %**, from 113 MiB (first tenth) to 194 MiB (last
  tenth), in a straight line: 26 MiB an hour over the last two hours, 198 MiB at the
  end against a 1,024 MiB limit. The app (493 to 497 MiB), mcp (106 MiB) and postgres
  did not grow. A 15-minute
  run of the worker alone under Python's allocation tracer (the feed on, no API
  traffic) showed its live objects flat at 51 MiB, while every wake-up allocates and
  frees about 65 MiB for the rows of the notification detectors (peak 118 MiB). The
  only growing site was the notification stub, which keeps what it sent: about 2 MiB an
  hour. So this looks like allocator fragmentation from the per-wake-up churn, not a
  leak; it is not proven, and at this rate the limit is 30 hours away. Followed up in
  #340 (the detectors' work per wake-up).
- **Flag: k6 dropped 6 of 474,951 arrivals** (no free virtual user at that instant), so
  its `dropped_iterations` threshold failed and k6 exited 99. The longest requests of
  the run took 5 to 6 s; p99 stayed under 150 ms. What stalled the system for those
  seconds is not known: the database's CPU reached 282 % a few times, and another
  agent's test runs were active on the machine.
- The usage feed made 770,446 records; the lag was at
  most 102 s.

<details><summary>Top 20 statements by total time</summary>

| Calls | Total ms | Mean ms | Statement |
|---|---|---|---|
| 1,578 | 627,652 | 397.75 | `INSERT INTO usage_record (id, network_record_id, kind, quantity, roaming, throttled, msisdn, imsi, e` |
| 123 | 222,460 | 1808.62 | `SELECT c.* FROM prepaid_command c LEFT JOIN funded_interval f ON f.id::text = c.request->>$4 LEFT JO` |
| 2,275,507 | 191,848 | 0.08 | `SELECT n.value FROM number_assignment a JOIN subscriber_number n ON n.id = a.subscriber_number_id WH` |
| 76,194 | 128,831 | 1.69 | `SELECT balance_ledger_check(NEW.billing_account_id)` |
| 54,001 | 108,178 | 2.00 | `INSERT INTO usage_record (id, network_record_id, kind, quantity, roaming, throttled, msisdn, imsi, e` |
| 123 | 86,700 | 704.88 | `SELECT DISTINCT ON (c.id) c.id, c.billing_account_id, f.product_id, p.name, s.required - s.available` |
| 581,797 | 72,674 | 0.12 | `SELECT u.*, s.token_hash FROM app_session s JOIN app_user u USING (username) WHERE s.token_hash = $1` |
| 101,540 | 55,006 | 0.54 | `SELECT a.id, ( (SELECT coalesce(sum(amount), $2) FROM payment WHERE billing_account_id = a.id AND st` |
| 10,768 | 43,828 | 4.07 | `SELECT * FROM usage_record WHERE "product_id" = $1 ORDER BY event_at DESC, id OFFSET $2 LIMIT $3` |
| 53,995 | 43,481 | 0.81 | `INSERT INTO idempotency_key (operator, method, route, key, request_hash, status_code, response, loca` |
| 21,613 | 43,320 | 2.00 | `INSERT INTO balance_action (kind, status, billing_account_id, currency, bucket_id, request, amount, ` |
| 5,946 | 43,076 | 7.24 | `EXISTS ( SELECT FROM funding_conversion_bill t JOIN funding_hold_conversion c ON c.hold_id = t.hold_` |
| 69,198 | 40,728 | 0.59 | `SELECT * FROM "product_order_item" WHERE id = ANY($1)` |
| 57,940 | 36,681 | 0.63 | `SELECT * FROM customer_bill WHERE id = $1` |
| 376 | 36,387 | 96.77 | `SELECT DISTINCT dedupe_key FROM notification_message WHERE dedupe_key = ANY($1)` |
| 36,270 | 31,378 | 0.87 | `UPDATE product_order_item SET state = $1 WHERE id = $2 RETURNING *` |
| 1,578 | 30,926 | 19.60 | `SELECT i.product_id, i.day, product_offering_on(i.product_id, i.day) AS id FROM unnest($1::uuid[], $` |
| 35,124 | 30,718 | 0.87 | `INSERT INTO payment (billing_account_id, currency, amount, received_at, payment_method, status, paym` |
| 68,784 | 29,444 | 0.43 | `SELECT ( (SELECT coalesce(sum(amount), $2) FROM payment WHERE billing_account_id = $1 AND status = $` |
| 56,348 | 29,215 | 0.52 | `SELECT b.id, b.bill_date, b.amount_due, b.state, coalesce((SELECT sum(amount) FROM bill_allocation W` |

</details>

### billing-night

`billing-night-20261114T023648`: **pass**. 20 % of the mix for two hours, the feed at the night's
8 records/s, and one catch-up of the dataset's first unbilled date (5 November) asked
for through `POST /internal/v1/billingRun/catchUp` 30 s into the run. The dataset's
dates are frozen, so this is not the calendar's today; one date is one daily run.

| | |
|---|---|
| Run | 100,000 members, 100,000 ok, 0 failed |
| Duration | **1,341.6 s (22.4 min)**; target 90 min. Started 38 s after the request (the worker's next wake-up) |
| `pg_dump -Fc` of the database | started 105 min after the run's start (the 02:15 backup's slot): 80 s |
| Feed lag | up to 1,424 s while the worker billed (the feed waits for the run), 13 s at the end. Not judged here |

API latency during the run's 22 minutes:

| Group | Requests | p95 ms | p99 ms |
|---|---|---|---|
| read | 6,710 | 38.9 | 55.3 |
| list | 1,342 | 69.3 | 86.4 |
| write | 2,458 | 79.7 | 106.5 |
| ui | 2,714 | 50.4 | 65.3 |
| mcp | 537 | 37.7 | 50.9 |
| usage-post | 1,342 | 61.9 | 74.5 |

The whole two hours (80,202 requests, no errors):

| Group | Requests | RPS | p50 ms | p95 ms | p99 ms | Errors | In target |
|---|---|---|---|---|---|---|---|
| read | 36,001 | 4.99 | 14.4 | 37.4 | 54.6 | 0.000% | yes |
| list | 7,201 | 1.0 | 33.5 | 67.3 | 90.3 | 0.000% | yes |
| write | 12,589 | 1.74 | 33.9 | 76.3 | 103.1 | 0.000% | yes |
| auth | 24 | 0.0 | 72.6 | 117.4 | 134.5 | 0.000% | yes |
| ui | 14,301 | 1.98 | 22.8 | 45.8 | 64.2 | 0.000% | yes |
| mcp | 2,880 | 0.4 | 18.3 | 36.3 | 51.7 | 0.000% | yes |
| usage-post | 7,201 | 1.0 | 36.7 | 60.8 | 79.7 | 0.000% | yes |

| Container | CPU avg / max % | RAM avg / max MiB | RAM, last tenth vs first |
|---|---|---|---|
| postgres | 33.0 / 235.3 | 2571.1 / 2679.8 | -0.8% |
| app | 13.8 / 34.5 | 385.9 / 390.9 | +2.6% |
| worker | 14.4 / 65.9 | 116.0 / 124.7 | +21.7% |
| mcp | 0.3 / 5.1 | 105.9 / 107.1 | +0.1% |

<details><summary>Top 20 statements by total time</summary>

| Calls | Total ms | Mean ms | Statement |
|---|---|---|---|
| 77 | 118,825 | 1543.18 | `SELECT c.* FROM prepaid_command c LEFT JOIN funded_interval f ON f.id::text = c.request->>$4 LEFT JO` |
| 1,424,500 | 106,952 | 0.08 | `SELECT n.value FROM number_assignment a JOIN subscriber_number n ON n.id = a.subscriber_number_id WH` |
| 176 | 77,446 | 440.03 | `INSERT INTO usage_record (id, network_record_id, kind, quantity, roaming, throttled, msisdn, imsi, e` |
| 100,000 | 60,012 | 0.60 | `WITH old AS ( SELECT status FROM billing_run_account WHERE run_id = $1 AND billing_account_id = $2 )` |
| 77 | 42,444 | 551.21 | `SELECT DISTINCT ON (c.id) c.id, c.billing_account_id, f.product_id, p.name, s.required - s.available` |
| 1 | 35,206 | 35205.78 | `COPY public.usage_record_y2026m11 (id, network_record_id, kind, quantity, roaming, throttled, msisdn` |
| 5,256 | 32,779 | 6.24 | `EXISTS ( SELECT FROM funding_conversion_bill t JOIN funding_hold_conversion c ON c.hold_id = t.hold_` |
| 7,201 | 26,904 | 3.74 | `INSERT INTO usage_record (id, network_record_id, kind, quantity, roaming, throttled, msisdn, imsi, e` |
| 231 | 19,664 | 85.13 | `SELECT DISTINCT dedupe_key FROM notification_message WHERE dedupe_key = ANY($1)` |
| 14,788 | 16,010 | 1.08 | `SELECT balance_ledger_check(NEW.billing_account_id)` |
| 107,690 | 14,934 | 0.14 | `SELECT b.id, b.bill_date, b.amount_due, b.state, coalesce((SELECT sum(amount) FROM bill_allocation W` |
| 107,690 | 14,356 | 0.13 | `SELECT p.id, p.received_at, p.amount, coalesce((SELECT sum(amount) FROM bill_allocation WHERE paymen` |
| 100,000 | 13,972 | 0.14 | `SELECT p.id, p.product_offering_id, p.status, p.start_date, p.termination_date, p.terminated_by_item` |
| 77 | 12,738 | 165.42 | `SELECT g.id, g.product_id, g.billing_account_id, upper(g.period) AS due, p.name FROM (SELECT DISTINC` |
| 100,580 | 11,970 | 0.12 | `SELECT a.*, r.run_date FROM applied_customer_billing_rate a JOIN billing_run r ON r.id = a.billing_r` |
| 77 | 11,955 | 155.26 | `SELECT g.id, g.product_id, g.billing_account_id, g.due FROM ( SELECT DISTINCT ON (product_id) id, pr` |
| 121,228 | 11,069 | 0.09 | `SELECT id, currency, bill_cycle_day, details, rating_type FROM billing_account WHERE id = $1 FOR UPD` |
| 77 | 10,188 | 132.31 | `SELECT e.id, e.action, e.business_date, e.product_id, e.billing_account_id, p.name FROM prepaid_life` |
| 13,385 | 10,178 | 0.76 | `SELECT a.id, ( (SELECT coalesce(sum(amount), $2) FROM payment WHERE billing_account_id = a.id AND st` |
| 263 | 9,999 | 38.02 | `SELECT status::text, count(*) FROM product GROUP BY 1` |

</details>

### prod-shaped

`prod-shaped-20261114T054630`: the 1x mix at today's production limits: app 0.5 CPU / 384m / 1
process / pool 10, worker 0.25 CPU, postgres 512m with `shared_buffers` 128MB.
**It fails, as expected**; this is the current ceiling.

- For the first 103 s the app ran at its 0.5 CPU limit and answered about 16 requests/s
  (1,640 in all) of the 47/s offered. Its memory grew with the waiting requests from
  109 to 386 MiB.
- At 02:52:38 UTC the kernel killed it (`Memory cgroup out of memory: Killed process
  (uvicorn)`, 386 MB). The load-test container has no restart policy, so the app stayed
  down for the remaining 28 minutes; in production it would restart and be killed again.
- 93.3% of the run's 76,367
  requests failed. The table below is therefore mostly refused connections (0 ms).
- The worker and postgres were not the limit: the feed lag stayed under
  103 s on 0.25 CPU, postgres peaked at 459 MiB.

| Group | Requests | RPS | p50 ms | p95 ms | p99 ms | Errors | In target |
|---|---|---|---|---|---|---|---|
| read | 43,476 | 23.57 | 0 | 0 | 31139.8 | 97.842% | no |
| list | 8,741 | 4.74 | 0 | 0 | 30925.4 | 97.838% | no |
| write | 8,762 | 4.75 | 0 | 0 | 32007.6 | 97.626% | no |
| auth | 67 | 0.04 | 113.6 | 42576.0 | 43269.0 | 41.791% | no |
| ui | 2,990 | 1.62 | 0 | 0 | 0 | 100.000% | no |
| mcp | 3,586 | 1.94 | 25.8 | 157.6 | 10014.7 | 0.000% | no |
| usage-post | 8,742 | 4.74 | 0 | 0 | 31142.1 | 97.849% | no |

| Container | CPU avg / max % | RAM avg / max MiB | RAM, last tenth vs first |
|---|---|---|---|
| postgres | 19.0 / 225.4 | 331.2 / 459.4 | -0.2% |
| app | 2.4 / 52.0 | 7.6 / 255.5 | -100.0% |
| worker | 7.2 / 25.7 | 114.1 / 127.1 | +23.0% |
| mcp | 1.4 / 8.0 | 107.9 / 108.9 | +0.1% |

<details><summary>Top 20 statements by total time</summary>

| Calls | Total ms | Mean ms | Statement |
|---|---|---|---|
| 276 | 269,374 | 975.99 | `INSERT INTO usage_record (id, network_record_id, kind, quantity, roaming, throttled, msisdn, imsi, e` |
| 276 | 34,758 | 125.94 | `SELECT i.n, m.product_id, m.billing_account_id FROM unnest($1::int[], $2::text[], $3::text[], $4::da` |
| 17 | 33,655 | 1979.72 | `SELECT c.* FROM prepaid_command c LEFT JOIN funded_interval f ON f.id::text = c.request->>$4 LEFT JO` |
| 314,500 | 26,565 | 0.08 | `SELECT n.value FROM number_assignment a JOIN subscriber_number n ON n.id = a.subscriber_number_id WH` |
| 17 | 18,309 | 1076.98 | `SELECT DISTINCT ON (c.id) c.id, c.billing_account_id, f.product_id, p.name, s.required - s.available` |
| 276 | 14,654 | 53.09 | `SELECT i.product_id, i.day, f.period_start, f.period_end FROM unnest($1::uuid[], $2::date[]) AS i(pr` |
| 17 | 6,920 | 407.09 | `SELECT g.id, g.product_id, g.billing_account_id, upper(g.period) AS due, p.name FROM (SELECT DISTINC` |
| 17 | 6,378 | 375.19 | `SELECT g.id, g.product_id, g.billing_account_id, g.due FROM ( SELECT DISTINCT ON (product_id) id, pr` |
| 51 | 4,763 | 93.39 | `SELECT DISTINCT dedupe_key FROM notification_message WHERE dedupe_key = ANY($1)` |
| 276 | 4,684 | 16.97 | `SELECT i.product_id, i.day, product_offering_on(i.product_id, i.day) AS id FROM unnest($1::uuid[], $` |
| 17 | 2,691 | 158.30 | `SELECT e.id, e.action, e.business_date, e.product_id, e.billing_account_id, p.name FROM prepaid_life` |
| 26,632 | 1,976 | 0.07 | `SELECT * FROM funded_interval WHERE product_id = $1 ORDER BY lower(period) DESC, authorized_at DESC ` |
| 17 | 1,976 | 116.21 | `SELECT c.id, c.billing_account_id, f.product_id, p.name FROM prepaid_command c JOIN funded_interval ` |
| 17 | 1,947 | 114.55 | `SELECT a.id, a.currency, a.auto_topup_threshold, a.auto_topup_amount FROM billing_account a WHERE a.` |
| 37 | 1,520 | 41.07 | `SELECT * FROM "customer_bill" ORDER BY created_at DESC, id DESC OFFSET $1 LIMIT $2` |
| 189 | 1,494 | 7.91 | `INSERT INTO usage_record (id, network_record_id, kind, quantity, roaming, throttled, msisdn, imsi, e` |
| 248 | 1,401 | 5.65 | `SELECT balance_ledger_check(NEW.billing_account_id)` |
| 731 | 1,310 | 1.79 | `SELECT * FROM "customer" WHERE id = ANY($1)` |
| 241 | 1,299 | 5.39 | `SELECT a.id, ( (SELECT coalesce(sum(amount), $2) FROM payment WHERE billing_account_id = a.id AND st` |
| 17 | 1,270 | 74.68 | `SELECT DISTINCT ON (pc.billing_account_id) pc.id AS command_id, a.id AS account_id, a.currency, s.re` |

</details>

### Thresholds proven

`steady-20261114T111440`: `make loadtest SCENARIO=steady MULT=6 DURATION=3m`. k6 exited 99
with 17 thresholds crossed: the p95/p99 of six groups, the error rate overall and per
group, the checks and the dropped iterations. The runner reported the run failed.
45,178 requests, 31.6% errors,
`read` p95 27,254 ms. The stress run shows the
same at 3x and 5x.

A first attempt at 20x proved nothing: k6 itself was killed at its 2g limit after 63 s
and wrote no summary (the runner reports such a run as failed, with its groups missing).

## Found and left open

- **The first wake-up of a business date can take over an hour.** Unfunded prepaid
  renewals are attempted again once a day. The aged dataset has 24,874 of them; at about
  6 a second the wake-up that crosses midnight took 81 min, and a queued catch-up, the
  feed and the notifications waited for it. The first wake-up after the restore (nine
  missed days) took 2 h 55 min for the same reason. Issue #340.
- **The app does not bound the requests it holds.** Under overload its memory grows with
  the queue until the limit: 1,421 of 1,536 MiB at 5x, and the kill at 384 MiB in
  prod-shaped. Issue #341.
- The shake-out on a 10k dataset was skipped: the scripts were proven directly on the
  100k dataset (smoke, then the diagnostics of 12 November).
- `APP_ENV=test` in the load-test stack: the inventory is in
  [loadtest.md](loadtest.md#the-k6-scenarios-281). It changes the session cookie's
  Secure flag and the callback URL rules, not billing or the measured operations.

## Production sizing recommendation

From laptop measurements, so treat the CPU figures as a floor for what a server core
gives, not as a guarantee.

- **Today's production limits do not carry the 1x peak**: the app's 0.5 CPU answers
  about a third of it, and 384 MiB does not survive the queue.
- **For 100,000 subscribers at the approved peak: a host with 4 dedicated cores and
  8 GB RAM**, with the load-test budget as the container limits:
  - app: 4 processes, pool 15, 3 CPU, 1.5 GB. At 1x it uses 0.6 CPU on average (peaks
    of 1.5 to 2.4) and 0.5 GB; the first break is between 2x and 3x, where it needs all
    3 CPU.
  - postgres: 3 GB with `shared_buffers=1GB` and `max_connections=80`. Keep
    `shared_buffers` at a third of the container's memory or less; 2GB of 3 GB was
    killed. At 1x it uses 0.5 to 0.6 CPU on average with peaks up to 2.8.
  - worker: 1 CPU, 512 MB (0.1 CPU on average at 1x; the daily run of 100k accounts
    takes 22 min). Its memory reached 198 MiB after three hours and was still growing
    26 MiB an hour (the soak's flag), so watch it, and give it a restart policy.
  - mcp: 0.5 CPU, 256 MB (2 calls/s uses 0.01 CPU).
- That leaves a factor of two over the peak before any target is missed, and the daily
  run uses a quarter of its 90-minute window.
- Before going to that load in production: resolve the daily retry of unfunded renewals,
  and bound the app's in-flight requests, so that an overload queues at the proxy
  instead of ending in a kill.
