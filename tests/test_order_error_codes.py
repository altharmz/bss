"""The operator copy must cover every business refusal emitted by TMF622, including the
account security refusals the order screen takes from `app/security/guard.py` (issue 226)."""

import ast
import json
from pathlib import Path


def test_order_refusal_codes_have_operator_explanations():
    root = Path(__file__).resolve().parents[1]
    codes = set()
    for name in ("process", "numbers", "prepaid", "cancel", "devices"):
        tree = ast.parse((root / "app" / "ordering" / f"{name}.py").read_text())
        for node in ast.walk(tree):
            # Problem(item_id, code, reason), or (code, reason) / (item_id, code, reason).
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "Problem"
                and len(node.args) > 1
            ):
                value = node.args[1]
            elif isinstance(node, ast.Tuple) and len(node.elts) in (2, 3):
                # Ignore action-membership tuples: problem tuples always have a reason
                # that is a string, formatted string, concatenation or reason variable.
                if isinstance(getattr(node, "ctx", None), ast.Store):
                    continue
                value = node.elts[-2]
                if isinstance(value, ast.Constant) and value.value in ("add", "modify"):
                    continue
            else:
                continue
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                codes.add(value.value)
    # numbers.py keeps its per-kind codes in the CODES table
    tree = ast.parse((root / "app" / "ordering" / "numbers.py").read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", "") == "CODES":
            codes |= {c.value for c in ast.walk(node.value) if isinstance(c, ast.Constant)} - {
                "msisdn",
                "imsi",
            }
    # the guard's refusals reach a rejected order's productOrderErrorMessage (and the 403s)
    tree = ast.parse((root / "app" / "security" / "guard.py").read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", "") == "MESSAGES":
            assert isinstance(node.value, ast.Dict)
            codes |= {k.value for k in node.value.keys if isinstance(k, ast.Constant)}
    exported = json.loads((root / "frontend/src/api/orderErrors.codes.json").read_text())
    assert codes == set(exported)
    assert len(exported) == len(set(exported))
