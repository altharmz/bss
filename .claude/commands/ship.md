---
description: Deploy the committed HEAD to the VPS and confirm the remote instance is healthy
---

The commit was already linted and tested by the pre-commit hook - do not test again.

1. Commit and push anything pending (the hook runs lint + test).
2. `make deploy` - builds locally and ships to `vps-test`; never build on the VPS.
3. If it fails, read the remote logs it printed and report the root cause. Retry at most
   once without changing something (the VPS sometimes stalls new connections).

Report in three lines: image tag deployed, health status, anything needing attention.
