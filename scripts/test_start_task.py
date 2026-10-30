#!/usr/bin/env python3
"""Host-only startup cases with disposable repositories and fake GitHub writes."""

import contextlib
import fcntl
import io
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import start_task as task

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
        "labels": connection([{"name": priority}]),
        "blockedBy": connection([]),
        "linkedBranches": connection([]),
        "closedByPullRequestsReferences": connection([]),
    }
    data.update(overrides)
    return {"id": f"item-{number}", "fieldValueByName": {"name": "Ready"}, "content": data}


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
        self.env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        self.env.update(
            GIT_CONFIG_GLOBAL="/dev/null",
            GIT_CONFIG_NOSYSTEM="1",
            PYTHONDONTWRITEBYTECODE="1",
            XDG_CONFIG_HOME=str(self.base / "config"),
            BSS_TEST_BOARD=str(self.base / "board.json"),
            BSS_TEST_PRS=str(self.base / "prs.json"),
            BSS_TEST_CALLS=str(self.base / "calls.jsonl"),
            BSS_TASK_AGENT="codex",
        )
        self.call(["git", "init", "--bare", "-q", "-b", "master", str(self.remote)], cwd=self.base)
        self.call(["git", "init", "-q", "-b", "master", str(self.primary)], cwd=self.base)
        self.git("config", "user.name", "Startup test")
        self.git("config", "user.email", "startup@example.test")
        (self.primary / "scripts").mkdir()
        for name in ("start_task.py", "task_runtime.py", "task_telemetry.py"):
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
            "if args[:2] == ['issue', 'edit']:\n"
            " if os.environ.get('BSS_TEST_FAIL') == 'claim' and '--add-label' in args: sys.exit(1)\n"
            " row = next(r for r in rows if str(r['content']['number']) == args[2])\n"
            " labels = row['content']['labels']['nodes']\n"
            " if '--add-label' in args: labels.append({'name': args[-1]})\n"
            " else: labels[:] = [n for n in labels if n['name'] != args[-1]]\n"
            " row['content']['labels']['totalCount'] = len(labels)\n"
            " with open(board_path, 'w') as output: json.dump(board, output)\n"
            " sys.exit(0)\n"
            "document = next(a for a in args if a.startswith('query='))\n"
            "if 'mutation' in document:\n"
            " if os.environ.get('BSS_TEST_FAIL') == 'status': sys.exit(1)\n"
            " item = next(a[5:] for a in args if a.startswith('item='))\n"
            " option = next(a[7:] for a in args if a.startswith('option='))\n"
            " row = next(r for r in rows if r['id'] == item)\n"
            " row['fieldValueByName']['name'] = 'Doing' if option == '47fc9ee4' else 'Ready'\n"
            " with open(board_path, 'w') as output: json.dump(board, output)\n"
            " print(json.dumps({'data': {'updated': True}}))\n"
            " sys.exit(0)\n"
            "cursor = next((a for a in args if a.startswith('cursor=')), None)\n"
            "index = int(cursor.rsplit('-', 1)[1]) if cursor else 0\n"
            "key = 'BSS_TEST_BOARD' if 'items(first:' in document else 'BSS_TEST_PRS'\n"
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
        branch = task.branch_name(number, title)
        return self.primary.parent / f"{self.primary.name}-worktrees" / branch

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

    def test_closed_blocked_owned_non_ready_and_open_relationships_are_excluded(self):
        cases = [
            issue(1, "P0", state="CLOSED"),
            issue(2, "P0", labels=connection([{"name": "P0"}, {"name": "blocked"}])),
            issue(3, "P0", labels=connection([{"name": "P0"}, {"name": "needs-human"}])),
            issue(4, "P0", labels=connection([{"name": "P0"}, {"name": "agent:codex"}])),
            issue(5, "P0", labels=connection([{"name": "P0"}, {"name": "agent:claude"}])),
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

    def test_ambiguous_priorities_and_duplicate_order(self):
        for rows in [
            [issue(labels=connection([]))],
            [issue(labels=connection([{"name": "P0"}, {"name": "P1"}]))],
            [issue(), issue()],
        ]:
            with self.subTest(rows=rows):
                self.board(rows)
                self.rejected("ambiguous")

    def test_ambiguous_board_view(self):
        self.board([issue()], views={"totalCount": 2, "nodes": []})
        self.rejected("ambiguous board order")

    def test_paginate_board_and_prs_before_selection(self):
        self.board([issue(81, "P0"), issue(82, "P0"), issue(83, "P1")], split=1)
        self.prs(["unrelated", "cloud/issue-81-already-active"], split=1)
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

        def concurrent_branch(items, represented):
            selected = choose(items, represented)
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
        self.git("branch", "cloud/issue-81-previous-name")
        self.git("push", "-q", "origin", "cloud/issue-81-previous-name")
        self.git("branch", "-d", "cloud/issue-81-previous-name")
        self.rejected("no eligible Ready issue")

    def test_open_pr_task_branch_excludes_issue(self):
        self.prs(["issue-81-without-a-closing-reference"])
        self.rejected("no eligible Ready issue")

    def test_existing_path_collision_preserves_contents(self):
        destination = self.destination()
        destination.mkdir(parents=True)
        (destination / "keep").write_text("untouched")
        self.rejected("worktree path collision")
        self.assertEqual((destination / "keep").read_text(), "untouched")

    def test_registered_worktree_collision_is_preserved(self):
        destination = self.destination()
        self.add_worktree(destination=destination)
        self.rejected("worktree path collision")
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
        return json.loads(Path(self.env["BSS_TEST_BOARD"]).read_text())[0]["node"]["items"]["nodes"][0]

    def test_claim_doing_and_disposable_credential(self):
        result = self.result()
        item = self.remote_item()
        self.assertIn("agent:codex", task.ownership(item))
        self.assertEqual(task.status(item), "Doing")
        secret = Path(result["secret_file"])
        self.assertEqual(secret.stat().st_mode & 0o777, 0o600)
        self.assertEqual(secret.parent.stat().st_mode & 0o777, 0o700)
        value = secret.read_text().split("=", 1)[1].strip()
        self.assertEqual(len(value), 64)
        self.assertNotIn(value, json.dumps(result))

    def test_failed_claim_and_status_leave_no_local_task(self):
        for failure in ("claim", "status"):
            with self.subTest(failure=failure):
                self.env["BSS_TEST_FAIL"] = failure
                self.rejected("command failed")
                self.assertFalse(self.destination().exists())
                self.assertEqual(task.status(self.remote_item()), "Ready")
                self.assertNotIn("agent:codex", task.ownership(self.remote_item()))

    def test_wip_limit(self):
        active = issue(90, labels=connection([{"name": "agent:codex"}]))
        active["fieldValueByName"]["name"] = "Doing"
        self.board([issue(), active])
        self.rejected("WIP limit")

    def test_claim_race_after_selection(self):
        choose = task.select_issue

        def race(items, represented):
            result = choose(items, represented)
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
            self.rejected("another start-task invocation")


if __name__ == "__main__":
    unittest.main(verbosity=2)
