"""Shared builders of tests/test_enum_lock.py, used by other test modules (#423)."""

import json
from pathlib import Path

import yaml

SPEC_DIR = Path(__file__).resolve().parents[2] / "spec"


def _spec_enum(filename: str, schema: str) -> list[str]:
    path = SPEC_DIR / filename
    assert path.is_file(), f"spec file missing: {path} (is ./spec mounted?)"
    text = path.read_text()
    doc = json.loads(text) if path.suffix == ".json" else yaml.load(text, Loader=yaml.CSafeLoader)
    schemas = doc.get("components", {}).get("schemas") or doc["definitions"]
    return [v.strip() for v in schemas[schema]["enum"]]
