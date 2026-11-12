# Load-test results (#281)

How the runs are made: [loadtest.md](loadtest.md#the-k6-scenarios-281). Each scenario's
full report is `loadtest/out/<scenario>-<time>/report.md` on the workstation (not
committed); this page keeps the numbers.

**Status: blocked, acceptance incomplete.** Smoke passes, but the existing worker feed
cannot sustain the approved usage rate. At 2026-11-12 13:28:01 UTC its log recorded
`queued=69552 made=80552 dropped=0 lag_seconds=2822.4`: 47 minutes behind the five-minute
target. The feed takes at most 500 records per wake-up and waits 60 seconds afterwards,
an upper bound below 8.34/s before other work, against 23/s average and 70/s peak.
Per issue #281's fixed-decisions instruction, the issue waits for a capacity decision;
the load has not been reduced to obtain a pass. The six required measurements are
not complete, and no production capacity conclusion is justified yet.

**Where the numbers come from.** A laptop CPU (AMD Ryzen 5 PRO 4650U, 6 cores / 12
threads) in WSL2 with 15 GB RAM. The system under test (lt-db, lt-app, lt-worker,
lt-mcp) shares a cpuset of 4 logical CPUs (`LOADTEST_SUT_CPUS`), emulating a 4-core host;
k6 runs on 2 other logical CPUs (`LOADTEST_K6_CPUS`). These are not server numbers: a
laptop core in WSL2 with SMT siblings is slower and noisier than a dedicated server vCPU.
The dataset is `seed100k` (100,000 subscribers, 114,978 lines, 6.8M usage records).

**Targets:** per group p95 < 1 s and p99 < 1.5 s; < 0.1 % errors; the 100k daily run
< 90 min; the usage feed lag < 5 min at peak.

## Context

| | |
|---|---|
| Run dates | 2026-11-12, functional checks and diagnostics |
| SUT cpuset / k6 cpuset | `4-7` / `8-9`; `lscpu -e` identifies separate physical cores 2–3 / 4 (each with two SMT threads) |
| Container limits | app 3.0 CPU / 1536m (4 processes, pool 15), worker 3.0 CPU / 1024m, mcp 0.5 / 256m, postgres 3g (`shared_buffers` 2GB) |
| Other containers at start | |

## Scenarios

Per scenario: p50/p95/p99 per group (ms), requests per second, errors, CPU/RAM per
container, the top statements, the verdict.

### smoke

Functional check `smoke-20261112T151322`: **PASS**, 63 requests, zero HTTP errors,
k6 exit 0. This used the settled 100k dataset, with validation work on the same host;
it is a functional check rather than an isolated capacity measurement.

| Group | Requests | p50 ms | p95 ms | p99 ms |
|---|---|---|---|---|
| read | 5 | 46.6 | 55.1 | 55.3 |
| list | 5 | 56.9 | 340.6 | 378.7 |
| write | 9 | 116.6 | 265.2 | 330.5 |
| auth | 21 | 72.7 | 83.2 | 83.2 |
| ui | 14 | 68.4 | 188.9 | 243.7 |
| mcp | 5 | 30.8 | 106.7 | 121.5 |
| usage-post | 1 | 87.4 | 87.4 | 87.4 |

The report includes sampled CPU/RAM and the top 20 statements. Maximum RAM was
2574.3 MiB for postgres, 323.7 MiB app, 87.6 MiB worker and 112.7 MiB MCP.

### steady

Diagnostic `steady-20261112T153219` was interrupted after 25 seconds with exit 137,
before k6 wrote a summary. It provides no latency or throughput verdict. The script
had placed all ID pools inside one `SharedArray` element, causing whole-dataset copies
on access; the pools now share individual IDs/rows. An interrupted run now produces
a failed report instead of crashing on the missing summary.

Corrected diagnostic `steady-20261112T163005`: **FAIL**, k6 exit 99, ten-minute 1x
window (623 seconds including setup/shutdown), 32,737 HTTP requests, 1.775% failures,
165 dropped iterations. This used the settled, previously exercised 100k dataset,
without a fresh restore; it is not the required 30-minute steady measurement.

| Group | Requests | RPS | p50 ms | p95 ms | p99 ms | HTTP failures |
|---|---|---|---|---|---|---|
| read | 14903 | 23.93 | 27.9 | 1481.7 | 8022.0 | 0.121% |
| list | 2979 | 4.78 | 130.2 | 3717.3 | 10556.9 | 0.302% |
| write | 5352 | 8.59 | 62.5 | 1424.4 | 5444.9 | 0.262% |
| auth | 40 | 0.06 | 91.9 | 350.7 | 363.2 | 0% |
| ui | 5281 | 8.48 | 55.9 | 462.9 | 4528.1 | 0.038% |
| mcp | 1197 | 1.92 | 16.6 | 826.1 | 6393.8 | 44.946% |
| usage-post | 2982 | 4.79 | 42.0 | 894.3 | 5492.3 | 0% |

RPS counts HTTP requests, so a five-request new-subscriber operation contributes five
write requests. Setup and graceful completion are included in these wall-time RPS values.
Only auth met both latency targets and its error target. No app 401s were recorded after
the cookie fix. The app log contained 126 pool-timeout matches (log matches, not a count
of unique incidents); recorded API failures were 500s. MCP's default per-principal
limit is 60/min (`bss_mcp/config.py`), below the required 2/s with one service token;
that configuration needs resolution before the mix can pass. The feed reached
16,700 queued records and 619.5 seconds lag, with zero generator drops.

| Container | CPU avg/max % | RAM avg/max MiB |
|---|---|---|
| postgres | 118.3 / 267.4 | 2084.1 / 3067.9 |
| app | 120.7 / 321.4 | 586.8 / 623.5 |
| worker | 32.2 / 59.9 | 85.7 / 104.6 |
| MCP | 1.6 / 5.2 | 106.3 / 106.9 |

The top SQL costs were the balance-ledger check (50.7 s total), usage inserts (28.1 s),
bill list pagination (19.6 s), notification customer lookup (14.0 s), and account
settlement (12.7 s). All 20 statements are in the local report and `pg-stats.txt`.
CPU percentages use Docker's convention (100% = one logical CPU). Docker memory
samples are working-set estimates, not a direct per-process RSS measurement; the
unrun soak's RSS acceptance still needs that distinction resolved.

### stress

Not measured; blocked by the feed capacity finding.

### soak

Not measured; blocked by the feed capacity finding.

### billing-night

Not measured. The supplied `seed100k` snapshot is billed through November 4, so today's
catch-up would bill multiple days. A valid single-day measurement needs yesterday
completed and today unbilled. The worker's automatic schedule also needs coordination
with the API trigger before this scenario can be accepted; the existing startup settle
would otherwise consume today's run. No billing timestamps or accounting rows were
rewritten to manufacture a measurement.

### prod-shaped

Not measured; blocked by the feed capacity finding.

### Thresholds proven

Not yet proven at an absurd arrival rate. The script enforces latency, HTTP failures,
checks and dropped iterations; a complete proof still needs k6 exit 99 and its summary.

## Production sizing recommendation

Do not size production from these functional checks. Resolve the feed throughput
bottleneck, prepare a current snapshot, then repeat the complete scenario matrix.
Increasing CPU alone cannot remove the feed's batch-per-wake-up ceiling.
