## Behaviour

| Endpoint | What | Proven by |
|---|---|---|
| `GET /healthz` | `{"status": "ok", "db": "up"}` after a `SELECT 1`; 503 `{"status": "degraded", "db": "down"}` when the database is unreachable. Docker's HEALTHCHECK and `scripts/deploy.sh` gate on it; it must stay cheap. Unauthenticated in Caddy (external monitoring) | `tests/test_health.py::test_healthz_ok_when_db_reachable`, `::test_healthz_degraded_when_db_down` |
| `GET /billing-status` | the external monitor's billing freshness signal: exactly `{date, status, ageSeconds}` of the latest run - `ok`/`running` (200), `stale`/`failed`/`running` (503 once overdue), `none` (503, no run at all). Unauthenticated, so no amounts, names or counts. Not part of `/healthz`. Details in [billing-worker.md](../billing-worker.md#billing-status) | `tests/test_billing_status.py` |
| `GET /` | `{"service": "bss", "status": "ok"}` - the template's status root; excluded from the progress scope | `tests/test_health.py::test_root_reports_service_status` |

FastAPI's default `/docs`, `/redoc` and `/openapi.json` are also served (not in
`app.openapi()`'s own paths, so not listed above); they are not the contract.
