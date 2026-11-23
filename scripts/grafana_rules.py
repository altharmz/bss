"""Compare the Grafana Cloud alert rules with ops/monitoring/grafana-cloud-rules.yaml, or write
them (issue #444). The human's step, like a deploy: it needs the stack URL and a service
account token with alert rule access, neither of which the repository holds.

    GRAFANA_URL=https://<stack>.grafana.net GRAFANA_TOKEN_FILE=~/.config/bss/grafana-token \
        python3 scripts/grafana_rules.py check | apply [--confirm]

`check` reads the live group and prints what differs (exit 1 if anything does). `apply` writes
the group through the provisioning API with `X-Disable-Provenance`, so the rules stay editable
in the UI, then reads it back. The live JSON is the base: fields the file does not manage
(ids, missing-series settings, notification settings) are kept as they are. A live rule that
the file does not name stops the run, because writing the group would delete it.
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
SPEC = ROOT / "ops" / "monitoring" / "grafana-cloud-rules.yaml"
RUNBOOK = "https://github.com/altharmz/bss/blob/master/ops/monitoring/README.md"
EXPR_DATASOURCE = {"type": "__expr__", "uid": "__expr__"}
NO_DATA = {"KeepLast", "OK"}
# instant query evaluated one minute back, as the imported rules did
TIME_RANGE = {"from": 660, "to": 60}


class RulesError(Exception):
    pass


def seconds(duration: str) -> int:
    match = re.fullmatch(r"(\d+)([smh])", duration)
    if not match:
        raise RulesError(f"unsupported duration {duration!r}")
    return int(match[1]) * {"s": 1, "m": 60, "h": 3600}[match[2]]


def load_spec(path: Path = SPEC) -> dict:
    return yaml.safe_load(path.read_text())


def managed(rule: dict, spec: dict) -> dict:
    """The fields the file owns for one rule, in the provisioning API's shape."""
    if rule["no_data"] not in NO_DATA:
        raise RulesError(f"{rule['title']}: no_data must be one of {sorted(NO_DATA)}")
    (op, value), *rest = rule["threshold"].items()
    if rest or op not in ("gt", "lt"):
        raise RulesError(f"{rule['title']}: threshold is one of gt or lt")
    expr = rule["expr"].strip()
    for key in ("app_job", "mcp_job"):
        expr = expr.replace(f"${key}", spec[key])
    source = spec["datasource_uid"]
    return {
        "title": rule["title"],
        "condition": "threshold",
        "data": [
            {
                "refId": "query",
                "queryType": "prometheus",
                "relativeTimeRange": dict(TIME_RANGE),
                "datasourceUid": source,
                "model": {
                    "datasource": {"type": "prometheus", "uid": source},
                    "expr": expr,
                    "instant": True,
                    "range": False,
                    "intervalMs": 1000,
                    "maxDataPoints": 43200,
                    "refId": "query",
                },
            },
            {
                "refId": "threshold",
                "queryType": "",
                "datasourceUid": "__expr__",
                "model": {
                    "datasource": dict(EXPR_DATASOURCE),
                    "type": "threshold",
                    "expression": "query",
                    "conditions": [{"evaluator": {"type": op, "params": [value]}}],
                    "intervalMs": 1000,
                    "maxDataPoints": 43200,
                    "refId": "threshold",
                },
            },
        ],
        "noDataState": rule["no_data"],
        "execErrState": "Error",
        "for": rule["for"],
        "annotations": {"runbook_url": RUNBOOK, "summary": rule["summary"]},
        "labels": {"severity": rule["severity"]},
    }


def build_group(live: dict, spec: dict) -> dict:
    """The group body to PUT: the live group with every rule's managed fields replaced."""
    if live.get("title") != spec["group"]:
        raise RulesError(f"live group is {live.get('title')!r}, the file names {spec['group']!r}")
    live_rules = {rule["uid"]: rule for rule in live.get("rules", [])}
    wanted = [rule["uid"] for rule in spec["rules"]]
    if len(set(wanted)) != len(wanted):
        raise RulesError("duplicate uid in the file")
    unknown = sorted(set(live_rules) - set(wanted))
    if unknown:
        names = ", ".join(f"{live_rules[uid]['title']} ({uid})" for uid in unknown)
        raise RulesError(f"live rules missing from the file (writing would delete them): {names}")
    if not live_rules:
        raise RulesError("the live group has no rules to take the shape from")
    template = next(iter(live_rules.values()))
    body = {key: value for key, value in live.items() if key != "rules"}
    body["interval"] = seconds(spec["interval"])
    body["rules"] = []
    for rule in spec["rules"]:
        if rule["uid"] in live_rules:
            base = copy.deepcopy(live_rules[rule["uid"]])
        else:
            base = copy.deepcopy(template)
            for key in ("id", "updated", "provenance"):
                base.pop(key, None)
            base["uid"] = rule["uid"]
        base.update(managed(rule, spec))
        body["rules"].append(base)
    return body


def project(rule: dict) -> dict:
    """What `check` compares: the managed fields without Grafana's additions."""
    data = []
    for item in rule.get("data", []):
        model = item.get("model", {})
        data.append(
            {
                "refId": item.get("refId"),
                "expr": (model.get("expr") or model.get("expression") or "").strip(),
                "conditions": [
                    condition.get("evaluator") for condition in model.get("conditions", [])
                ],
            }
        )
    return {
        "title": rule.get("title"),
        "condition": rule.get("condition"),
        "data": data,
        "noDataState": rule.get("noDataState"),
        "execErrState": rule.get("execErrState"),
        "for": rule.get("for"),
        "annotations": rule.get("annotations") or {},
        "labels": rule.get("labels") or {},
    }


def differences(live: dict, body: dict) -> list[str]:
    found = []
    if live.get("interval") != body.get("interval"):
        found.append(f"group interval: {live.get('interval')} -> {body.get('interval')}")
    live_rules = {rule["uid"]: rule for rule in live.get("rules", [])}
    for rule in body["rules"]:
        title = rule["title"]
        if rule["uid"] not in live_rules:
            found.append(f"{title}: new rule")
            continue
        old, new = project(live_rules[rule["uid"]]), project(rule)
        for key in new:
            if old[key] != new[key]:
                found.append(f"{title}: {key}: {json.dumps(old[key])} -> {json.dumps(new[key])}")
    return found


class Grafana:
    def __init__(self, url: str, token: str) -> None:
        self.url = url.rstrip("/")
        self.token = token

    def call(self, method: str, path: str, body: dict | None = None, **headers: str):
        request = urllib.request.Request(  # noqa: S310 - https stack URL given by the human
            self.url + path,
            method=method,
            data=None if body is None else json.dumps(body).encode(),
            headers={
                "Authorization": f"Bearer {self.token}",
                "Accept": "application/json",
                "Content-Type": "application/json",
                **headers,
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310
                text = response.read().decode()
        except urllib.error.HTTPError as error:
            detail = error.read().decode(errors="replace")[:500]
            raise RulesError(f"{method} {path}: HTTP {error.code} {detail}") from None
        return json.loads(text) if text else None

    def group_of(self, spec: dict) -> tuple[str, dict]:
        """Find the live group through its first existing rule: its folder uid and contents."""
        for rule in spec["rules"]:
            try:
                live_rule = self.call("GET", f"/api/v1/provisioning/alert-rules/{rule['uid']}")
            except RulesError as error:
                if "HTTP 404" in str(error):
                    continue
                raise
            folder = live_rule["folderUID"]
            group = self.call(
                "GET", f"/api/v1/provisioning/folder/{folder}/rule-groups/{spec['group']}"
            )
            return folder, group
        raise RulesError("none of the file's rule uids exists on the stack")

    def put_group(self, folder: str, body: dict) -> None:
        self.call(
            "PUT",
            f"/api/v1/provisioning/folder/{folder}/rule-groups/{body['title']}",
            body,
            **{"X-Disable-Provenance": "true"},
        )


def connect() -> Grafana:
    url = os.environ.get("GRAFANA_URL", "")
    token_file = os.environ.get("GRAFANA_TOKEN_FILE", "")
    if not url.startswith("https://") or not token_file:
        raise RulesError("set GRAFANA_URL=https://<stack>.grafana.net and GRAFANA_TOKEN_FILE")
    token = Path(token_file).expanduser().read_text().strip()
    if not token:
        raise RulesError(f"{token_file} is empty")
    return Grafana(url, token)


def run(command: str, confirm: bool, grafana: Grafana, spec: dict) -> int:
    folder, live = grafana.group_of(spec)
    body = build_group(live, spec)
    found = differences(live, body)
    for line in found:
        print(line)
    if not found:
        print(f"{spec['group']}: the stack matches the file")
        return 0
    if command == "check":
        return 1
    if not confirm:
        print("not written: run again with --confirm (make grafana-rules-apply CONFIRM=1)")
        return 1
    grafana.put_group(folder, body)
    _, after = grafana.group_of(spec)
    left = differences(after, build_group(after, spec))
    if left:
        print("written, but the stack still differs:", *left, sep="\n")
        return 1
    print(f"{spec['group']}: written and read back, {len(body['rules'])} rules match the file")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=("check", "apply"))
    parser.add_argument("--confirm", action="store_true", help="apply: really write")
    args = parser.parse_args(argv)
    try:
        return run(args.command, args.confirm, connect(), load_spec())
    except RulesError as error:
        print(f"grafana-rules: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
