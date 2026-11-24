#!/usr/bin/env python3
"""GitHub facts per task: content-free projection, follow-ups and SQL validation."""

import unittest
from unittest.mock import patch

import task_facts

TASK = "issue-185-payment-gateway"
PRIVATE = "private title or body text"


def fake(prs, labels=(), references=(), parent=None, items=()):
    def fetch(*args):
        if args[:2] == ("pr", "list"):
            return prs
        if args[:2] == ("issue", "view"):
            return {"labels": [{"name": n} for n in labels], "body": PRIVATE}
        return {
            "data": {
                "repository": {
                    "issue": {
                        "parent": parent,
                        "projectItems": {"nodes": list(items)},
                        "timelineItems": {"nodes": [{"source": r} for r in references]},
                    }
                }
            }
        }

    return fetch


PR = {
    "number": 211,
    "additions": 4589,
    "deletions": 20,
    "changedFiles": 47,
    "commits": [{"oid": "a"}, {"oid": "b"}],
    "mergedAt": "2026-11-05T19:51:32Z",
    "title": PRIVATE,
}


class FactsTests(unittest.TestCase):
    def test_merged_pr_size_labels_epic_and_followups(self):
        references = [
            # Planning reference before the merge: not a follow-up.
            {
                "__typename": "Issue",
                "number": 182,
                "createdAt": "2026-11-01T00:00:00Z",
                "labels": {"nodes": []},
            },
            {
                "__typename": "Issue",
                "number": 240,
                "createdAt": "2026-11-07T00:00:00Z",
                "labels": {"nodes": [{"name": "bug"}, {"name": "P1"}]},
            },
            {
                "__typename": "PullRequest",
                "number": 241,
                "createdAt": "2026-11-08T00:00:00Z",
                "title": 'Revert "' + PRIVATE + '"',
            },
            {
                "__typename": "PullRequest",
                "number": 211,
                "createdAt": "2026-11-09T00:00:00Z",
                "title": "self",
            },
        ]
        items = [
            {"project": {"id": "PVT_other"}, "fieldValueByName": {"name": "P3"}},
            {"project": {"id": task_facts.PROJECT}, "fieldValueByName": {"name": "P1"}},
        ]
        data = task_facts.facts(TASK, fake([PR], ["mvno"], references, {"number": 182}, items))
        self.assertEqual(
            data,
            {
                "pr_number": 211,
                "additions": 4589,
                "deletions": 20,
                "changed_files": 47,
                "commits": 2,
                "labels": ["mvno"],
                "priority": "P1",
                "epic": 182,
                "merged_at": "2026-11-05T19:51:32Z",
                "followups": [
                    {"number": 240, "kind": "issue", "labels": ["P1", "bug"]},
                    {"number": 241, "kind": "pr"},
                ],
                "reverted": True,
            },
        )
        statement = task_facts.facts_sql(TASK, data)
        self.assertNotIn(PRIVATE, statement)
        self.assertNotIn("Revert", statement)
        self.assertIn("ON CONFLICT (task_id) DO UPDATE", statement)
        self.assertIn("'P1'", statement)

    def test_priority_is_none_when_empty_or_off_the_board(self):
        for items in (
            [],
            [{"project": {"id": "PVT_other"}, "fieldValueByName": {"name": "P0"}}],
            [{"project": {"id": task_facts.PROJECT}, "fieldValueByName": None}],
        ):
            with self.subTest(items=items):
                data = task_facts.facts(TASK, fake([PR], items=items))
                self.assertIsNone(data["priority"])
                self.assertIn("NULL", task_facts.facts_sql(TASK, data))

    def test_unmerged_task_has_no_facts(self):
        self.assertIsNone(task_facts.facts(TASK, fake([])))
        with patch.object(task_facts, "sql") as sql:
            self.assertFalse(task_facts.capture(TASK, fake([])))
        sql.assert_not_called()

    def test_invalid_values_never_reach_sql(self):
        good = task_facts.facts(TASK, fake([PR]))
        for change in (
            {"additions": -1},
            {"epic": "1; DROP"},
            {"merged_at": "yesterday'"},
            {"reverted": "yes"},
            {"priority": "P1'; DROP"},
        ):
            with self.subTest(change=change), self.assertRaises(ValueError):
                task_facts.facts_sql(TASK, {**good, **change})
        with self.assertRaises(ValueError):
            task_facts.facts("issue-1-x'; --", fake([PR]))

    def test_label_names_are_bounded(self):
        self.assertEqual(task_facts.labels([{"name": "ok"}, {"name": "x" * 60}, {}]), ["ok"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
