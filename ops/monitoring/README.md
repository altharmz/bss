# Monitoring kit

Scrape configuration, alert rules and a Grafana dashboard for the bss app and the MCP
connector. Both serve `GET /metrics` in the Prometheus text format; the series are listed
by the registries in `app/metrics.py` and `bss_mcp/metrics.py`, and a test
(`tests/test_monitoring_kit.py`) fails when the rules or the dashboard name a series that
no registry exports.

| File | What |
|---|---|
| `prometheus-scrape.example.yml` | two jobs, `bss-app` and `bss-mcp`, 60 s interval, bearer token from a file |
| `alerts.yml` | Prometheus rule file: app down, database down, 5xx rate, slow requests, stale billing run, stuck or failed queues, MCP errors |
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

- Rules: add `alerts.yml` to `rule_files` (Grafana Cloud: import it as a rule group).
- Dashboard: Grafana -> Dashboards -> Import -> upload `grafana-dashboard.json`, then pick the
  Prometheus data source.

The worker has no endpoint: its health shows through the queue and billing series.
