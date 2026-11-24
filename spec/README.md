# TM Forum Open API specifications

Vendored, unmodified copies of the official TM Forum Open API definitions this project
follows. They are the source of truth for every TMF-derived Postgres enum; a test
(`tests/schema/test_enum_lock.py`) parses these files and compares them with `pg_enum`.

All files come from the [tmforum-apis](https://github.com/tmforum-apis) GitHub org, pinned
to the commit below, and are licensed under the **Apache License 2.0** (per each repo's
`LICENSE` file).

Three checks, which prove different things:

- **README vs upstream** - `make spec-verify-upstream` re-fetches each file's git blob SHA
  from the GitHub API at the pinned commit and compares it with the SHAs recorded under
  "Upstream blob SHAs". This is the check that ties the recorded SHAs to upstream. It
  needs the network, so it is not part of `make test`; run it after re-vendoring.
- **Files vs README** - `tests/tmf/test_spec_provenance.py` recomputes the blob SHA of every
  local file and compares it with the recorded one. It runs offline in `make test` and
  only ties the files to this README, not to upstream.
- **Later tampering** - `sha256sum -c SHA256SUMS` detects a file that changed after it was
  vendored. `SHA256SUMS` was computed here from the downloaded files, so on its own it
  says nothing about upstream.

| File | API | Version | Source repo | Upstream commit |
|------|-----|---------|-------------|-----------------|
| `TMF620-Product_Catalog_Management-v5.0.0.oas.yaml` | Product Catalog | 5.0.0 | [TMF620_ProductCatalog](https://github.com/tmforum-apis/TMF620_ProductCatalog/blob/24e69f3026f2b514baa27d7206540fc7b55bcb45/TMF620-Product_Catalog_Management-v5.0.0.oas.yaml) | `24e69f3026f2b514baa27d7206540fc7b55bcb45` |
| `TMF622-ProductOrdering-v5.0.0.oas.yaml` | Product Ordering | 5.0.0 | [TMF622_ProductOrder](https://github.com/tmforum-apis/TMF622_ProductOrder/blob/a3825f95248eb04fb1c7e2321305bc99631168a4/TMF622-ProductOrdering-v5.0.0.oas.yaml) | `a3825f95248eb04fb1c7e2321305bc99631168a4` |
| `TMF629-Customer_Management-v5.0.1.oas.yaml` | Customer | 5.0.1 ¹ | [TMF629_CustomerManagement](https://github.com/tmforum-apis/TMF629_CustomerManagement/blob/f60fa840989b88d27fdd0a397c7923f19c9d8168/TMF629-Customer_Management-v5.0.1.oas.yaml) | `f60fa840989b88d27fdd0a397c7923f19c9d8168` |
| `TMF632-Party_Management-v5.0.0.oas.yaml` | Party | 5.0.0 | [TMF632_PartyManagement](https://github.com/tmforum-apis/TMF632_PartyManagement/blob/11c6a0ecf2b9a7007f43e86601b37e45d9f39b1e/TMF632-Party_Management-v5.0.0.oas.yaml) | `11c6a0ecf2b9a7007f43e86601b37e45d9f39b1e` |
| `TMF635-Product_Usage_Management-v5.0.0.oas.yaml` | Product Usage | 5.0.0 | [TMF635_UsageManagement](https://github.com/tmforum-apis/TMF635_UsageManagement/blob/82ea3c6415351b1725b4fd94bcd80ca08425425f/TMF635-Product_Usage_Management-v5.0.0.oas.yaml) | `82ea3c6415351b1725b4fd94bcd80ca08425425f` |
| `TMF637-ProductInventory-v5.0.0.oas.yaml` | Product Inventory | 5.0.0 | [TMF637_ProductInventory](https://github.com/tmforum-apis/TMF637_ProductInventory/blob/f598b4a02186071f2794b8f9b4f6904ef0a8aa52/TMF637-ProductInventory-v5.0.0.oas.yaml) | `f598b4a02186071f2794b8f9b4f6904ef0a8aa52` |
| `TMF666-Account_Management-v5.0.0.oas.yaml` | Account | 5.0.0 | [TMF666_AccountManagement](https://github.com/tmforum-apis/TMF666_AccountManagement/blob/8b9c4f62cbf8c41f5b71f5836f48486a3fd856b8/TMF666-Account_Management-v5.0.0.oas.yaml) | `8b9c4f62cbf8c41f5b71f5836f48486a3fd856b8` |
| `TMF681-Communication-v4.0.0.swagger.json` | Communication | 4.0.0 | [TMF681_Communication](https://github.com/tmforum-apis/TMF681_Communication/blob/057a390f6c20ec3176a66aec0573c44b7f70711e/TMF681-Communication-v4.0.0.swagger.json) | `057a390f6c20ec3176a66aec0573c44b7f70711e` |
| `TMF683-Party_Interaction-v5.0.0.oas.yaml` | Party Interaction | 5.0.0 | [TMF683_PartyInteraction](https://github.com/tmforum-apis/TMF683_PartyInteraction/blob/500f0cc099188e89e50a062b1c38d1af25e171bb/TMF683-Party_Interaction-v5.0.0.oas.yaml) | `500f0cc099188e89e50a062b1c38d1af25e171bb` |
| `TMF621-Trouble_Ticket-v5.0.1.oas.yaml` | Trouble Ticket | 5.0.1 ³ | [TMF621_TroubleTicket](https://github.com/tmforum-apis/TMF621_TroubleTicket/blob/cd24d2357e28501b4ab0def951150639443c0058/TMF621-Trouble_Ticket-v5.0.1.oas.yaml) | `cd24d2357e28501b4ab0def951150639443c0058` |
| `TMF678-CustomerBill-v5.0.0.oas.yaml` | Customer Bill | 5.0.0 | [TMF678_CustomerBill](https://github.com/tmforum-apis/TMF678_CustomerBill/blob/6d1628e7ac15f077083216f9bccd469e9f1af74f/TMF678-CustomerBill-v5.0.0.oas.yaml) | `6d1628e7ac15f077083216f9bccd469e9f1af74f` |
| `TMF676-Payment-v4.0.0.swagger.json` | Payment | 4.0.0 ² | [TMF676_PaymentManagement](https://github.com/tmforum-apis/TMF676_PaymentManagement/blob/1de83fd2dc6d09e250c9d1ca7abf3b1b93688d9f/TMF676-Payment-v4.0.0.swagger.json) | `1de83fd2dc6d09e250c9d1ca7abf3b1b93688d9f` |
| `TMF670-PaymentMethod-v4.0.0.swagger.json` | Payment Method | 4.0.0 | [TMF670_PaymentMethods](https://github.com/tmforum-apis/TMF670_PaymentMethods/blob/dc8dfeb39382736cb40e79704ee6ba25a588dc3b/TMF670-PaymentMethod-v4.0.0.swagger.json) | `dc8dfeb39382736cb40e79704ee6ba25a588dc3b` |

## Subscriber-number and prepaid contracts

Additional contracts vendored for subscriber numbers and prepaid (N1). Their
operations remain unimplemented; contract fixtures do not imply runtime coverage.

| File | API | Version | Source repo | Upstream commit |
|------|-----|---------|-------------|-----------------|
| `TMF639-Resource_Inventory_Management-v5.0.0.oas.yaml` | Resource Inventory | 5.0.0 | [TMF639_ResourceInventory](https://github.com/tmforum-apis/TMF639_ResourceInventory/blob/2693a854940de3c6e15b1aade24f981d94b75d3a/TMF639-Resource_Inventory_Management-v5.0.0.oas.yaml) | `2693a854940de3c6e15b1aade24f981d94b75d3a` |
| `TMF654-PrepayBalance-v4.0.0.swagger.json` | Prepay Balance | 4.0.0 | [TMF654_PrepayBalanceManagement](https://github.com/tmforum-apis/TMF654_PrepayBalanceManagement/blob/562e655d14d796b8380b4e244a5b601f69d60b64/TMF654-PrepayBalance-v4.0.0.swagger.json) | `562e655d14d796b8380b4e244a5b601f69d60b64` |

## Upstream blob SHAs

The git blob SHA of each file at its pinned commit, as returned by the GitHub API
(`GET /repos/tmforum-apis/<repo>/git/trees/<commit>`, the entry for the file's path).
A blob SHA is `sha1("blob <size>\0" + content)`, so it can be recomputed without git.
This is as trustworthy as the pinned commit and GitHub's answer for it: it shows the files
are byte-identical to upstream at that commit, not that upstream itself is correct.
When re-vendoring a file, update its commit above and its blob SHA here together, then
run `make spec-verify-upstream`.

| File | Upstream blob SHA |
|------|-------------------|
| `TMF620-Product_Catalog_Management-v5.0.0.oas.yaml` | `545dedbca967b0949e2df054268ada76e44fd564` |
| `TMF622-ProductOrdering-v5.0.0.oas.yaml` | `64a933b31d8ad9ce433e174dee330e491319b2e9` |
| `TMF629-Customer_Management-v5.0.1.oas.yaml` | `467d54a24c6f85541980daf82fbdead6ad57868e` |
| `TMF632-Party_Management-v5.0.0.oas.yaml` | `72ae9fdab3f19beadb995f24dccf84e1b62fb784` |
| `TMF635-Product_Usage_Management-v5.0.0.oas.yaml` | `6186bd90b5c9f2dcf2199a2f21fcf2a3a7aee71f` |
| `TMF637-ProductInventory-v5.0.0.oas.yaml` | `637ee2e7e156adf8e640102d9bd951d229ab24e9` |
| `TMF639-Resource_Inventory_Management-v5.0.0.oas.yaml` | `2c34d9772bb937e4aebb59ae71d1a6825df39737` |
| `TMF654-PrepayBalance-v4.0.0.swagger.json` | `60378955ca383e0f2e6427d57d9d9d5ed1ddb349` |
| `TMF681-Communication-v4.0.0.swagger.json` | `16f56e0ce2476cc5e3f756d5b9b98d11bb26a0af` |
| `TMF683-Party_Interaction-v5.0.0.oas.yaml` | `ad22459677788e089e2f5615c5c452a60d98a349` |
| `TMF621-Trouble_Ticket-v5.0.1.oas.yaml` | `645fe4149ab97ba08fd21ad598007727005674d4` |
| `TMF666-Account_Management-v5.0.0.oas.yaml` | `082fb3d09777b8c894283f9c6649c5daa690660f` |
| `TMF678-CustomerBill-v5.0.0.oas.yaml` | `9425d310fda1e739b21595d2b445b7bc6ca6d5cb` |
| `TMF676-Payment-v4.0.0.swagger.json` | `b3fcf10ad03954ddd2619df48bb6907ac96390f7` |
| `TMF670-PaymentMethod-v4.0.0.swagger.json` | `02ac9876a575d7da56afa30ce31c58e0c952c5ae` |

## Known upstream typos

- `ProductStatusType` (TMF637, and its copy in TMF622) spells the last value `'aborted '`
  with a trailing space. We store `aborted`. The enum lock test compares every value after
  `strip()` - a general rule, not a per-value exception.

## Notes

¹ The TMF629 repo publishes no v5.0.0 file; v5.0.1 is the only v5 definition there.

² TMF676 has no v5 release. The repo also contains
`TMF676_Payment_Management_API_v4.0.0_swagger.json`, whose parsed JSON content is identical
to the file vendored here.

³ The TMF621 repo publishes no v5.0.0 file; v5.0.1 is the only v5 definition there. TMF683's
`servers` URL names no API path (`https://serverRoot`); it is served under
`/tmf-api/partyInteractionManagement/v5` (docs/decisions/care.md).
