# Staging data scripts (D1, D3, D4, D5)

Dry-run-first scripts that fix the data of the deployed demo stand
(data-quality items D1-D5 of epic #43). **The human runs them**; a
session never does, and tests run them only against a local throwaway database
(`tests/ops/test_staging_data.py`).

They talk HTTP to a base URL and sign in as the UI does. Credentials come from your shell,
never from a file:

```
export BSS_URL=http://localhost:18081     # the app through `make tunnel`
export BSS_USER=...
read -rs BSS_PASSWORD; export BSS_PASSWORD
```

Each script is a dry run unless you pass `--apply`. It prints every change it would make
(`would: ...`, or `APPLY: ...` when writing) and a count. All are idempotent: after
`--apply`, a second run prints `0 change(s)`. Read the dry-run output first.

| Item | Script | What it does |
|---|---|---|
| D1 | `rename_accounts.py` | Billing accounts whose name does not start with their party's name become `<given> <family> main` (individual) or `<organization> corporate`. PATCHes `name` only. |
| D2 | none | Rated history is immutable and bills were issued. History before 2026-10-21 stays; 2026-10-21 is the cut-over to USD magnitudes. The next stand reset rebuilds history from the USD seed. |
| D3 | `retire_test_data.py` | Offerings and specifications whose name or code matches `^MCP `, `TMF-E2E-`, `Prepaid Verifier`, `MCP Coverage`, `MCP-DEMO-`, `MCP Isolated` become Retired and not sellable. Test billing accounts (same patterns, on the account or its customer) with no open product become Closed. Nothing is deleted. |
| D4 | `seed_numbers.py` | Numbers `12025550100`-`12025550119` (existing ones skipped); the specification "Mobile Voice & Data (numbered)" that requires a number; offerings "Mobile S" `MOB-NS` and "Mobile L" `MOB-NL` on it, priced as the current MOB-S / MOB-L. Existing mobile products cannot get numbers (decision B3). |
| D5 | `catalog_tree.py` | Reuses "Mobile Plans" and "Consumer Catalog 2026"; creates the categories Home Internet and TV and the catalog "Business Catalog 2026"; assigns every active offering to a category by its specification's name. |

Run D4 before D5, so the new offerings are assigned too.

Standard library only (Python 3.9+): run them with `python3 ops/staging-data/<script>.py`.
