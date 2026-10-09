#!/usr/bin/env bash
# Is this a known /billing-status answer? Used by scripts/smoke.sh (make smoke) and
# scripts/deploy.sh (the remote smoke), tested by scripts/test-billing-status-check.sh.
# Usage: billing-status-check.sh <http code>   (the response body on stdin)
# Exit 0 if the code and the body's status belong together (app/status.py):
#   200 ok | running   (a run in progress, younger than the 2 h grace)
#   503 stale | failed | running | none   (running: overdue)
# Anything else exits 1. The deploy only proves the endpoint and the worker's table work;
# freshness is the external monitor's job, so 503 is a valid answer here.
set -uo pipefail

case "${1:-}" in
  200) grep -Eq '"status": *"(ok|running)"' ;;
  503) grep -Eq '"status": *"(stale|failed|running|none)"' ;;
  *) exit 1 ;;
esac
