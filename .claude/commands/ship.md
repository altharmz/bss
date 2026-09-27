---
description: Lint, test, deploy to the VPS and confirm the remote instance is healthy
---

Run the full verification gate for this change:

1. `make lint` — fix anything it reports.
2. `make test` — all tests must pass. If a test fails, read the output before editing code.
3. `make deploy` — builds locally and ships to `vps-test`. Never build on the VPS.
4. If the deploy script fails, read the remote logs it printed and report the root cause.
   Do not retry the deploy more than once without changing something.

Report the final state in three lines: image tag deployed, health status, and anything
that needs the human's attention.
