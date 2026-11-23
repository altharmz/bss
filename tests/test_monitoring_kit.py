"""ops/monitoring: the files parse and name only series the registries export (issue #240); the
Grafana Cloud rules survive missing data without resolving (issue #444)."""

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
    yaml.safe_load((KIT / "grafana-cloud-rules.yaml").read_text())
    json.loads((KIT / "grafana-dashboard.json").read_text())


def test_alerts_and_dashboard_use_only_exported_series(api, db_conn):
    factories.run(db_conn)
    db_conn.commit()
    app_metrics.DB_COLLECTOR._at = None
    unknown = set()
    for name in ("alerts.yml", "grafana-dashboard.json", "grafana-cloud-rules.yaml"):
        unknown |= used((KIT / name).read_text())
    known = exported()
    assert not unknown - known, sorted(unknown - known)


def test_every_alert_has_a_severity_and_a_summary():
    rules = yaml.safe_load((KIT / "alerts.yml").read_text())["groups"][0]["rules"]
    assert len(rules) > 0
    for rule in rules:
        assert rule["labels"]["severity"], rule["alert"]
        assert rule["annotations"]["summary"], rule["alert"]


def grafana_rules() -> list[dict]:
    return yaml.safe_load((KIT / "grafana-cloud-rules.yaml").read_text())["rules"]


def test_grafana_rules_and_prometheus_rules_are_the_same_set():
    prometheus = yaml.safe_load((KIT / "alerts.yml").read_text())["groups"][0]["rules"]
    assert {r["alert"]: r["labels"]["severity"] for r in prometheus} == {
        r["title"]: r["severity"] for r in grafana_rules()
    }
    uids = [r["uid"] for r in grafana_rules()]
    assert len(uids) == len(set(uids))


# a comparison that filters series (`x > 3600`); `x > bool 3600` yields 0 or 1 instead
FILTER = re.compile(r"(==|!=|[<>]=?)(?!=)(?!\s*bool\b)")


def test_grafana_rules_never_read_missing_data_as_recovery():
    """No data keeps the last state, except for absence rules, whose healthy result is empty.
    For that to hold, a healthy value must be a sample, not an empty result: the query
    returns the raw value and the threshold expression compares it."""
    for rule in grafana_rules():
        title, expr = rule["title"], rule["expr"]
        absence = expr.strip().startswith("absent")
        assert rule["no_data"] == ("OK" if absence else "KeepLast"), title
        assert not FILTER.search(expr), (title, expr)
        assert set(rule["threshold"]) <= {"gt", "lt"}, title
        assert rule["summary"], title
