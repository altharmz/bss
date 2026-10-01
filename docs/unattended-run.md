# Unattended run

Work done while the owner was away, after Checkpoint 5 was approved. Scope, in order:
stage 5 checkpoint 6 (catalog), then stage 4 checkpoint 1 (worker design note, no code).
No deploy, no SSH, no vps-infra, no push. Local commits only when `make lint`, `make
test`, `make web-lint` and `make web-test` pass, through the pre-commit hook.

## Commits made

| Commit | What |
|---|---|
| `ab76a12` | make seed: deterministic demo data through the public TMF API |
| `4b652cf` | UI: subscribers list, subscriber card, Add product with backend preview, Deactivate via TMF622 |
| `0419c17` | CLAUDE.md: make seed |

## Decisions taken unattended

(none yet)

## Pending my approval

(none yet)

## Stuck / unfinished

(none yet)

## Manual review list (`make web-dev`, http://localhost:5173, after `make seed`)

1. **Subscribers list**: the table; the state filter with Suspended (Sergey Volkov) and
   Closed (Dmitry Sokolov); a search for "romash" and for "zzz" (No matches + Reset).
2. **Olga Kuznetsova's card**: large negative balance and "In arrears", credit limit 500,
   negative "Available incl. credit". Add product -> Fiber 100: prorated preview with the
   credit-limit warning.
3. **Anna Smirnova's card**: a product with an "ends ..." pill and no Deactivate.
   Deactivate the other one to see the confirm and the toast.
4. **Elena Morozova's card**: a "starts ..." (future-dated) product and an empty charge
   history ("No charges") - her orders are from today.
5. **Sergey Volkov's card**: "+ Add product" disabled with its caption.
6. **Ivan Petrov's card**: a refund in the charge history and a mix of transactions.
7. **Error states**: with web-dev running, `docker compose stop app`, reload (context
   error with the call and Retry), `docker compose start app`, Retry. Loading skeletons:
   throttle the network in devtools.
