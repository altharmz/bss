---
description: Investigate a misbehaving container before changing any code
---

Something is wrong with the running stack. Investigate before editing anything:

1. `make ps` - which containers are up, which are healthy.
2. `make logs` - read the actual errors.
3. If the database is implicated: `make psql` (needs a TTY) or `make migrate` output.
4. Form a hypothesis and state it explicitly, then confirm it with one more command.

Only after that propose a fix. Do not change code on a guess, and do not restart the
stack as a first move - a restart erases the evidence.
