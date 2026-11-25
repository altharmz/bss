#!/usr/bin/env python3
"""Host-only startup cases with disposable repositories and fake GitHub writes."""

import contextlib
import fcntl
import io
import json
import os
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import start_task as task
from task_runtime import gh_detail

SCRIPTS = Path(__file__).resolve().parent
MARKER = "startup-fixture-must-never-be-read-or-printed"


def connection(rows):
    return {"totalCount": len(rows), "nodes": rows}


def issue(number=81, priority="P2", **overrides):
    data = {
        "id": f"issue-{number}",
        "number": number,
        "title": "Subscriber search" if number == 81 else f"Task {number}",
        "state": "OPEN",
        "repository": {"nameWithOwner": "altharmz/bss"},
        "labels": connection([]),
        "blockedBy": connection([]),
        "linkedBranches": connection([]),
        "closedByPullRequestsReferences": connection([]),
    }
    data.update(overrides)
    return {
        "id": f"item-{number}",
        "fieldValueByName": {"name": "Ready"},
        "priority": {"name": priority} if priority else None,
        "content": data,
    }


class FreeSocket:
    def bind(self, _address):
        pass

    def close(self):
        pass


class StartupTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="bss-start-task-test-")
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        # Spaces also exercise argv/path handling, including the printed cd command.
        self.primary = self.base / "primary checkout"
        self.remote = self.base / "origin.git"
        self.env = {
            k: v
            for k, v in os.environ.items()
            if not k.startswith(("GIT_", "BSS_SLOT", "BSS_WORKER_", "BSS_TASK_OWNER"))
        }
        self.env.update(
            GIT_CONFIG_GLOBAL="/dev/null",
            GIT_CONFIG_NOSYSTEM="1",
            PYTHONDONTWRITEBYTECODE="1",
            XDG_CONFIG_HOME=str(self.base / "config"),
            BSS_TEST_BOARD=str(self.base / "board.json"),
            BSS_TEST_PRS=str(self.base / "prs.json"),
            BSS_TEST_CALLS=str(self.base / "calls.jsonl"),
            BSS_TASK_AGENT="codex",
            BSS_GH_RETRY_PAUSE="0",
            BSS_BOARD_ADD_WAIT="0",
        )
        self.call(["git", "init", "--bare", "-q", "-b", "master", str(self.remote)], cwd=self.base)
        self.call(["git", "init", "-q", "-b", "master", str(self.primary)], cwd=self.base)
        self.git("config", "user.name", "Startup test")
        self.git("config", "user.email", "startup@example.test")
        (self.primary / "scripts").mkdir()
        for name in (
            "start_task.py",
            "task_board.py",
            "task_repo.py",
            "task_runtime.py",
            "task_testmon.py",
            "task_telemetry.py",
            "worker_slots.py",
            "task_finish.py",
            "task_cleanup.py",
            "task_pr.py",
            "full_suite.py",
            "hook_selection.py",
            "task_git.py",
            "task_quality.py",
            "board_admin.py",
            "install-hooks.py",
            "task.py",
        ):
            shutil.copy2(SCRIPTS / name, self.primary / "scripts" / name)
        shutil.copy2(SCRIPTS.parent / "Makefile", self.primary / "Makefile")
        (self.primary / ".gitignore").write_text(".task/\n.env\n__pycache__/\n")
        (self.primary / "README").write_text("base\n")
        self.git("add", ".")
        self.git("commit", "-qm", "initial")
        self.git("remote", "add", "origin", str(self.remote))
        self.git("push", "-qu", "origin", "master")
        self.original = self.git("rev-parse", "HEAD")
        self.fake_bin = self.base / "bin"
        self.fake_bin.mkdir()
        gh = self.fake_bin / "gh"
        gh.write_text(
            f"#!{sys.executable}\n"
            "import json, os, sys\n"
            "args = sys.argv[1:]\n"
            "with open(os.environ['BSS_TEST_CALLS'], 'a') as output:\n"
            " output.write(json.dumps(args) + '\\n')\n"
            "board_path = os.environ['BSS_TEST_BOARD']\n"
            "with open(board_path) as source: board = json.load(source)\n"
            "rows = [r for p in board for r in p['node']['items']['nodes']]\n"
            "if args[:2] == ['issue', 'create']:\n"
            " number = max(r['content']['number'] for r in rows) + 1\n"
            " labels = [{'name': args[i + 1]} for i, a in enumerate(args) if a == '--label']\n"
            " row = {'id': f'item-{number}', 'fieldValueByName': {'name': 'Inbox'}, 'content': {\n"
            "  'id': f'issue-{number}', 'number': number,\n"
            "  'title': args[args.index('--title') + 1],\n"
            "  'state': 'OPEN', 'repository': {'nameWithOwner': 'altharmz/bss'},\n"
            "  'labels': {'totalCount': len(labels), 'nodes': labels},\n"
            "  'blockedBy': {'totalCount': 0, 'nodes': []},\n"
            "  'linkedBranches': {'totalCount': 0, 'nodes': []},\n"
            "  'closedByPullRequestsReferences': {'totalCount': 0, 'nodes': []}}}\n"
            " if os.environ.get('BSS_TEST_FAIL') in ('noadd', 'midpage'):\n"
            "  with open(board_path + '.hidden', 'w') as output: json.dump(row, output)\n"
            "  print(f'https://github.com/altharmz/bss/issues/{number}')\n"
            "  sys.exit(0)\n"
            " board[0]['node']['items']['nodes'].append(row)\n"
            " for page in board: page['node']['items']['totalCount'] += 1\n"
            " with open(board_path, 'w') as output: json.dump(board, output)\n"
            " print(f'https://github.com/altharmz/bss/issues/{number}')\n"
            " sys.exit(0)\n"
            "if args[:2] == ['issue', 'edit']:\n"
            " if os.environ.get('BSS_TEST_FAIL') == 'claim' and '--add-label' in args:\n"
            "  sys.exit(1)\n"
            " row = next(r for r in rows if str(r['content']['number']) == args[2])\n"
            " labels = row['content']['labels']['nodes']\n"
            " for flag, value in zip(args, args[1:]):\n"
            "  if flag == '--remove-label':\n"
            "   labels[:] = [n for n in labels if n['name'] != value]\n"
            "  if flag == '--add-label': labels.append({'name': value})\n"
            " row['content']['labels']['totalCount'] = len(labels)\n"
            " with open(board_path, 'w') as output: json.dump(board, output)\n"
            " sys.exit(0)\n"
            "if args[:2] == ['issue', 'comment']:\n"
            " sys.exit(0)\n"
            "if args[:2] == ['issue', 'view']:\n"
            " if os.path.exists(board_path + '.hidden'):\n"
            "  with open(board_path + '.hidden') as source: rows.append(json.load(source))\n"
            " row = next(r for r in rows if str(r['content']['number']) == args[2])\n"
            " c = row['content']\n"
            " print(json.dumps({'number': c['number'], 'state': c['state'], 'title': c['title'],\n"
            "  'url': f\"https://github.com/altharmz/bss/issues/{c['number']}\",\n"
            "  'labels': c['labels']['nodes']}))\n"
            " sys.exit(0)\n"
            "document = next(a for a in args if a.startswith('query='))\n"
            "flaky = os.environ.get('BSS_TEST_FLAKY')\n"
            "if flaky and not os.path.exists(flaky):\n"
            " open(flaky, 'w').close()\n"
            " print('HTTP 502: Bad Gateway (https://api.github.com/graphql)', file=sys.stderr)\n"
            " sys.exit(1)\n"
            "if 'addProjectV2ItemById' in document:\n"
            " with open(board_path + '.hidden') as source: row = json.load(source)\n"
            " board[0]['node']['items']['nodes'].append(row)\n"
            " for page in board: page['node']['items']['totalCount'] += 1\n"
            " with open(board_path, 'w') as output: json.dump(board, output)\n"
            " print(json.dumps({'data': {'addProjectV2ItemById': {'item': {'id': row['id']}}}}))\n"
            " sys.exit(0)\n"
            "if 'issue(number' in document:\n"
            " n = next(a[7:] for a in args if a.startswith('number='))\n"
            " print(json.dumps({'data': {'repository': {'issue': {'id': f'issue-{n}'}}}}))\n"
            " sys.exit(0)\n"
            "if 'updateProjectV2ItemPosition' in document:\n"
            " if os.environ.get('BSS_TEST_FAIL') == 'position': sys.exit(1)\n"
            " item = next(a[5:] for a in args if a.startswith('item='))\n"
            " row = next(r for r in rows if r['id'] == item)\n"
            " for page in board:\n"
            "  nodes = page['node']['items']['nodes']\n"
            "  if row in nodes: nodes.remove(row)\n"
            " after = next((a[6:] for a in args if a.startswith('after=')), None)\n"
            " if after is None: board[0]['node']['items']['nodes'].insert(0, row)\n"
            " for page in board if after else []:\n"
            "  nodes = page['node']['items']['nodes']\n"
            "  ids = [r['id'] for r in nodes]\n"
            "  if after in ids: nodes.insert(ids.index(after) + 1, row)\n"
            " with open(board_path, 'w') as output: json.dump(board, output)\n"
            " out = {'updateProjectV2ItemPosition': {'clientMutationId': None}}\n"
            " print(json.dumps({'data': out}))\n"
            " sys.exit(0)\n"
            "if 'mutation' in document:\n"
            " if os.environ.get('BSS_TEST_FAIL') == 'status': sys.exit(1)\n"
            " item = next(a[5:] for a in args if a.startswith('item='))\n"
            " option = next(a[7:] for a in args if a.startswith('option='))\n"
            " row = next(r for r in rows if r['id'] == item)\n"
            " field = next(a[6:] for a in args if a.startswith('field='))\n"
            " if field == 'PVTSSF_lAHOEpM-b84BpHA0zhrP5BI':\n"
            "  row['priority'] = {'name': {'0c8ba284': 'P0', '08fef3c3': 'P1', 'fd42a13c': 'P2',\n"
            "   '1df9b3bf': 'P3'}[option]}\n"
            " else:\n"
            "  row['fieldValueByName'] = {'name': {'47fc9ee4': 'Doing', 'abe5d139': 'Ready',\n"
            "   'f75ad846': 'Inbox', '98f1210e': 'Waiting', '98236657': 'Done'}[option]}\n"
            " with open(board_path, 'w') as output: json.dump(board, output)\n"
            " print(json.dumps({'data': {'updated': True}}))\n"
            " sys.exit(0)\n"
            "cursor = next((a for a in args if a.startswith('cursor=')), None)\n"
            "index = int(cursor.rsplit('-', 1)[1]) if cursor else 0\n"
            "key = 'BSS_TEST_BOARD' if 'items(first:' in document else 'BSS_TEST_PRS'\n"
            "hidden = board_path + '.hidden'\n"
            "midpage = os.environ.get('BSS_TEST_FAIL') == 'midpage' and os.path.exists(hidden)\n"
            "if midpage and key == 'BSS_TEST_BOARD' and cursor:\n"
            " # the board automation adds the issue between page 1 and page 2 of one read\n"
            " with open(hidden) as source: row = json.load(source)\n"
            " os.remove(hidden)\n"
            " board[-1]['node']['items']['nodes'].append(row)\n"
            " for page in board: page['node']['items']['totalCount'] += 1\n"
            " with open(board_path, 'w') as output: json.dump(board, output)\n"
            "with open(os.environ[key]) as source: data = json.load(source)\n"
            "print(json.dumps({'data': data[index]}))\n"
        )
        gh.chmod(0o755)
        self.env["PATH"] = str(self.fake_bin) + os.pathsep + self.env["PATH"]
        self.board([issue()])
        self.prs([])

    def call(self, args, cwd=None, check=True):
        return subprocess.run(  # noqa: S603 - test-owned argv and disposable repositories
            args,
            cwd=cwd or self.primary,
            env=self.env,
            capture_output=True,
            text=True,
            check=check,
            timeout=30,
        )

    def git(self, *args, cwd=None):
        return self.call(["git", *args], cwd=cwd).stdout.strip()

    def board(self, items, views=None, split=None):
        views = views or {
            "totalCount": 1,
            "nodes": [
                {"layout": "BOARD_LAYOUT", "filter": None, "sortByFields": {"totalCount": 0}}
            ],
        }
        self.write_pages(
            "BSS_TEST_BOARD", items, split, lambda page: {"node": {"views": views, "items": page}}
        )

    def prs(self, branches, split=None):
        self.write_pages(
            "BSS_TEST_PRS",
            [{"headRefName": b} for b in branches],
            split,
            lambda page: {"repository": {"pullRequests": page}},
        )

    def write_pages(self, key, rows, split, wrap):
        chunks = [rows] if split is None else [rows[:split], rows[split:]]
        pages = [
            wrap(
                {
                    "totalCount": len(rows),
                    "nodes": chunk,
                    "pageInfo": {
                        "hasNextPage": index < len(chunks) - 1,
                        "endCursor": f"next-{index + 1}",
                    },
                }
            )
            for index, chunk in enumerate(chunks)
        ]
        Path(self.env[key]).write_text(json.dumps(pages))

    def cli(self, *args, cwd=None):
        return self.call(
            ["make", "--no-print-directory", "start-task", *args], cwd=cwd, check=False
        )

    def result(self):
        result = self.cli("JSON=1")
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def rejected(self, text, cwd=None):
        result = self.cli("JSON=1", cwd=cwd)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(text, result.stderr)
        self.assertEqual(result.stdout, "")
        self.assertNotIn(MARKER, result.stdout + result.stderr)
        return result

    def destination(self, number=81, title="Subscriber search"):
        return self.primary.parent / f"{self.primary.name}-agents/worker1/repo"

    def add_worktree(self, branch="issue-70-another-task", destination=None):
        path = destination or self.base / branch
        self.git("worktree", "add", "-qb", branch, str(path))
        return path

    def advance_origin(self):
        writer = self.base / "writer"
        self.call(["git", "clone", "-q", str(self.remote), str(writer)], cwd=self.base)
        self.git("config", "user.name", "Startup test", cwd=writer)
        self.git("config", "user.email", "startup@example.test", cwd=writer)
        (writer / "README").write_text("upstream\n")
        self.git("commit", "-qam", "upstream", cwd=writer)
        self.git("push", "-q", cwd=writer)
        return self.git("rev-parse", "HEAD", cwd=writer)

    def test_primary_required(self):
        linked = self.add_worktree()
        self.rejected("primary checkout", cwd=linked)

    def test_explicit_git_queries_ignore_inherited_hook_selectors(self):
        with patch.dict(
            os.environ,
            {"GIT_DIR": str(self.remote), "GIT_WORK_TREE": str(self.base / "wrong-root")},
        ):
            self.assertEqual(task.worktrees(self.primary), [(self.primary, "master")])

    def test_startup_rejects_inherited_git_selectors(self):
        self.env["GIT_DIR"] = str(self.remote)
        self.rejected("unset inherited GIT_*")

    def test_master_required(self):
        self.git("switch", "-qc", "administration-mistake")
        self.rejected("must be on master")

    def test_untracked_checkout_rejected(self):
        (self.primary / "untracked").touch()
        self.rejected("must be clean")

    def test_staged_checkout_rejected(self):
        (self.primary / "README").write_text("staged\n")
        self.git("add", "README")
        self.rejected("must be clean")

    def test_unstaged_checkout_rejected(self):
        (self.primary / "README").write_text("unstaged\n")
        self.rejected("must be clean")

    def test_fast_forward_and_fresh_branch_base(self):
        expected = self.advance_origin()
        result = self.result()
        self.assertEqual(self.git("rev-parse", "master"), expected)
        self.assertEqual(self.git("rev-parse", "HEAD", cwd=result["worktree"]), expected)
        self.assertNotEqual(expected, self.original)

    def test_a_new_worktree_gets_the_saved_test_selection_record(self):
        (self.primary / ".gitignore").write_text(".task/\n.env\n__pycache__/\n.testmon/\n")
        self.git("commit", "-qam", "ignore the test-selection record")
        self.git("push", "-q", "origin", "master")
        saved = self.primary / ".git/bss-testmon/testmondata"
        saved.parent.mkdir()
        connection = sqlite3.connect(saved)
        connection.execute("CREATE TABLE node (name TEXT)")
        connection.commit()
        connection.close()
        result = self.result()
        self.assertEqual(result["test_selection"], "seeded")
        seeded = Path(result["worktree"]) / ".testmon/.testmondata"
        self.assertTrue(seeded.is_file())
        self.assertEqual(self.git("status", "--porcelain", cwd=result["worktree"]), "")

    def test_a_record_git_does_not_ignore_is_not_seeded(self):
        saved = self.primary / ".git/bss-testmon/testmondata"
        saved.parent.mkdir()
        sqlite3.connect(saved).close()
        result = self.result()
        self.assertEqual(result["test_selection"], "not seeded (not ignored)")
        self.assertFalse((Path(result["worktree"]) / ".testmon").exists())

    def test_without_a_saved_record_the_worktree_starts_without_one(self):
        result = self.result()
        self.assertEqual(result["test_selection"], "none")
        self.assertFalse((Path(result["worktree"]) / ".testmon").exists())

    def test_local_ahead_is_not_reset_or_used_as_task_base(self):
        (self.primary / "README").write_text("local\n")
        self.git("commit", "-qam", "local work")
        local = self.git("rev-parse", "HEAD")
        self.rejected("cannot fast-forward")
        self.assertEqual(self.git("rev-parse", "HEAD"), local)
        self.assertFalse(self.destination().exists())

    def test_divergence_does_not_merge_or_rebase(self):
        (self.primary / "README").write_text("local\n")
        self.git("commit", "-qam", "local work")
        local = self.git("rev-parse", "HEAD")
        remote = self.advance_origin()
        self.rejected("cannot fast-forward")
        self.assertEqual(self.git("rev-parse", "HEAD"), local)
        self.assertEqual(self.git("rev-parse", "origin/master"), remote)

    def test_incoming_tracked_secret_cannot_overwrite_primary_or_enter_worktree(self):
        protected = self.primary / ".env"
        protected.write_text(MARKER)
        self.advance_origin()
        writer = self.base / "writer"
        (writer / ".env").write_text("upstream-test-fixture")
        self.git("add", "-f", ".env", cwd=writer)
        self.git("commit", "-qm", "fixture with forbidden path", cwd=writer)
        self.git("push", "-q", cwd=writer)
        self.rejected("refusing tracked secret paths")
        self.assertEqual(protected.read_text(), MARKER)
        self.assertEqual(self.git("rev-parse", "master"), self.original)
        self.assertFalse(self.destination().exists())

    def test_priority_then_board_position_not_issue_number(self):
        self.board(
            [issue(1, "P3"), issue(2, "P2"), issue(3, "P1"), issue(95, "P0"), issue(4, "P0")]
        )
        self.assertEqual(self.result()["issue"], 95)

    def test_requested_issue_requires_matching_explicit_approval_before_startup(self):
        for args in (
            ("ISSUE=81",),
            ("ISSUE=81", "HUMAN_APPROVAL=82"),
            ("ISSUE=81", "HUMAN_APPROVAL=1"),
            ("HUMAN_APPROVAL=81",),
            ("ISSUE=0", "HUMAN_APPROVAL=0"),
            ("ISSUE=-1", "HUMAN_APPROVAL=-1"),
            ("ISSUE=abc", "HUMAN_APPROVAL=abc"),
        ):
            with self.subTest(args=args):
                result = self.cli("JSON=1", *args)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(result.stdout, "")
                self.assertFalse(Path(self.env["BSS_TEST_CALLS"]).exists())
                self.assertFalse(self.destination().exists())

    def test_approved_issue_overrides_order_without_changing_other_items(self):
        self.board([issue(95, "P0"), issue(81, "P2")])
        result = self.cli("JSON=1", "ISSUE=81", "HUMAN_APPROVAL=81")
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(result.stdout)
        self.assertEqual(data["issue"], 81)
        self.assertEqual(data["human_approval"], 81)
        rows = json.loads(Path(self.env["BSS_TEST_BOARD"]).read_text())
        items = rows[0]["node"]["items"]["nodes"]
        self.assertEqual(items[0], issue(95, "P0"))
        self.assertEqual(items[1]["fieldValueByName"]["name"], "Doing")
        self.assertEqual(
            items[1]["content"]["labels"],
            connection([{"name": "agent:codex"}]),
        )
        self.assertEqual(items[1]["priority"], {"name": "P2"})

    def test_requested_ineligible_issue_never_falls_back_to_another_ready_issue(self):
        cases = [
            None,
            issue(81, state="CLOSED"),
            issue(81, labels=connection([{"name": "blocked"}])),
            issue(81, labels=connection([{"name": "needs-human"}])),
            issue(81, labels=connection([{"name": "agent:claude"}])),
            issue(81, blockedBy=connection([{"state": "OPEN"}])),
            issue(81, linkedBranches=connection([{"ref": {"name": "other"}}])),
            issue(81, closedByPullRequestsReferences=connection([{"state": "OPEN"}])),
        ]
        for status in ("Inbox", "Doing", "Waiting", "Done"):
            row = issue(81)
            row["fieldValueByName"]["name"] = status
            cases.append(row)
        for row in cases:
            with self.subTest(row=row):
                self.board([issue(95, "P0"), *([row] if row else [])])
                result = self.cli("JSON=1", "ISSUE=81", "HUMAN_APPROVAL=81")
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("requested issue #81 is not an eligible Ready issue", result.stderr)
                self.assertFalse(self.destination().exists())
        self.board([issue(95, "P0"), issue(81)])
        self.git("branch", "issue-81-existing")
        result = self.cli("JSON=1", "ISSUE=81", "HUMAN_APPROVAL=81")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("no fallback selected", result.stderr)
        self.assertFalse(self.destination().exists())

    def test_closed_blocked_owned_non_ready_and_open_relationships_are_excluded(self):
        cases = [
            issue(1, "P0", state="CLOSED"),
            issue(2, "P0", labels=connection([{"name": "blocked"}])),
            issue(3, "P0", labels=connection([{"name": "needs-human"}])),
            issue(4, "P0", labels=connection([{"name": "agent:codex"}])),
            issue(5, "P0", labels=connection([{"name": "agent:claude"}])),
            issue(6, "P0", blockedBy=connection([{"state": "OPEN"}])),
            issue(7, "P0", linkedBranches=connection([{"ref": {"name": "custom-task"}}])),
            issue(8, "P0", closedByPullRequestsReferences=connection([{"state": "OPEN"}])),
        ]
        for number, status in enumerate(["Doing", "Waiting", "Inbox", "Done"], 9):
            item = issue(number, "P0")
            item["fieldValueByName"]["name"] = status
            cases.append(item)
        self.board([*cases, issue(99, "P3")])
        self.assertEqual(self.result()["issue"], 99)

    def test_no_eligible_issue(self):
        self.board([])
        self.rejected("no eligible Ready issue")
        self.assertEqual(len(task.worktrees(self.primary)), 1)

    def test_duplicate_order_is_ambiguous(self):
        self.board([issue(), issue()])
        self.rejected("ambiguous")

    def test_unknown_priority_value_is_refused(self):
        self.board([issue(81, "P4")])
        self.rejected("#81: unknown Priority field value 'P4'")

    def test_priority_comes_from_the_field_not_labels(self):
        self.board([issue(10, "P3", labels=connection([{"name": "P0"}])), issue(11, "P1")])
        self.assertEqual(self.result()["issue"], 11)

    def test_empty_priority_counts_as_p3_then_board_order(self):
        self.board([issue(11, None), issue(10, "P3"), issue(12, "P2")])
        self.assertEqual(self.result()["issue"], 12)
        self.board([issue(11, None), issue(10, "P3")])
        self.assertEqual(self.result()["issue"], 11)
        self.board([issue(10, "P3"), issue(11, None)])
        self.assertEqual(self.result()["issue"], 10)

    def test_ambiguous_board_view(self):
        self.board([issue()], views={"totalCount": 2, "nodes": []})
        self.rejected("ambiguous board order")

    def test_paginate_board_and_prs_before_selection(self):
        self.board([issue(81, "P0"), issue(82, "P0"), issue(83, "P1")], split=1)
        self.prs(["unrelated", "issue-81-already-active"], split=1)
        self.assertEqual(self.result()["issue"], 82)

    def test_incomplete_relationships_fail_closed(self):
        self.board([issue(blockedBy={"totalCount": 101, "nodes": []})])
        self.rejected("incomplete issue relationships")

    def test_slug_generation_and_limit(self):
        self.assertEqual(
            task.branch_name(81, "Subscriber Search / V2!"), "issue-81-subscriber-search-v2"
        )
        self.assertEqual(task.branch_name(81, "École — billing"), "issue-81-ecole-billing")
        self.assertEqual(task.branch_name(81, "日本語"), "issue-81-task")
        name = task.branch_name(81, "a-" * 100)
        self.assertLessEqual(len(name), 80)
        self.assertFalse(name.endswith("-"))

    def test_existing_local_branch_excludes_issue_without_deletion(self):
        self.git("branch", "issue-81-previous-name")
        self.rejected("no eligible Ready issue")
        self.assertEqual(self.git("rev-parse", "issue-81-previous-name"), self.original)

    def test_branch_appearing_after_selection_is_not_deleted(self):
        choose = task.select_issue

        def concurrent_branch(items, represented, requested=None):
            selected = choose(items, represented, requested)
            self.git("branch", task.branch_name(selected["number"], selected["title"]))
            return selected

        with (
            patch.dict(os.environ, self.env, clear=True),
            contextlib.chdir(self.primary),
            patch.object(task, "select_issue", concurrent_branch),
            self.assertRaisesRegex(task.StartupError, "branch collision"),
        ):
            task.start()
        self.assertEqual(self.git("rev-parse", "issue-81-subscriber-search"), self.original)
        self.assertFalse(self.destination().exists())

    def test_branch_creation_failure_has_clear_cleanup_diagnostic(self):
        lock = self.primary / ".git/refs/heads/issue-81-subscriber-search.lock"
        lock.touch()
        self.rejected("no worktree created; review branch issue-81-subscriber-search")
        self.assertFalse(self.destination().exists())
        self.assertEqual(self.git("branch", "--format=%(refname:short)"), "master")

        self.assertEqual(task.status(self.remote_item()), "Ready")
        self.assertNotIn("agent:codex", task.ownership(self.remote_item()))
        self.assertTrue(lock.exists())

    def test_existing_remote_branch_excludes_issue(self):
        self.git("branch", "issue-81-previous-name")
        self.git("push", "-q", "origin", "issue-81-previous-name")
        self.git("branch", "-d", "issue-81-previous-name")
        self.rejected("no eligible Ready issue")

    def test_open_pr_task_branch_excludes_issue(self):
        self.prs(["issue-81-without-a-closing-reference"])
        self.rejected("no eligible Ready issue")

    def test_existing_path_collision_preserves_contents(self):
        self.env["BSS_SLOT"] = "worker1"
        destination = self.destination()
        destination.mkdir(parents=True)
        (destination / "keep").write_text("untouched")
        self.rejected("occupied")
        self.assertEqual((destination / "keep").read_text(), "untouched")

    def test_registered_worktree_collision_is_preserved(self):
        self.env["BSS_SLOT"] = "worker1"
        destination = self.destination()
        self.add_worktree(destination=destination)
        self.rejected("overlaps a registered worktree")
        self.assertEqual(
            self.git("branch", "--show-current", cwd=destination), "issue-70-another-task"
        )

    def test_active_worktree_reservations_are_not_reused_even_when_stopped(self):
        linked = self.add_worktree()
        (linked / ".task").mkdir()
        (linked / ".task/runtime.json").write_text(
            json.dumps(task.runtime("issue-70-another-task", [21000, 21001, 21002]))
        )
        with (
            patch.dict(os.environ, self.env, clear=True),
            contextlib.chdir(self.primary),
            patch.object(task.socket, "socket", FreeSocket),
        ):
            result = task.start()
        self.assertEqual(
            [result[key] for key in ("app_port", "mcp_port", "web_port")], [21003, 21004, 21005]
        )

    def test_invalid_active_reservations_fail_closed(self):
        linked = self.add_worktree()
        (linked / ".task").mkdir()
        (linked / ".task/runtime.json").write_text(MARKER)
        self.rejected("cannot safely read runtime reservations")
        self.assertTrue(linked.exists())
        self.assertFalse(self.destination().exists())

    def test_allocator_first_free_block_and_occupied_port_skipping(self):
        class OccupiedSocket(FreeSocket):
            def bind(self, address):
                if address[1] == 21001:
                    raise OSError("occupied")

        with patch.object(task.socket, "socket", OccupiedSocket):
            with task.allocate(set()) as ports:
                self.assertEqual(ports, [21003, 21004, 21005])
        with patch.object(task.socket, "socket", FreeSocket):
            with task.allocate(set()) as ports:
                self.assertEqual(ports, [21000, 21001, 21002])

    def test_actual_host_listener_is_skipped_and_not_killed(self):
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
            with patch.object(task, "PORT_START", port), patch.object(task, "PORT_END", port + 90):
                with task.allocate(set()) as ports:
                    self.assertNotIn(port, ports)
            self.assertEqual(listener.getsockname()[1], port)

    def test_reserved_ports_and_exhaustion(self):
        with (
            patch.object(task, "PORT_START", 8442),
            patch.object(task, "PORT_END", 8450),
            patch.object(task.socket, "socket", FreeSocket),
        ):
            with task.allocate(set()) as ports:
                self.assertEqual(ports, [8445, 8446, 8447])
        with (
            self.assertRaisesRegex(task.StartupError, "no free three-port block"),
            task.allocate(set(range(task.PORT_START, task.PORT_END + 1))),
        ):
            self.fail("exhausted range allocated")

    def test_runtime_json_exact_schema_and_json_output(self):
        result = self.result()
        metadata = json.loads((Path(result["worktree"]) / ".task/runtime.json").read_text())
        self.assertEqual(
            metadata,
            task.runtime(
                result["branch"], [result[k] for k in ("app_port", "mcp_port", "web_port")]
            ),
        )
        self.assertEqual(set(metadata), {"version", "task_id", "app_port", "mcp_port", "web_port"})
        self.assertEqual((result["app_port"] - 21000) % 3, 0)
        self.assertLessEqual(result["web_port"], 21998)
        self.assertTrue(result["secret_exists"])
        self.assertEqual(Path(result["worktree"]), self.destination())
        self.assertEqual(self.git("status", "--porcelain"), "")

    def test_secret_existence_only_and_ignored_primary_secret_untouched(self):
        secret = self.base / "config/bss/tasks/issue-81-subscriber-search/.env"
        secret.parent.mkdir(parents=True)
        secret.write_text("POSTGRES_PASSWORD=" + MARKER)
        primary_secret = self.primary / ".env"
        primary_secret.write_text(MARKER)
        original = Path.open

        def no_secret_read(path, mode="r", *args, **kwargs):
            if path.name == ".env":
                raise AssertionError("startup opened a secret file")
            return original(path, mode, *args, **kwargs)

        with (
            patch.dict(os.environ, self.env, clear=True),
            contextlib.chdir(self.primary),
            patch.object(Path, "open", no_secret_read),
        ):
            result = task.start()
        self.assertTrue(result["secret_exists"])
        self.assertEqual(result["secret_file"], str(secret))
        self.assertNotIn(MARKER, json.dumps(result))
        self.assertEqual(secret.read_text(), "POSTGRES_PASSWORD=" + MARKER)
        self.assertEqual(primary_secret.read_text(), MARKER)

    def test_relative_and_internal_secret_paths_fail(self):
        for config in ("relative", str(self.primary / ".task/config")):
            with self.subTest(config=config):
                self.env["XDG_CONFIG_HOME"] = config
                self.rejected("external")

    def test_rollback_after_runtime_write_failure(self):
        secret = self.base / "config/bss/tasks/issue-81-subscriber-search/.env"
        secret.parent.mkdir(parents=True)
        secret.write_text(MARKER)
        writer = task.write_runtime

        def fail_after_write(output, data):
            writer(output, data)
            raise OSError("injected metadata failure")

        errors = io.StringIO()
        with (
            patch.dict(os.environ, self.env, clear=True),
            contextlib.chdir(self.primary),
            patch.object(task, "write_runtime", fail_after_write),
            contextlib.redirect_stderr(errors),
            self.assertRaises(OSError),
        ):
            task.start()
        self.assertIn("rolled back", errors.getvalue())
        self.assertFalse(self.destination().exists())
        self.assertEqual(self.git("branch", "--format=%(refname:short)"), "master")
        self.assertEqual(len(task.worktrees(self.primary)), 1)
        self.assertEqual(secret.read_text(), MARKER)

    def test_output_failure_also_rolls_back_created_resources(self):
        def fail_report(result):
            self.assertTrue((Path(result["worktree"]) / ".task/runtime.json").is_file())
            raise OSError("injected output failure")

        errors = io.StringIO()
        with (
            patch.dict(os.environ, self.env, clear=True),
            contextlib.chdir(self.primary),
            contextlib.redirect_stderr(errors),
            self.assertRaises(OSError),
        ):
            task.start(on_ready=fail_report)
        self.assertIn("rolled back", errors.getvalue())
        self.assertFalse(self.destination().exists())
        self.assertEqual(self.git("branch", "--format=%(refname:short)"), "master")

    def test_rollback_after_partial_worktree_creation_failure(self):
        hook = self.primary / ".git/hooks/post-checkout"
        hook.write_text("#!/bin/sh\nexit 1\n")
        hook.chmod(0o755)
        self.rejected("rolled back")
        self.assertFalse(self.destination().exists())
        self.assertEqual(self.git("branch", "--format=%(refname:short)"), "master")

    def test_rollback_preserves_unexpected_worktree_changes(self):
        hook = self.primary / ".git/hooks/post-checkout"
        hook.write_text("#!/bin/sh\necho keep > unexpected\nexit 1\n")
        hook.chmod(0o755)
        self.rejected("preserved changed task")
        self.assertEqual((self.destination() / "unexpected").read_text().strip(), "keep")

    def test_human_output_and_github_transaction(self):
        result = self.cli()
        self.assertEqual(result.returncode, 0, result.stderr)
        for fragment in [
            "Slot: worker1",
            "Issue: #81",
            "Subscriber search",
            "Branch: issue-81-subscriber-search",
            "Worktree:",
            "App port:",
            "MCP port:",
            "Web port:",
            "Secret file:",
            "Secret exists: yes",
            "Secret created: yes",
            "Claim: agent:codex / Doing",
            "Next: cd '",
        ]:
            self.assertIn(fragment, result.stdout)
        calls = Path(self.env["BSS_TEST_CALLS"]).read_text().splitlines()
        self.assertTrue(calls)
        self.assertTrue(any("mutation" in line for line in calls))

    def remote_item(self):
        return json.loads(Path(self.env["BSS_TEST_BOARD"]).read_text())[0]["node"]["items"][
            "nodes"
        ][0]

    def test_new_priority_task_creates_ready_then_starts_independently(self):
        body = "Goal: keep a;b and `literal` intact\n\n- [ ] Done"
        result = self.call(
            [
                "make",
                "--no-print-directory",
                "new-priority-task",
                "TITLE=Explicit request",
                f"BODY={body}",
                "LABELS=harness,docs",
                "JSON=1",
            ],
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(result.stdout)
        self.assertEqual((data["issue"], data["title"]), (82, "Explicit request"))
        item = self.remote_item()
        self.assertEqual(item["content"]["number"], 82)
        self.assertEqual(data["status"], "Ready")
        self.assertEqual(data["url"], "https://github.com/altharmz/bss/issues/82")
        self.assertEqual(task.status(item), "Ready")
        self.assertEqual(task.ownership(item), {"harness", "docs"})
        self.assertEqual(task.priority(item), "P0")
        self.assertFalse(self.destination().exists())
        self.assertEqual(self.git("branch", "--format=%(refname:short)"), "master")
        self.assertFalse((self.base / "config/bss/tasks").exists())
        self.assertFalse((self.primary.parent / (self.primary.name + "-agents")).exists())
        calls = [
            json.loads(line) for line in Path(self.env["BSS_TEST_CALLS"]).read_text().splitlines()
        ]
        create = next(call for call in calls if call[:2] == ["issue", "create"])
        self.assertEqual(create[create.index("--body") + 1], body)
        self.assertNotIn("P0", create)
        self.assertEqual(self.result()["issue"], 82)

    def test_new_priority_creation_ignores_doing_items_and_unavailable_slots(self):
        active = issue(90, labels=connection([{"name": "agent:codex"}]))
        active["fieldValueByName"]["name"] = "Doing"
        self.board([issue(), active])
        # Invalid explicit slot would prevent startup, but not issue creation.
        self.env["BSS_SLOT"] = "../unavailable"
        result = self.call(
            [
                "make",
                "--no-print-directory",
                "new-priority-task",
                "TITLE=Independent creation",
                "BODY=Acceptance",
                "JSON=1",
            ]
        )
        self.assertEqual(json.loads(result.stdout)["status"], "Ready")
        self.assertFalse(self.destination().exists())

    def test_new_priority_task_reads_the_body_from_a_file(self):
        # Text that merely mentions a blocked command must not matter when it comes from a file.
        body = (
            "Mentions "
            + "docker volume"
            + " rm and a secret read without running them\n\n- [ ] Done"
        )
        path = self.base / "body.md"
        path.write_text(body)
        result = self.call(
            [
                "make",
                "--no-print-directory",
                "new-priority-task",
                "TITLE=From a file",
                f"BODY_FILE={path}",
                "JSON=1",
            ]
        )
        self.assertEqual(json.loads(result.stdout)["issue"], 82)
        calls = [
            json.loads(line) for line in Path(self.env["BSS_TEST_CALLS"]).read_text().splitlines()
        ]
        create = next(call for call in calls if call[:2] == ["issue", "create"])
        self.assertEqual(create[create.index("--body") + 1], body)
        for extra, message in (
            (["BODY=x", f"BODY_FILE={path}"], "BODY or BODY_FILE"),
            (["BODY_FILE=relative.md"], "absolute path"),
            ([f"BODY_FILE={self.base}"], "regular file"),
        ):
            with self.subTest(extra=extra):
                failed = self.call(
                    ["make", "--no-print-directory", "new-priority-task", "TITLE=t", *extra],
                    check=False,
                )
                self.assertNotEqual(failed.returncode, 0)
                self.assertIn(message, failed.stderr)
        self.assertEqual(self.remote_item()["content"]["number"], 82)

    def test_new_priority_task_adds_the_issue_when_board_automation_is_late(self):
        self.env["BSS_TEST_FAIL"] = "noadd"
        result = self.call(
            [
                "make",
                "--no-print-directory",
                "new-priority-task",
                "TITLE=Late automation",
                "BODY=Goal",
                "JSON=1",
            ],
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("adding it directly", result.stderr)
        data = json.loads(result.stdout)
        self.assertEqual((data["issue"], data["status"]), (82, "Ready"))
        item = self.remote_item()
        self.assertEqual(item["content"]["number"], 82)
        self.assertEqual(task.status(item), "Ready")

    def test_new_priority_task_survives_the_board_automation_landing_mid_read(self):
        # Two board pages; the automation adds the new issue between them, so one poll
        # sees the item count change. That is the awaited event, not a failure (#242).
        self.board([issue(81), issue(80, "P3")], split=1)
        self.env.update(BSS_TEST_FAIL="midpage", BSS_BOARD_ADD_WAIT="2")
        result = self.call(
            [
                "make",
                "--no-print-directory",
                "new-priority-task",
                "TITLE=Mid-read automation",
                "BODY=Goal",
                "JSON=1",
            ],
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("adding it directly", result.stderr)
        data = json.loads(result.stdout)
        self.assertEqual((data["issue"], data["status"]), (82, "Ready"))
        item = self.remote_item()
        self.assertEqual(item["content"]["number"], 82)
        self.assertEqual(task.status(item), "Ready")

    def test_preserved_issue_is_finished_without_creating_another(self):
        self.env["BSS_TEST_FAIL"] = "position"
        failed = self.call(
            ["make", "--no-print-directory", "new-priority-task", "TITLE=T", "BODY=B"],
            check=False,
        )
        self.assertIn("make new-priority-task ISSUE=82", failed.stderr)
        self.env.pop("BSS_TEST_FAIL")
        result = self.call(
            ["make", "--no-print-directory", "new-priority-task", "ISSUE=82", "JSON=1"]
        )
        self.assertEqual(json.loads(result.stdout)["issue"], 82)
        self.assertEqual(task.status(self.remote_item()), "Ready")
        calls = [
            json.loads(line) for line in Path(self.env["BSS_TEST_CALLS"]).read_text().splitlines()
        ]
        self.assertEqual(sum(call[:2] == ["issue", "create"] for call in calls), 1)
        for args in (["ISSUE=81"], ["ISSUE=x"], ["ISSUE=82", "TITLE=t"]):
            with self.subTest(args=args):
                refused = self.call(
                    ["make", "--no-print-directory", "new-priority-task", *args], check=False
                )
                self.assertNotEqual(refused.returncode, 0)

    def test_preserved_issue_needs_priority_p0_or_empty(self):
        loose = issue(83, None)
        loose["fieldValueByName"] = None
        self.board([issue(81, "P2"), loose])
        refused = self.call(
            ["make", "--no-print-directory", "new-priority-task", "ISSUE=81"], check=False
        )
        self.assertIn("#81 has Priority P2, not P0", refused.stderr)
        result = self.call(
            ["make", "--no-print-directory", "new-priority-task", "ISSUE=83", "JSON=1"]
        )
        self.assertEqual(json.loads(result.stdout)["issue"], 83)
        item = self.remote_item()
        self.assertEqual((task.status(item), task.priority(item)), ("Ready", "P0"))

    def test_github_call_is_retried_once_and_errors_are_bounded(self):
        self.env["BSS_TEST_FLAKY"] = str(self.base / "flaky")
        self.assertEqual(self.result()["issue"], 81)
        self.assertEqual(
            gh_detail("HTTP 502: Bad Gateway (https://api.github.com/graphql)\nsecond"),
            "HTTP 502: Bad Gateway (<url>)",
        )
        self.assertEqual(gh_detail("token ghp_abcdefghijklmnopqrstuvwxyz0123456789"), "<redacted>")
        self.assertLessEqual(len(gh_detail("x" * 500)), 120)
        self.assertEqual(gh_detail(""), "")

    def test_new_priority_task_rejects_missing_input_and_reserved_labels(self):
        cases = [
            ["BODY=valid"],
            ["TITLE=valid"],
            ["TITLE=valid", "BODY=valid", "LABELS=P1"],
            ["TITLE=valid", "BODY=valid", "LABELS=agent:claude"],
            ["TITLE=valid", "BODY=valid", "LABELS=needs-human"],
        ]
        for args in cases:
            with self.subTest(args=args):
                result = self.call(
                    ["make", "--no-print-directory", "new-priority-task", *args], check=False
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(self.destination().exists())
                self.assertEqual(self.remote_item()["content"]["number"], 81)

    def test_new_priority_task_position_failure_identifies_preserved_issue(self):
        self.env["BSS_TEST_FAIL"] = "position"
        result = self.call(
            [
                "make",
                "--no-print-directory",
                "new-priority-task",
                "TITLE=Explicit request",
                "BODY=Goal and acceptance criteria",
                "JSON=1",
            ],
            check=False,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("new issue #82 is preserved", result.stderr)
        self.assertFalse(self.destination().exists())
        item = next(
            r
            for p in json.loads(Path(self.env["BSS_TEST_BOARD"]).read_text())
            for r in p["node"]["items"]["nodes"]
            if r["content"]["number"] == 82
        )
        self.assertEqual(item["content"]["number"], 82)
        self.assertEqual(task.status(item), "Ready")
        self.assertEqual(task.ownership(item), set())
        self.assertEqual(task.priority(item), "P0")
        self.env.pop("BSS_TEST_FAIL")
        self.assertEqual(self.result()["issue"], 82)

    def test_claim_doing_and_disposable_credential(self):
        result = self.result()
        item = self.remote_item()
        self.assertIn("agent:codex", task.ownership(item))
        self.assertEqual(task.status(item), "Doing")
        secret = Path(result["secret_file"])
        self.assertEqual(secret.stat().st_mode & 0o777, 0o600)
        self.assertEqual(secret.parent.stat().st_mode & 0o777, 0o700)
        values = dict(line.split("=", 1) for line in secret.read_text().splitlines())
        self.assertEqual(set(values), {"POSTGRES_PASSWORD", "BSS_SERVICE_SECRET"})
        for value in values.values():
            self.assertRegex(value, r"^[0-9a-f]{64}$")
            self.assertNotIn(value, json.dumps(result))
        self.assertNotEqual(values["POSTGRES_PASSWORD"], values["BSS_SERVICE_SECRET"])

    def test_failed_claim_and_status_leave_no_local_task(self):
        for failure in ("claim", "status"):
            with self.subTest(failure=failure):
                self.env["BSS_TEST_FAIL"] = failure
                self.rejected("command failed")
                self.assertFalse(self.destination().exists())
                self.assertEqual(task.status(self.remote_item()), "Ready")
                self.assertNotIn("agent:codex", task.ownership(self.remote_item()))

    def test_same_agent_doing_item_does_not_block_startup(self):
        active = issue(90, labels=connection([{"name": "agent:codex"}]))
        active["fieldValueByName"]["name"] = "Doing"
        self.board([issue(), active])
        result = self.result()
        self.assertEqual(result["issue"], 81)
        self.assertEqual(task.status(self.remote_item()), "Doing")
        self.assertIn("agent:codex", task.ownership(self.remote_item()))

    def test_claim_race_after_selection(self):
        choose = task.select_issue

        def race(items, represented, requested=None):
            result = choose(items, represented, requested)
            self.board([issue(labels=connection([{"name": "P2"}, {"name": "agent:claude"}]))])
            return result

        with (
            patch.dict(os.environ, self.env, clear=True),
            contextlib.chdir(self.primary),
            patch.object(task, "select_issue", race),
            self.assertRaises(task.StartupError),
        ):
            task.start()
        self.assertFalse(self.destination().exists())
        self.assertEqual(task.ownership(self.remote_item()), {"P2", "agent:claude"})

    def test_symlinked_secret_refused_without_reading_target(self):
        target = self.primary / ".env"
        target.write_text(MARKER)
        secret = self.base / "config/bss/tasks/issue-81-subscriber-search/.env"
        secret.parent.mkdir(parents=True)
        secret.symlink_to(target)
        self.rejected("symlinked")
        self.assertEqual(target.read_text(), MARKER)

    def test_changed_remote_state_is_preserved_on_failure(self):
        def fail_report(_result):
            item = issue(labels=connection([{"name": "P2"}, {"name": "agent:claude"}]))
            item["fieldValueByName"]["name"] = "Waiting"
            self.board([item])
            raise OSError("output failed")

        with (
            patch.dict(os.environ, self.env, clear=True),
            contextlib.chdir(self.primary),
            contextlib.redirect_stderr(io.StringIO()) as errors,
            self.assertRaises(OSError),
        ):
            task.start(on_ready=fail_report)
        self.assertIn("preserved uncertain remote state", errors.getvalue())
        self.assertEqual(task.status(self.remote_item()), "Waiting")
        self.assertEqual(task.ownership(self.remote_item()), {"P2", "agent:claude"})
        self.assertFalse(self.destination().exists())

    def test_failure_retains_new_credential_and_compensates_remote(self):
        def fail_report(result):
            self.assertTrue(result["secret_created"])
            raise OSError("output failed")

        with (
            patch.dict(os.environ, self.env, clear=True),
            contextlib.chdir(self.primary),
            contextlib.redirect_stderr(io.StringIO()) as errors,
            self.assertRaises(OSError),
        ):
            task.start(on_ready=fail_report)
        secret = self.base / "config/bss/tasks/issue-81-subscriber-search/.env"
        self.assertTrue(secret.exists())
        self.assertNotIn(secret.read_text().split("=", 1)[1].strip(), errors.getvalue())
        self.assertFalse(self.destination().exists())
        self.assertEqual(task.status(self.remote_item()), "Ready")
        self.assertNotIn("agent:codex", task.ownership(self.remote_item()))

    def test_modified_runtime_is_preserved(self):
        def fail_report(result):
            (Path(result["worktree"]) / ".task/runtime.json").write_text("changed")
            raise OSError("output failed")

        with (
            patch.dict(os.environ, self.env, clear=True),
            contextlib.chdir(self.primary),
            contextlib.redirect_stderr(io.StringIO()) as errors,
            self.assertRaises(OSError),
        ):
            task.start(on_ready=fail_report)
        self.assertIn("preserved changed runtime metadata", errors.getvalue())
        self.assertEqual((self.destination() / ".task/runtime.json").read_text(), "changed")

    def test_invocation_lock(self):
        common = Path(self.git("rev-parse", "--path-format=absolute", "--git-common-dir"))
        with (common / "start-task.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            process = subprocess.Popen(
                ["make", "--no-print-directory", "start-task", "JSON=1"],  # noqa: S607
                cwd=self.primary,
                env=self.env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            try:
                time.sleep(0.15)
                self.assertIsNone(process.poll(), "startup must wait for the shared lock")
                fcntl.flock(lock, fcntl.LOCK_UN)
                output, errors = process.communicate(timeout=30)
                self.assertEqual(process.returncode, 0, errors)
                self.assertEqual(json.loads(output)["slot"], "worker1")
            finally:
                if process.poll() is None:
                    process.kill()
                    process.communicate()

    def test_owner_identities_and_validation(self):
        for owner in ("codex", "claude", "kimi", "future-client"):
            with (
                self.subTest(owner=owner),
                patch.dict(os.environ, {**self.env, "BSS_TASK_OWNER": owner}, clear=True),
            ):
                self.assertEqual(task.agent_label(), f"agent:{owner}")
        for owner in (None, "", "Kimi", "../kimi", "kimi_client", "a" * 33):
            environment = {**self.env, "BSS_TASK_OWNER": owner}
            if owner is None:
                for key in ("BSS_TASK_OWNER", "BSS_TASK_AGENT", "CODEX_THREAD_ID", "CLAUDECODE"):
                    environment.pop(key, None)
            with (
                self.subTest(owner=owner),
                patch.dict(os.environ, environment, clear=True),
                self.assertRaisesRegex(task.StartupError, "BSS_TASK_OWNER must be"),
            ):
                task.agent_label()

    def test_bare_make_detects_client_and_selects_persistent_slot(self):
        self.env.pop("BSS_TASK_AGENT", None)
        self.env.pop("CLAUDECODE", None)
        self.env["CODEX_THREAD_ID"] = "fixture-thread"
        data = self.result()
        self.assertEqual((data["slot"], data["agent"]), ("worker1", "agent:codex"))
        self.assertEqual(Path(data["worktree"]), self.destination())
        self.assertTrue((self.destination().parent / "active.json").is_file())

    def test_detection_and_override_are_independent_of_slots(self):
        for client, marker in (("codex", "CODEX_THREAD_ID"), ("claude", "CLAUDECODE")):
            with patch.dict(os.environ, {marker: "fixture"}, clear=True):
                self.assertEqual(task.task_owner(), client)
        with patch.dict(os.environ, {"CODEX_THREAD_ID": "x", "CLAUDECODE": "1"}, clear=True):
            with self.assertRaisesRegex(task.StartupError, "ambiguous client"):
                task.task_owner()
            with patch.dict(os.environ, {"BSS_TASK_OWNER": "kimi"}):
                self.assertEqual(task.task_owner(), "kimi")

    def test_auto_skips_unknown_occupied_registered_symlink_and_locked_slots(self):
        pool = self.destination().parent.parent
        pool.mkdir()
        (pool / "worker1").mkdir()
        (pool / "worker1/repo").mkdir()
        (pool / "worker1/repo/keep").write_text(MARKER)
        (pool / "worker2").mkdir()
        (pool / "worker2/unknown").write_text(MARKER)
        registered = self.add_worktree(destination=pool / "worker3/repo")
        shutil.rmtree(registered)
        (pool / "worker4").symlink_to(self.primary, target_is_directory=True)
        (pool / "worker5").mkdir()
        (pool / "worker6/state").mkdir(parents=True)
        (pool / "worker6/state/keep").write_text(MARKER)
        with (pool / "worker5/slot.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            data = self.result()
        self.assertEqual(data["slot"], "worker6")
        self.assertEqual((pool / "worker1/repo/keep").read_text(), MARKER)
        self.assertEqual((pool / "worker2/unknown").read_text(), MARKER)
        self.assertEqual((pool / "worker6/state/keep").read_text(), MARKER)
        self.assertTrue((pool / "worker4").is_symlink())
        self.assertIn(registered, dict(task.worktrees(self.primary)))

    def test_auto_exhaustion_reports_every_candidate_without_claiming(self):
        pool = self.destination().parent.parent
        for name in ("worker1", "worker2", "worker3", "qa"):
            (pool / name).mkdir(parents=True)
            (pool / name / "active.json").write_text(MARKER)
        result = self.rejected("no usable worker slot")
        for name in ("worker1", "worker2", "worker3", "qa"):
            self.assertIn(name + ":", result.stderr)
            self.assertEqual((pool / name / "active.json").read_text(), MARKER)
        self.assertEqual(task.status(self.remote_item()), "Ready")
        self.assertNotIn("agent:codex", task.ownership(self.remote_item()))

    def test_auto_skips_symlinked_first_slot_and_unconfirmed_stale_reservation(self):
        pool = self.destination().parent.parent
        pool.mkdir()
        (pool / "worker1").symlink_to(self.primary, target_is_directory=True)
        (pool / "worker2").mkdir()
        stale = pool / "worker2/active.json"
        payload = json.dumps({"version": 1, "task_id": "issue-70-abandoned"})
        stale.write_text(payload)
        data = self.result()
        self.assertEqual(data["slot"], "worker3")
        self.assertTrue((pool / "worker1").is_symlink())
        self.assertEqual(stale.read_text(), payload)

    def test_auto_skips_active_task_and_claims_next_issue_and_slot(self):
        first = self.result()
        rows = json.loads(Path(self.env["BSS_TEST_BOARD"]).read_text())[0]["node"]["items"]["nodes"]
        self.board(rows + [issue(82)])
        self.env["BSS_TASK_OWNER"] = "claude"
        second = self.result()
        self.assertEqual((second["slot"], second["issue"]), ("worker2", 82))
        self.assertTrue(Path(first["worktree"]).is_dir())
        self.assertNotEqual(first["app_port"], second["app_port"])

    def test_simultaneous_startups_serialize_issue_and_slot_claims(self):
        self.board([issue(81), issue(82)])
        processes = []
        try:
            for owner in ("codex", "claude"):
                environ = {**self.env, "BSS_TASK_OWNER": owner}
                processes.append(
                    subprocess.Popen(
                        ["make", "--no-print-directory", "start-task", "JSON=1"],  # noqa: S607
                        cwd=self.primary,
                        env=environ,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        text=True,
                    )
                )
            results = []
            for process in processes:
                output, errors = process.communicate(timeout=30)
                self.assertEqual(process.returncode, 0, errors)
                results.append(json.loads(output))
            for key in ("slot", "issue", "branch", "worktree", "app_port", "secret_file"):
                self.assertEqual(len({data[key] for data in results}), 2, key)
            self.assertEqual({data["slot"] for data in results}, {"worker1", "worker2"})
        finally:
            for process in processes:
                if process.poll() is None:
                    process.kill()
                    process.communicate()

    def test_slot_startup_through_make(self):
        result = self.cli("SLOT=worker1", "JSON=1")
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(result.stdout)
        slot = self.base / "primary checkout-agents/worker1"
        self.assertEqual(data["slot"], "worker1")
        self.assertEqual(Path(data["worktree"]), slot / "repo")
        self.assertEqual(self.git("branch", "--show-current", cwd=slot / "repo"), data["branch"])
        self.assertEqual(json.loads((slot / "active.json").read_text())["task_id"], data["branch"])
        self.assertEqual(
            task.validate_metadata(json.loads((slot / "repo/.task/runtime.json").read_text()))[0],
            data["branch"],
        )

    def test_kimi_owner_is_independent_of_worker2(self):
        self.env["BSS_TASK_OWNER"] = "kimi"
        result = self.cli("SLOT=worker2", "JSON=1")
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(result.stdout)
        self.assertEqual(data["agent"], "agent:kimi")
        self.assertEqual(data["slot"], "worker2")
        self.assertEqual(Path(data["worktree"]), self.base / "primary checkout-agents/worker2/repo")
        self.assertIn("agent:kimi", task.ownership(self.remote_item()))

    def test_slot_lock_rejects_another_invocation(self):
        self.env["BSS_SLOT"] = "worker1"
        slot = self.base / "primary checkout-agents/worker1"
        slot.mkdir(parents=True)
        with (slot / "slot.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.rejected("locked by another invocation")
        self.assertEqual(task.status(self.remote_item()), "Ready")

    def test_registered_slot_without_repo_is_not_reused(self):
        self.env["BSS_SLOT"] = "worker1"
        slot = self.base / "primary checkout-agents/worker1"
        repo = self.add_worktree(destination=slot / "repo")
        shutil.rmtree(repo)  # Simulate filesystem loss while Git still registers the worktree.
        self.rejected("overlaps a registered worktree")
        (self.base / "extra").mkdir()
        self.env["BSS_WORKER_ROOT"] = str(self.base / "extra/../primary checkout-agents")
        self.rejected("overlaps a registered worktree")
        self.assertIn(repo, dict(task.worktrees(self.primary)))

    def test_slot_rollback_preserves_modified_worktree_and_reservation(self):
        self.env["BSS_SLOT"] = "worker1"
        slot = self.base / "primary checkout-agents/worker1"

        def fail_report(result):
            (Path(result["worktree"]) / "unexpected").write_text(MARKER)
            raise OSError("injected failure")

        with (
            patch.dict(os.environ, self.env, clear=True),
            contextlib.chdir(self.primary),
            contextlib.redirect_stderr(io.StringIO()) as errors,
            self.assertRaises(OSError),
        ):
            task.start(fail_report)
        self.assertEqual((slot / "repo/unexpected").read_text(), MARKER)
        self.assertTrue((slot / "active.json").exists())
        self.assertIn("preserved slot reservation", errors.getvalue())

    def test_unsafe_slots_fail_before_claim(self):
        for name in (
            "../worker1",
            "/foo",
            "worker/1",
            ".",
            "..",
            "Worker1",
            "a" * 33,
            "worker1;touch injected",
            "worker1`touch injected`",
        ):
            with self.subTest(name=name):
                self.env["BSS_SLOT"] = name
                self.rejected("slot must be")
                self.assertEqual(task.status(self.remote_item()), "Ready")
        self.assertFalse((self.primary / "injected").exists())

    def test_slot_occupancy_and_symlinks_preserved(self):
        self.env["BSS_SLOT"] = "worker1"
        slot = self.base / "primary checkout-agents/worker1"
        slot.mkdir(parents=True)
        repo = slot / "repo"
        repo.mkdir()
        (repo / "keep").write_text(MARKER)
        self.rejected("occupied")
        self.assertEqual((repo / "keep").read_text(), MARKER)
        (repo / "keep").unlink()
        repo.rmdir()
        (slot / "active.json").write_text(MARKER)
        self.rejected("occupied")
        (slot / "active.json").unlink()
        repo.symlink_to(self.primary, target_is_directory=True)
        self.rejected("occupied")
        self.assertTrue(repo.is_symlink())

    def test_worker_root_override_and_symlink_rejection(self):
        self.env.update(BSS_SLOT="qa1", BSS_WORKER_ROOT="relative")
        self.rejected("must be absolute")
        alias = self.base / "alias"
        alias.symlink_to(self.base, target_is_directory=True)
        self.env["BSS_WORKER_ROOT"] = str(alias)
        self.rejected("symlinked")
        self.env["BSS_WORKER_ROOT"] = str(self.primary / "workers")
        self.rejected("overlaps")
        self.env["BSS_WORKER_ROOT"] = str(self.base / "custom pool")
        self.assertEqual(Path(self.result()["worktree"]), self.base / "custom pool/qa1/repo")

    def test_slot_rollback_and_persistent_state(self):
        self.env["BSS_SLOT"] = "worker1"
        slot = self.base / "primary checkout-agents/worker1"
        (slot / "state").mkdir(parents=True)
        (slot / "state/keep").write_text(MARKER)

        def fail_report(_result):
            raise OSError("injected failure")

        with (
            patch.dict(os.environ, self.env, clear=True),
            contextlib.chdir(self.primary),
            contextlib.redirect_stderr(io.StringIO()),
            self.assertRaises(OSError),
        ):
            task.start(fail_report)
        self.assertTrue(slot.is_dir())
        self.assertFalse((slot / "repo").exists())
        self.assertFalse((slot / "active.json").exists())
        self.assertEqual((slot / "state/keep").read_text(), MARKER)
        self.assertEqual(task.status(self.remote_item()), "Ready")
        self.assertEqual(self.result()["slot"], "worker1")

    def test_changed_slot_state_is_preserved(self):
        self.env["BSS_SLOT"] = "worker1"
        slot = self.base / "primary checkout-agents/worker1"

        def fail_report(_result):
            (slot / "active.json").write_text("changed")
            raise OSError("injected failure")

        with (
            patch.dict(os.environ, self.env, clear=True),
            contextlib.chdir(self.primary),
            contextlib.redirect_stderr(io.StringIO()) as errors,
            self.assertRaises(OSError),
        ):
            task.start(fail_report)
        self.assertEqual((slot / "active.json").read_text(), "changed")
        self.assertIn("preserved slot reservation", errors.getvalue())

    def test_two_slots_retain_task_port_and_owner_isolation(self):
        self.env["BSS_SLOT"] = "worker1"
        first = self.result()
        self.env.update(BSS_SLOT="worker2", BSS_TASK_OWNER="claude")
        self.board([issue(82)])
        second = self.result()
        for key in ("branch", "worktree", "secret_file", "app_port", "mcp_port", "web_port"):
            self.assertNotEqual(first[key], second[key], key)
        self.assertEqual(second["agent"], "agent:claude")
        self.assertEqual(second["slot"], "worker2")

    def test_release_slot_requires_absent_unregistered_repo_then_allows_reuse(self):
        self.env["BSS_SLOT"] = "worker1"
        first = self.result()
        slot = Path(first["worktree"]).parent
        rejected = self.call(["make", "release-slot"], check=False)
        self.assertNotEqual(rejected.returncode, 0)
        # Test fixture models an operator's safe manual worktree removal.
        (slot / "repo/.task/runtime.json").unlink()
        self.git("worktree", "remove", str(slot / "repo"))
        self.rejected("occupied")
        released = self.call(["make", "release-slot"], check=False)
        self.assertEqual(released.returncode, 0, released.stderr)
        self.assertTrue(slot.is_dir())
        self.board([issue(82)])
        second = self.result()
        self.assertEqual(first["worktree"], second["worktree"])
        self.assertNotEqual(first["branch"], second["branch"])


class TelemetryCatchUpTests(unittest.TestCase):
    def test_a_failed_catch_up_warns_and_start_task_still_reports_the_task(self):
        import task_collect
        import task_telemetry

        result = {
            "branch": "issue-81-subscriber-search",
            "slot": "worker1",
            "agent": "agent:claude",
        }
        with (
            patch.object(task_telemetry, "sql", return_value="") as event,
            patch.object(task_collect, "catch_up", side_effect=RuntimeError(MARKER)),
            patch.object(task_collect, "collect", return_value=[]) as collect,
            contextlib.redirect_stdout(io.StringIO()) as out,
            contextlib.redirect_stderr(io.StringIO()) as error,
        ):
            task.print_result(result, json_output=True)
        self.assertEqual(json.loads(out.getvalue()), result)
        event.assert_called_once()
        collect.assert_called_once()
        self.assertIn("catch-up of finished tasks failed", error.getvalue())
        self.assertNotIn(MARKER, error.getvalue())


if __name__ == "__main__":
    unittest.main(verbosity=2)
