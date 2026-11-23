# Monitoring kit

Scrape configuration, alert rules and a Grafana dashboard for the bss app and the MCP
connector. Both serve `GET /metrics` in the Prometheus text format; the series are listed
by the registries in `app/metrics.py` and `bss_mcp/metrics.py`, and a test
(`tests/test_monitoring_kit.py`) fails when the rules or the dashboard name a series that
no registry exports.

| File | What |
|---|---|
| `prometheus-scrape.example.yml` | two jobs, `bss-app` and `bss-mcp`, 60 s interval, bearer token from a file |
| `alerts.yml` | Prometheus rule file: app down, database down, 5xx rate, slow requests, stale billing run, stuck or failed queues, MCP errors, missing scrape telemetry |
| `grafana-cloud-rules.yaml` | the live Grafana Cloud rules: the same set with Grafana's no-data and error handling; `make grafana-rules-check` / `grafana-rules-apply` |
| `grafana-dashboard.json` | importable dashboard; the data source is an import input |

## The token

`/metrics` is off (404) until `BSS_METRICS_TOKEN` is set, on the app and on mcp with the same
value. Generate one with `openssl rand -hex 32`, put it in `.env.prod` and deploy. The
scraper sends it as `Authorization: Bearer <token>`; anything else is a 401. Put the same value
in the scraper's token file (`credentials_file` in the example).

## Scraping

The app listens on `127.0.0.1:18081` on the VPS only. Either scrape from the host itself
(`127.0.0.1:18081/metrics`), or route the paths through the shared Caddy (vps-infra config,
the human's step). In the bss site block, both excluded from basic auth (the bearer token
protects them):

```caddy
handle /metrics {
    reverse_proxy bss:8080
}
handle /mcp-metrics {
    rewrite * /metrics
    reverse_proxy bss-mcp:8080
}
```

Then merge `prometheus-scrape.example.yml` into the scraper's configuration and point the
targets at that host.

## Rules and dashboard

- Self-hosted Prometheus: add `alerts.yml` to `rule_files`. Prometheus resolves an alert whose
  series disappear, so a restart ends and restarts firing alerts; that format cannot say
  otherwise.
- Grafana Cloud: the rules are `grafana-cloud-rules.yaml`, below. Do not import `alerts.yml`
  there: Grafana's import keeps the Prometheus behaviour (no data = Normal).
- Dashboard: Grafana -> Dashboards -> Import -> upload `grafana-dashboard.json`, then pick the
  Prometheus data source.

The worker has no endpoint: its health shows through the queue and billing series.

## Grafana Cloud rules: missing data is not a recovery

The VPS reboots at 03:00 and the app's series are missing for about three minutes. The rules
were first imported from a Prometheus file, which gave them `noDataState: OK`, `execErrState: OK`
and filters such as `bss_queue_oldest_pending_age_seconds > 3600` in PromQL. Every night
`BssQueueStuck` went Alerting -> Normal (NoData) -> Pending -> Alerting: a false resolved
notification, a new firing one and a new repeat cycle (issue #444).

`grafana-cloud-rules.yaml` fixes the state handling, not the thresholds:

| Situation | Behaviour |
|---|---|
| series missing (reboot, failed scrape) | No data, **Keep Last State**: a firing alert stays firing, nothing is sent, a pending one keeps its timer |
| data back, still over the threshold | stays Alerting (same alert, same start, no new notification) |
| data back, under the threshold | Normal, with a resolved notification: recovery needs a real sample |
| query or data source error | **Error**: a `DatasourceError` alert, never a silent Normal |
| scrapes fail (`up` is 0) for 5 min | `BssAppDown` (the 3-minute reboot stays below it) |
| no scrape at all for 15 min | `BssAppTelemetryMissing` / `BssMcpTelemetryMissing` (absence rules: no data = OK, their healthy result) |

Each query returns the raw value and Grafana's threshold expression compares it, so an empty
result only ever means missing series. A test (`tests/test_monitoring_kit.py`) refuses a filter
in PromQL, a rule without Keep Last State (absence rules: OK) and a rule set that differs from
`alerts.yml`.

### Apply (the human's step)

1. Create a service account in Grafana Cloud (Administration -> Users and access -> Service
   accounts) with the **Editor** role, or a custom role with alert rule read and write, and a
   token for it. Put the token in a file outside the repository, e.g.
   `~/.config/bss/grafana-token` (`chmod 600`).
2. `make grafana-rules-check GRAFANA_URL=https://<stack>.grafana.net GRAFANA_TOKEN_FILE=~/.config/bss/grafana-token`
   prints every difference and changes nothing.
3. `make grafana-rules-apply GRAFANA_URL=... GRAFANA_TOKEN_FILE=... CONFIRM=1` writes the group
   (the rules stay editable in the UI), reads it back and fails if anything still differs.

Expect one change in the alert history: Grafana can reset the state of a rule whose queries change,
so a firing alert (today `BssQueueStuck` for `prepaid_command`) is resolved once and fires again
after its pending period. The first 03:00 reboot afterwards is the real test: the rule's
History tab should show no Normal (NoData) transition.

The script stops if the live group has a rule the file does not name (writing would delete
it): add the rule to the file first. Rollback: `git revert` the change and apply again, or
edit a rule in the UI (Configure no data and error handling).

### `BssQueueStuck` on `prepaid_command`

The age of the oldest pending row, from `created_at`. A renewal the customer cannot pay stays
pending by design until a top-up or the line's deactivation (up to grace + suspension days,
[prepaid.md](../../docs/decisions/prepaid.md)), so this alert can fire for a planned wait for
money rather than a stuck worker. Check which row it is before acting on it.
