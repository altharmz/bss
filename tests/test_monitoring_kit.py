"""ops/monitoring: the files parse and name only series the registries export (issue #240)."""

import json
import re
from pathlib import Path

import yaml

from app import metrics as app_metrics
from bss_mcp import metrics as mcp_metrics
from tests import factories

KIT = Path(__file__).resolve().parent.parent / "ops" / "monitoring"
# what the exposition adds to a family's name
SUFFIXES = ("_total", "_bucket", "_count", "_sum", "_created")


def exported() -> set[str]:
    """Every family the two registries produce, collected live (the database series need a
    database with a billing run, or the age series is omitted)."""
    names = set()
    for registry in (app_metrics.REGISTRY, mcp_metrics.REGISTRY):
        for family in registry.collect():
            names.add(family.name)
            names.update(family.name + suffix for suffix in SUFFIXES)
    return names


def used(text: str) -> set[str]:
    return set(re.findall(r"\bbss_[a-z0-9_]+", text))


def test_the_files_parse():
    yaml.safe_load((KIT / "alerts.yml").read_text())
    yaml.safe_load((KIT / "prometheus-scrape.example.yml").read_text())
    json.loads((KIT / "grafana-dashboard.json").read_text())


def test_alerts_and_dashboard_use_only_exported_series(api, db_conn):
    factories.run(db_conn)
    db_conn.commit()
    app_metrics.DB_COLLECTOR._at = None
    unknown = set()
    for name in ("alerts.yml", "grafana-dashboard.json"):
        unknown |= used((KIT / name).read_text())
    known = exported()
    assert not unknown - known, sorted(unknown - known)


def test_every_alert_has_a_severity_and_a_summary():
    rules = yaml.safe_load((KIT / "alerts.yml").read_text())["groups"][0]["rules"]
    assert len(rules) > 0
    for rule in rules:
        assert rule["labels"]["severity"], rule["alert"]
        assert rule["annotations"]["summary"], rule["alert"]
