#!/usr/bin/env python3
"""Board administration targets through make, against the startup fixture's fake gh."""

import json
import unittest
from pathlib import Path

import test_start_task as startup_fixtures

connection = startup_fixtures.connection


def item(number, column="Ready", labels=(), priority="P2", **content):
    row = startup_fixtures.issue(number, priority, **content)
    row["content"]["labels"] = connection([{"name": name} for name in labels])
    row["fieldValueByName"] = {"name": column} if column else None
    return row


class BoardAdminTests(unittest.TestCase):
    def setUp(self):
        self.f = startup_fixtures.StartupTests(methodName="runTest")
        self.addCleanup(self.f.doCleanups)
        self.f.setUp()
        self.calls = Path(self.f.env["BSS_TEST_CALLS"])
        self.calls.write_text("")

    def make(self, target, *args):
        return self.f.call(["make", "--no-print-directory", target, *args], check=False)

    def ok(self, target, *args):
        result = self.make(target, *args)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.strip()

    def json(self, target, *args):
        return json.loads(self.ok(target, "JSON=1", *args))

    def refused(self, target, text, *args):
        result = self.make(target, *args)
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(result.stderr.startswith(f"{target}: "), result.stderr)
        self.assertIn(text, result.stderr)
        self.assertEqual(result.stdout, "")
        return result

    def logged(self):
        return [json.loads(line) for line in self.calls.read_text().splitlines()]

    def writes(self):
        return [
            args
            for args in self.logged()
            if args[:2] in (["issue", "create"], ["issue", "edit"], ["issue", "comment"])
            or any(a.startswith("query=") and "mutation" in a for a in args)
        ]

    def rows(self):
        pages = json.loads(Path(self.f.env["BSS_TEST_BOARD"]).read_text())
        return [r for page in pages for r in page["node"]["items"]["nodes"]]

    def row(self, number):
        return next(r for r in self.rows() if r["content"]["number"] == number)

    def state(self, number):
        return (self.row(number)["fieldValueByName"] or {}).get("name")

    # make board

    def test_board_groups_by_status_in_board_order_without_done(self):
        self.f.board(
            [
                item(5, "Ready", ("docs",), title="Docs"),
                item(6, "Done", title="Finished"),
                item(7, "Inbox", (), "P3", title="Idea"),
                item(8, None, (), None, title="Loose"),
                item(9, "Waiting", ("needs-human",), "P1", title="Blocked"),
                item(10, "Ready", (), "P1", title="Second"),
                item(11, "Doing", ("agent:codex",), title="Busy"),
            ]
        )
        self.assertEqual(
            self.ok("board"),
            "Inbox: 1\n  #7 P3 [] Idea\n"
            "Ready: 2\n  #5 P2 [docs] Docs\n  #10 P1 [] Second\n"
            "Doing: 1\n  #11 P2 [agent:codex] Busy\n"
            "Waiting: 1\n  #9 P1 [needs-human] Blocked\n"
            "No status: 1\n  #8 -- [] Loose",
        )
        items = self.json("board")["items"]
        self.assertEqual([i["issue"] for i in items], [7, 5, 10, 11, 9, 8])
        self.assertEqual(
            items[1],
            {
                "issue": 5,
                "item_id": "item-5",
                "status": "Ready",
                "priority": "P2",
                "labels": ["docs"],
                "title": "Docs",
                "position": 1,
            },
        )
        self.assertEqual(items[-1]["status"], None)
        self.assertEqual(items[-1]["priority"], None)
        self.assertEqual(items[-1]["position"], 4)
        self.assertEqual(self.writes(), [])

    def test_board_without_open_items(self):
        self.f.board([item(6, "Done")])
        self.assertEqual(self.ok("board"), "board: no open items")
        self.assertEqual(self.json("board"), {"items": []})

    def test_wrong_github_host_is_refused(self):
        self.f.env["GH_HOST"] = "github.example"
        self.refused("board", "unexpected GitHub host")
        self.assertEqual(self.logged(), [])

    # make add-task

    def test_add_task_creates_with_labels_and_priority_in_inbox(self):
        self.f.board([item(81, "Ready")])
        body = 'Line one; `ticks` and "quotes"\n- [ ] step\n'
        result = self.json(
            "add-task", "TITLE=New idea", f"BODY={body}", "LABELS= docs, ,ui ", "PRIORITY=P2"
        )
        self.assertEqual(
            result,
            {
                "issue": 82,
                "url": "https://github.com/altharmz/bss/issues/82",
                "title": "New idea",
                "status": "Inbox",
                "priority": "P2",
            },
        )
        create = self.writes()[0]
        self.assertEqual(create[create.index("--body") + 1], body)
        self.assertEqual(
            [create[i + 1] for i, a in enumerate(create) if a == "--label"], ["docs", "ui"]
        )
        self.assertEqual(self.state(82), "Inbox")
        self.assertEqual(self.row(82)["priority"], {"name": "P2"})
        self.assertIn("fd42a13c", " ".join(self.writes()[1]))  # the P2 field option
        self.assertIn("f75ad846", " ".join(self.writes()[2]))  # Inbox set explicitly

    def test_add_task_ready_from_body_file(self):
        body = self.f.base / "body.md"
        body.write_text("From a file\n")
        line = self.ok("add-task", "TITLE=Filed", f"BODY_FILE={body}", "STATUS=Ready")
        self.assertEqual(line, "Created #82: https://github.com/altharmz/bss/issues/82 (Ready)")
        create = self.writes()[0]
        self.assertEqual(create[create.index("--body") + 1], "From a file\n")
        self.assertNotIn("--label", create)
        self.assertEqual(self.state(82), "Ready")

    def test_add_task_validation_writes_nothing(self):
        body = self.f.base / "body.md"
        body.write_text("x\n")
        for args, text in (
            (["BODY=x"], "add-task requires a non-empty TITLE"),
            (["TITLE=t"], "add-task requires exactly one of BODY and BODY_FILE"),
            (["TITLE=t", "BODY=x", f"BODY_FILE={body}"], "exactly one of BODY and BODY_FILE"),
            (["TITLE=t", "BODY=x", "PRIORITY=P0"], "P0 issues are created with make new-pri"),
            (
                ["TITLE=t", "BODY=x", "LABELS=agent:codex"],
                "LABELS must not contain agent labels or P0-P3",
            ),
            (
                ["TITLE=t", "BODY=x", "LABELS=docs,P1"],
                "LABELS must not contain agent labels or P0-P3",
            ),
            (["TITLE=t", "BODY=x", "STATUS=Doing"], "STATUS must be Inbox or Ready"),
            (["TITLE=t", "BODY=x", "PRIORITY=high"], "PRIORITY must be P1, P2 or P3"),
        ):
            with self.subTest(args=args):
                self.refused("add-task", text, *args)
        self.assertEqual(self.logged(), [])

    def test_add_task_adds_the_item_when_board_automation_is_late(self):
        self.f.env["BSS_TEST_FAIL"] = "noadd"
        self.assertEqual(self.json("add-task", "TITLE=Late", "BODY=x")["status"], "Inbox")
        self.assertTrue(any("addProjectV2ItemById" in " ".join(a) for a in self.writes()))
        self.assertEqual(self.state(82), "Inbox")

    def test_add_task_failure_after_creation_names_the_finish_command(self):
        self.f.env["BSS_TEST_FAIL"] = "status"
        self.refused(
            "add-task",
            "issue #82 was created but not placed: ",
            "TITLE=t",
            "BODY=x",
            "STATUS=Ready",
        )
        result = self.make("add-task", "TITLE=t", "BODY=x", "STATUS=Ready")
        self.assertIn("finish with: make move-task ISSUE=83 STATUS=Ready", result.stderr)
        result = self.make("add-task", "TITLE=t", "BODY=x", "PRIORITY=P2")
        self.assertIn(
            "finish with: make move-task ISSUE=84 STATUS=Inbox"
            " and make set-priority ISSUE=84 PRIORITY=P2",
            result.stderr,
        )

    # make move-task

    def test_allowed_moves(self):
        for current in ("Inbox", "Ready", "Waiting", None):
            for target in ("Inbox", "Ready"):
                if current == target:
                    continue
                with self.subTest(current=current, target=target):
                    self.f.board([item(81, current)])
                    result = self.json("move-task", "ISSUE=81", f"STATUS={target}")
                    self.assertEqual(result, {"issue": 81, "previous": current, "status": target})
                    self.assertEqual(self.state(81), target)
        for current in ("Inbox", "Ready", "Doing", None):
            with self.subTest(current=current, target="Waiting"):
                self.f.board([item(81, current)])
                self.json(
                    "move-task", "ISSUE=81", "STATUS=Waiting", "WAITING_ON=x", "UNBLOCKS_WHEN=y"
                )
                self.assertEqual(self.state(81), "Waiting")
        self.f.board([item(81, "Waiting", ("agent:claude",))])
        self.assertEqual(self.ok("move-task", "ISSUE=81", "STATUS=Doing"), "#81: Waiting -> Doing")
        self.assertEqual(self.state(81), "Doing")

    def test_waiting_posts_the_comment_before_the_status_change(self):
        self.f.board([item(81, "Ready")])
        line = self.ok(
            "move-task",
            "ISSUE=81",
            "STATUS=Waiting",
            "WAITING_ON=the human; `review`",
            "UNBLOCKS_WHEN=#300 merges",
        )
        self.assertEqual(line, "#81: Ready -> Waiting")
        comment, change = self.writes()
        self.assertEqual(
            comment,
            [
                "issue",
                "comment",
                "81",
                "--repo",
                "altharmz/bss",
                "--body",
                "Waiting on: the human; `review` — unblocks when: #300 merges",
            ],
        )
        self.assertIn("98f1210e", " ".join(change))

    def test_move_to_the_same_status_writes_nothing(self):
        self.f.board([item(81, "Waiting")])
        self.assertEqual(
            self.ok("move-task", "ISSUE=81", "STATUS=Waiting", "WAITING_ON=a", "UNBLOCKS_WHEN=b"),
            "#81: Waiting -> Waiting",
        )
        self.assertEqual(self.writes(), [])

    def test_move_refusals_write_nothing(self):
        cases = (
            ("Ready", ["ISSUE=x", "STATUS=Ready"], "ISSUE must be an issue number"),
            ("Ready", ["ISSUE=81", "STATUS=Done"], "Done is set by closing the issue"),
            ("Ready", ["ISSUE=81", "STATUS=Later"], "STATUS must be Inbox, Ready, Waiting"),
            ("Ready", ["ISSUE=81", "STATUS=Doing"], "Doing is set by make start-task"),
            ("Ready", ["ISSUE=81", "STATUS=Waiting", "WAITING_ON=x"], "needs WAITING_ON and"),
            ("Doing", ["ISSUE=81", "STATUS=Ready"], "#81: cannot move from Doing to Ready"),
            ("Doing", ["ISSUE=81", "STATUS=Inbox"], "#81: cannot move from Doing to Inbox"),
            ("Waiting", ["ISSUE=81", "STATUS=Doing"], "#81: cannot move from Waiting to Doing"),
        )
        for current, args, text in cases:
            with self.subTest(args=args, current=current):
                self.f.board([item(81, current)])
                self.refused("move-task", text, *args)
        self.f.board([item(81, "Waiting", ("agent:codex", "needs-human"))])
        self.refused("move-task", "cannot move from Waiting to Doing", "ISSUE=81", "STATUS=Doing")
        self.assertEqual(self.writes(), [])

    def test_closed_issue_is_refused(self):
        self.f.board([item(81, "Ready", state="CLOSED")])
        self.refused("move-task", "#81 is closed", "ISSUE=81", "STATUS=Inbox")
        self.assertEqual(self.writes(), [])

    def test_issue_off_the_board_is_added_first(self):
        self.f.board([item(81, "Ready")])
        hidden = item(90, None)
        Path(self.f.env["BSS_TEST_BOARD"] + ".hidden").write_text(json.dumps(hidden))
        self.assertEqual(
            self.ok("move-task", "ISSUE=90", "STATUS=Ready"), "#90: no status -> Ready"
        )
        self.assertEqual(self.state(90), "Ready")

    # make set-priority

    def test_set_priority_requires_matching_approval(self):
        self.f.board([item(81, "Ready")])
        for args in (["ISSUE=81", "PRIORITY=P1"], ["ISSUE=81", "PRIORITY=P1", "HUMAN_APPROVAL=82"]):
            with self.subTest(args=args):
                self.refused(
                    "set-priority", "set-priority needs HUMAN_APPROVAL=<issue number>", *args
                )
        self.assertEqual(self.logged(), [])

    def test_set_priority_sets_the_field_and_leaves_labels_alone(self):
        self.f.board([item(81, "Ready", ("P3", "docs"), "P2")])
        result = self.json("set-priority", "ISSUE=81", "PRIORITY=P1", "HUMAN_APPROVAL=81")
        self.assertEqual(result, {"issue": 81, "previous": "P2", "priority": "P1"})
        self.assertEqual(self.row(81)["priority"], {"name": "P1"})
        labels = {n["name"] for n in self.row(81)["content"]["labels"]["nodes"]}
        self.assertEqual(labels, {"P3", "docs"})
        [write] = self.writes()
        self.assertIn("PVTSSF_lAHOEpM-b84BpHA0zhrP5BI", " ".join(write))
        self.assertIn("08fef3c3", " ".join(write))

    def test_set_priority_without_change_writes_nothing(self):
        self.f.board([item(81, "Ready", (), "P1")])
        self.assertEqual(
            self.ok("set-priority", "ISSUE=81", "PRIORITY=P1", "HUMAN_APPROVAL=81"),
            "#81: priority P1 -> P1",
        )
        self.assertEqual(self.writes(), [])

    def test_set_priority_from_none(self):
        self.f.board([item(81, "Ready", (), None)])
        self.assertEqual(
            self.ok("set-priority", "ISSUE=81", "PRIORITY=P0", "HUMAN_APPROVAL=81"),
            "#81: priority none -> P0",
        )
        self.assertEqual(self.row(81)["priority"], {"name": "P0"})

    # make reorder-task

    def order(self):
        return [r["content"]["number"] for r in self.rows()]

    def test_reorder_requires_matching_approval(self):
        self.f.board([item(81), item(82)])
        for args in (["ISSUE=82"], ["ISSUE=82", "HUMAN_APPROVAL=81"]):
            with self.subTest(args=args):
                self.refused(
                    "reorder-task", "reorder-task needs HUMAN_APPROVAL=<issue number>", *args
                )
        self.assertEqual(self.logged(), [])

    def test_reorder_first_and_after(self):
        self.f.board([item(81), item(82), item(83), item(84)])
        self.assertEqual(
            self.ok("reorder-task", "ISSUE=83", "HUMAN_APPROVAL=83"), "#83: now first on the board"
        )
        self.assertEqual(self.order(), [83, 81, 82, 84])
        result = self.json("reorder-task", "ISSUE=83", "AFTER=82", "HUMAN_APPROVAL=83")
        self.assertEqual(result, {"issue": 83, "after": 82})
        self.assertEqual(self.order(), [81, 82, 83, 84])

    def test_reorder_after_an_unknown_issue(self):
        self.f.board([item(81), item(82)])
        self.refused(
            "reorder-task",
            "#99 is not on the BSS board",
            "ISSUE=82",
            "AFTER=99",
            "HUMAN_APPROVAL=82",
        )
        self.refused(
            "reorder-task",
            "AFTER must be a different issue",
            "ISSUE=82",
            "AFTER=82",
            "HUMAN_APPROVAL=82",
        )
        self.assertEqual(self.writes(), [])


if __name__ == "__main__":
    unittest.main()
