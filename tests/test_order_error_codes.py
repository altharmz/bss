"""The operator copy must cover every business refusal emitted by TMF622."""

import ast
import json
from pathlib import Path


def test_order_refusal_codes_have_operator_explanations():
    root = Path(__file__).resolve().parents[1]
    codes = set()
    for name in ("process", "numbers", "prepaid", "cancel"):
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
    exported = json.loads((root / "frontend/src/api/orderErrors.codes.json").read_text())
    assert codes == set(exported)
    assert len(exported) == len(set(exported))
