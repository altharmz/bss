#!/usr/bin/env python3
"""Per-issue summary rows and rollups never add partial coverage or unpriced cost."""

import unittest

import task_summary


def task(issue, tokens, cost, complete=True, priced=True, model="claude-opus-5-5", labels=()):
    return {
        "task_id": f"issue-{issue}-x",
        "usage_identities": [{"provider": "anthropic", "client": "claude", "model": model}],
        "tokens": {"total_tokens": tokens},
        "known_cost_by_currency": {"USD": cost} if cost else {},
        "cost_complete": priced,
        "collection_complete": complete,
        "agent_active_seconds": 3600,
        "time_to_merge_seconds": 7200,
        "human_prompts": 2,
        "friction": {"commit": 1, "create-pr": 2},
        "facts": {
            "labels": ["P1", "agent:claude", *labels],
            "priority": "P2",
            "epic": 182,
            "additions": 90,
            "deletions": 10,
            "changed_files": 3,
            "followups": [{"number": 5, "kind": "issue", "labels": ["bug"]}],
            "reverted": False,
        },
        "sample_count": 1,
    }


class SummaryTests(unittest.TestCase):
    def test_row_keeps_area_labels_and_counts_friction_and_bugs(self):
        r = task_summary.row(task(185, 1000, "2.00", labels=("mvno",)))
        self.assertEqual(r["labels"], ["mvno"])
        self.assertEqual(r["priority"], "P2")
        self.assertEqual(r["failed_attempts"], 3)
        self.assertEqual(r["followup_bugs"], 1)

    def test_rollups_add_only_complete_rows(self):
        rows = [
            task_summary.row(task(1, 1000, "2.00")),
            task_summary.row(task(2, 3000, "4.00")),
            task_summary.row(task(3, 999999, "9.00", complete=False)),
            task_summary.row(task(4, 500, None, priced=False)),
        ]
        (group,) = task_summary.rollup(rows, "models")
        self.assertEqual(
            (group["issues"], group["covered_issues"], group["priced_issues"]), (4, 3, 2)
        )
        self.assertEqual(group["tokens"], 4500)
        self.assertEqual(group["cost_usd"], "6.00")
        self.assertEqual(group["cost_per_issue_usd"], "3.00")
        self.assertEqual(group["tokens_per_changed_line"], 15)  # 4500 / 300 lines

    def test_table_marks_partial_rows(self):
        summary = {
            "issues": [task_summary.row(task(3, 1000, "1.00", complete=False))],
            "by_model": [],
            "by_label": [],
            "by_epic": [],
        }
        text = task_summary.table(summary)
        self.assertIn("~0.0M", text)
        self.assertIn("partial", text)
        self.assertIn("#3     P2", text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
