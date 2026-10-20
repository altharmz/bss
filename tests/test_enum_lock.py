"""Every TMF-derived Postgres enum equals the enum in the vendored spec.

Values are compared after str.strip() - a general rule, because upstream has at least one
typo with a trailing space ('aborted ' in ProductStatusType). Order matters too.
A missing spec file is a failure, never a skip.
"""

import json
from pathlib import Path

import yaml

SPEC_DIR = Path(__file__).resolve().parent.parent / "spec"

# Postgres enum type -> (spec file, schema name)
TMF_ENUMS = {
    "customer_bill_on_demand_state_type": (
        "TMF678-CustomerBill-v5.0.0.oas.yaml",
        "CustomerBillOnDemandStateType",
    ),
    "product_status_type": ("TMF637-ProductInventory-v5.0.0.oas.yaml", "ProductStatusType"),
    "product_order_state_type": (
        "TMF622-ProductOrdering-v5.0.0.oas.yaml",
        "ProductOrderStateType",
    ),
    "product_order_item_state_type": (
        "TMF622-ProductOrdering-v5.0.0.oas.yaml",
        "ProductOrderItemStateType",
    ),
    "item_action_type": ("TMF622-ProductOrdering-v5.0.0.oas.yaml", "ItemActionType"),
    "customer_bill_state_type": ("TMF678-CustomerBill-v5.0.0.oas.yaml", "CustomerBillStateType"),
}


def _spec_enum(filename: str, schema: str) -> list[str]:
    path = SPEC_DIR / filename
    assert path.is_file(), f"spec file missing: {path} (is ./spec mounted?)"
    text = path.read_text()
    doc = json.loads(text) if path.suffix == ".json" else yaml.load(text, Loader=yaml.CSafeLoader)
    schemas = doc.get("components", {}).get("schemas") or doc["definitions"]
    return [v.strip() for v in schemas[schema]["enum"]]


def _pg_enum(conn, type_name: str) -> list[str]:
    rows = conn.execute(
        """
        SELECT e.enumlabel FROM pg_enum e JOIN pg_type t ON t.oid = e.enumtypid
        WHERE t.typname = %s ORDER BY e.enumsortorder
        """,
        (type_name,),
    ).fetchall()
    return [r[0] for r in rows]


def test_tmf_enums_match_spec(db_conn):
    compared = 0
    for type_name, (filename, schema) in TMF_ENUMS.items():
        expected = _spec_enum(filename, schema)
        actual = _pg_enum(db_conn, type_name)
        assert actual, f"enum type {type_name} does not exist"
        assert actual == expected, f"{type_name} differs from {filename}#{schema}"
        compared += 1
    assert compared == 6


def test_every_spec_file_is_present():
    listed = [
        line.split()[1]
        for line in (SPEC_DIR / "SHA256SUMS").read_text().splitlines()
        if line.strip()
    ]
    assert len(listed) == 10
    for name in listed:
        assert (SPEC_DIR / name).is_file(), f"spec file missing: {name}"
