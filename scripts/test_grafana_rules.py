"""grafana_rules.py: the group body, the differences and the check/apply flow (issue #444)."""

import copy
import io
import unittest
from contextlib import redirect_stdout

import grafana_rules as rules

NEW_UID = "bss-usage-default-partition"


def imported_rule(number: int, rule: dict) -> dict:
    """A rule as the Prometheus import left it on the stack: the filter in PromQL, the
    prometheus_math step, no data and errors both OK."""
    return {
        "id": number,
        "uid": rule["uid"],
        "orgID": 1,
        "folderUID": "folder-1",
        "ruleGroup": "bss-operational",
        "title": rule["title"],
        "condition": "threshold",
        "data": [
            {"refId": "query", "model": {"expr": "bss_queue_oldest_pending_age_seconds > 3600"}},
            {"refId": "prometheus_math", "model": {"expression": "is_number($query)"}},
            {
                "refId": "threshold",
                "model": {
                    "expression": "prometheus_math",
                    "conditions": [{"evaluator": {"type": "gt", "params": [0]}}],
                },
            },
        ],
        "noDataState": "OK",
        "execErrState": "OK",
        "for": rule["for"],
        "annotations": {"runbook_url": rules.RUNBOOK, "summary": rule["summary"]},
        "labels": {"__converted_prometheus_rule__": "true", "severity": rule["severity"]},
        "isPaused": False,
        "missingSeriesEvalsToResolve": 1,
        "provenance": "",
    }


def live_group(spec: dict) -> dict:
    present = [rule for rule in spec["rules"] if rule["uid"] != NEW_UID]
    return {
        "title": "bss-operational",
        "folderUid": "folder-1",
        "interval": 60,
        "rules": [imported_rule(n, rule) for n, rule in enumerate(present, 1)],
    }


class FakeGrafana:
    def __init__(self, group: dict) -> None:
        self.group = group
        self.writes = []

    def group_of(self, spec):
        return "folder-1", copy.deepcopy(self.group)

    def put_group(self, folder, body):
        self.writes.append((folder, body))
        self.group = copy.deepcopy(body)


class BuildGroupTests(unittest.TestCase):
    def setUp(self):
        self.spec = rules.load_spec()
        self.live = live_group(self.spec)

    def rule(self, body, title):
        return next(rule for rule in body["rules"] if rule["title"] == title)

    def test_the_queue_rule_gets_the_raw_value_keep_last_and_error(self):
        stuck = self.rule(rules.build_group(self.live, self.spec), "BssQueueStuck")
        self.assertEqual(stuck["noDataState"], "KeepLast")
        self.assertEqual(stuck["execErrState"], "Error")
        self.assertEqual(stuck["condition"], "threshold")
        query, threshold = stuck["data"]
        self.assertEqual(query["model"]["expr"], "bss_queue_oldest_pending_age_seconds")
        self.assertEqual(threshold["model"]["expression"], "query")
        self.assertEqual(
            threshold["model"]["conditions"], [{"evaluator": {"type": "gt", "params": [3600]}}]
        )

    def test_absence_rules_keep_no_data_ok(self):
        body = rules.build_group(self.live, self.spec)
        for title in ("BssAppTelemetryMissing", "BssMcpTelemetryMissing"):
            self.assertEqual(self.rule(body, title)["noDataState"], "OK", title)

    def test_job_placeholders_are_expanded(self):
        down = self.rule(rules.build_group(self.live, self.spec), "BssAppDown")
        self.assertEqual(
            down["data"][0]["model"]["expr"],
            'up{job="integrations/metrics_endpoint/3748204-metrics-endpoint-bss-app"}',
        )

    def test_unmanaged_fields_and_routing_labels_survive(self):
        stuck = self.rule(rules.build_group(self.live, self.spec), "BssQueueStuck")
        self.assertEqual(stuck["id"], 6)
        self.assertEqual(stuck["folderUID"], "folder-1")
        self.assertEqual(stuck["missingSeriesEvalsToResolve"], 1)
        self.assertEqual(stuck["labels"], {"severity": "warning"})
        self.assertEqual(stuck["uid"], "fa35f86b-ef9d-570d-bc62-8b0e28df3517")

    def test_a_rule_new_to_the_stack_takes_the_shape_without_the_template_id(self):
        added = self.rule(rules.build_group(self.live, self.spec), "BssUsageDefaultPartition")
        self.assertEqual(added["uid"], NEW_UID)
        self.assertNotIn("id", added)
        self.assertEqual(added["ruleGroup"], "bss-operational")

    def test_a_live_rule_the_file_does_not_name_stops_the_write(self):
        extra = imported_rule(99, {**self.spec["rules"][0], "uid": "x-1", "title": "ByHand"})
        self.live["rules"].append(extra)
        with self.assertRaisesRegex(rules.RulesError, "ByHand"):
            rules.build_group(self.live, self.spec)

    def test_another_group_name_stops_the_write(self):
        self.live["title"] = "other"
        with self.assertRaises(rules.RulesError):
            rules.build_group(self.live, self.spec)

    def test_differences_name_the_old_state_and_vanish_once_written(self):
        body = rules.build_group(self.live, self.spec)
        found = rules.differences(self.live, body)
        self.assertIn('BssQueueStuck: noDataState: "OK" -> "KeepLast"', found)
        self.assertIn("BssUsageDefaultPartition: new rule", found)
        self.assertEqual(rules.differences(body, rules.build_group(body, self.spec)), [])

    def test_unsupported_settings_are_refused(self):
        bad = copy.deepcopy(self.spec)
        bad["rules"][0]["no_data"] = "Alerting"
        with self.assertRaisesRegex(rules.RulesError, "no_data"):
            rules.build_group(self.live, bad)


class RunTests(unittest.TestCase):
    def setUp(self):
        self.spec = rules.load_spec()

    def run_quiet(self, *args):
        out = io.StringIO()
        with redirect_stdout(out):
            code = rules.run(*args)
        return code, out.getvalue()

    def test_check_reports_and_never_writes(self):
        grafana = FakeGrafana(live_group(self.spec))
        code, out = self.run_quiet("check", True, grafana, self.spec)
        self.assertEqual(code, 1)
        self.assertIn("KeepLast", out)
        self.assertEqual(grafana.writes, [])

    def test_apply_without_confirm_does_not_write(self):
        grafana = FakeGrafana(live_group(self.spec))
        code, out = self.run_quiet("apply", False, grafana, self.spec)
        self.assertEqual(code, 1)
        self.assertIn("--confirm", out)
        self.assertEqual(grafana.writes, [])

    def test_apply_writes_reads_back_and_then_matches(self):
        grafana = FakeGrafana(live_group(self.spec))
        code, out = self.run_quiet("apply", True, grafana, self.spec)
        self.assertEqual(code, 0, out)
        self.assertEqual(len(grafana.writes), 1)
        self.assertIn("read back", out)
        code, out = self.run_quiet("check", False, grafana, self.spec)
        self.assertEqual(code, 0)
        self.assertIn("matches", out)


if __name__ == "__main__":
    unittest.main()
