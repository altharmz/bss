# MVNO features: planned scope

**Planned, not started.** Mobile MVNO features beyond the subscriber numbers already
built. Recorded 2026-10-21; nothing here is developed yet. A data-model plan is proposed
before development (CLAUDE.md working style).

## Status by feature

| Feature | Status | TMF basis |
|---|---|---|
| MSISDN change | **built** (N4-N6, deployed `013da24`) | TMF622 `modify` item with a new `realizingResource`; TMF639 `LogicalResource` ([prepaid-plan.md](prepaid-plan.md#subscriber-numbers-tmf639-mapping)) |
| IMSI change | **planned** (in scope, not developed) | same pattern as MSISDN change, see below |
| Port-in / port-out | not in scope | no TMF Open API; national processes (US: NPAC + ATIS WICIS, usually through the host MNO or a porting bureau) |
| Physical SIM inventory | not in scope (decided 2026-10-21) | logical resources only |
| Network provisioning (TMF640) | not in scope | the BSS records identities; activating them in the network is a separate integration |

## IMSI change

- An IMSI is a TMF639 `LogicalResource` with a built-in `imsi` resource specification,
  like the `msisdn` one; `value` holds the IMSI digits (MCC + MNC + MSIN, at most 15),
  unique. The ICCID of the SIM or eSIM profile is a characteristic of the IMSI resource,
  not a physical resource.
- A product specification that lists the `imsi` resource specification requires one IMSI
  per product, assigned by the TMF622 `add` item through `realizingResource`, exclusively
  (one live product per IMSI).
- IMSI change: a TMF622 `modify` item with a new IMSI in `realizingResource`, effective
  `requestedStartDate`; the MSISDN and billing are unaffected. The old IMSI is released.
- Open for the design: whether a released IMSI is reusable at all (typically it is
  retired with its SIM), and how an `add`/`modify` item carries both an MSISDN and an
  IMSI in `realizingResource`.
