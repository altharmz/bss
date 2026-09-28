# TM Forum Open API specifications

Vendored, unmodified copies of the official TM Forum Open API definitions this project
follows. They are the source of truth for every TMF-derived Postgres enum; a test
(`tests/test_enum_lock.py`) parses these files and compares them with `pg_enum`.

All files come from the [tmforum-apis](https://github.com/tmforum-apis) GitHub org, pinned
to the commit below, and are licensed under the **Apache License 2.0** (per each repo's
`LICENSE` file). Integrity: `sha256sum -c SHA256SUMS`.

| File | API | Version | Source repo | Upstream commit |
|------|-----|---------|-------------|-----------------|
| `TMF620-Product_Catalog_Management-v5.0.0.oas.yaml` | Product Catalog | 5.0.0 | [TMF620_ProductCatalog](https://github.com/tmforum-apis/TMF620_ProductCatalog/blob/24e69f3026f2b514baa27d7206540fc7b55bcb45/TMF620-Product_Catalog_Management-v5.0.0.oas.yaml) | `24e69f3026f2b514baa27d7206540fc7b55bcb45` |
| `TMF622-ProductOrdering-v5.0.0.oas.yaml` | Product Ordering | 5.0.0 | [TMF622_ProductOrder](https://github.com/tmforum-apis/TMF622_ProductOrder/blob/a3825f95248eb04fb1c7e2321305bc99631168a4/TMF622-ProductOrdering-v5.0.0.oas.yaml) | `a3825f95248eb04fb1c7e2321305bc99631168a4` |
| `TMF629-Customer_Management-v5.0.1.oas.yaml` | Customer | 5.0.1 ¹ | [TMF629_CustomerManagement](https://github.com/tmforum-apis/TMF629_CustomerManagement/blob/f60fa840989b88d27fdd0a397c7923f19c9d8168/TMF629-Customer_Management-v5.0.1.oas.yaml) | `f60fa840989b88d27fdd0a397c7923f19c9d8168` |
| `TMF632-Party_Management-v5.0.0.oas.yaml` | Party | 5.0.0 | [TMF632_PartyManagement](https://github.com/tmforum-apis/TMF632_PartyManagement/blob/11c6a0ecf2b9a7007f43e86601b37e45d9f39b1e/TMF632-Party_Management-v5.0.0.oas.yaml) | `11c6a0ecf2b9a7007f43e86601b37e45d9f39b1e` |
| `TMF637-ProductInventory-v5.0.0.oas.yaml` | Product Inventory | 5.0.0 | [TMF637_ProductInventory](https://github.com/tmforum-apis/TMF637_ProductInventory/blob/f598b4a02186071f2794b8f9b4f6904ef0a8aa52/TMF637-ProductInventory-v5.0.0.oas.yaml) | `f598b4a02186071f2794b8f9b4f6904ef0a8aa52` |
| `TMF666-Account_Management-v5.0.0.oas.yaml` | Account | 5.0.0 | [TMF666_AccountManagement](https://github.com/tmforum-apis/TMF666_AccountManagement/blob/8b9c4f62cbf8c41f5b71f5836f48486a3fd856b8/TMF666-Account_Management-v5.0.0.oas.yaml) | `8b9c4f62cbf8c41f5b71f5836f48486a3fd856b8` |
| `TMF678-CustomerBill-v5.0.0.oas.yaml` | Customer Bill | 5.0.0 | [TMF678_CustomerBill](https://github.com/tmforum-apis/TMF678_CustomerBill/blob/6d1628e7ac15f077083216f9bccd469e9f1af74f/TMF678-CustomerBill-v5.0.0.oas.yaml) | `6d1628e7ac15f077083216f9bccd469e9f1af74f` |
| `TMF676-Payment-v4.0.0.swagger.json` | Payment | 4.0.0 ² | [TMF676_PaymentManagement](https://github.com/tmforum-apis/TMF676_PaymentManagement/blob/1de83fd2dc6d09e250c9d1ca7abf3b1b93688d9f/TMF676-Payment-v4.0.0.swagger.json) | `1de83fd2dc6d09e250c9d1ca7abf3b1b93688d9f` |

## Known upstream typos

- `ProductStatusType` (TMF637, and its copy in TMF622) spells the last value `'aborted '`
  with a trailing space. We store `aborted`. The enum lock test compares every value after
  `strip()` - a general rule, not a per-value exception.

## Notes

¹ The TMF629 repo publishes no v5.0.0 file; v5.0.1 is the only v5 definition there.

² TMF676 has no v5 release. The repo also contains
`TMF676_Payment_Management_API_v4.0.0_swagger.json`, whose parsed JSON content is identical
to the file vendored here.
