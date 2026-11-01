"""Task PR integration with real disposable Git and fake GitHub; no real mutations."""

import contextlib
import io
import json
import os
import sys
import unittest
from unittest.mock import patch

import task_pr
import task_telemetry
from test_task_git import GitTests


class PrTests(unittest.TestCase):
    call = GitTests.call
    write_metadata = GitTests.write_metadata
    runtime_setup = GitTests.runtime_setup
    quality_setup = GitTests.quality_setup
    git_setup = GitTests.setUp

    def setUp(self):
        self.git_setup()
        self.head = self.call(["git", "rev-parse", "HEAD"]).stdout.strip()
        self.pr = {
            "number": 123,
            "url": "https://github.com/altharmz/bss/pull/123",
            "state": "OPEN",
            "body": "A focused change.\n\nFixes #77\n",
            "baseRefName": "master",
            "headRefName": self.task,
            "headRefOid": self.head,
            "isCrossRepository": False,
            "isDraft": False,
            "mergeable": "MERGEABLE",
            "mergeStateStatus": "CLEAN",
            "reviewDecision": "",
            "labels": [],
            "statusCheckRollup": [
                {"__typename": "CheckRun", "status": "COMPLETED", "conclusion": "SUCCESS"}
            ],
            "changedFiles": 1,
        }
        self.fixture = self.base / "github.json"
        self.state = {
            "pr": self.pr,
            "issue": {"number": 77, "state": "OPEN", "labels": []},
            "files": [[{"filename": "scripts/task_pr.py"}]],
            "behind": 0,
        }
        self.env["BSS_TEST_GITHUB"] = str(self.fixture)
        fake = self.bin / "gh"
        fake.write_text(
            "#!/usr/bin/env python3\n"
            "import json,os,sys\n"
            "from pathlib import Path\n"
            "p=Path(os.environ['BSS_TEST_GITHUB']); d=json.loads(p.read_text()); a=sys.argv[1:]\n"
            "assert '--repo' in a or a[0]=='api'\n"
            "if a[:2]==['issue','view']: out=d['issue']\n"
            "elif a[:2]==['pr','list']: out=[{'number':123}] if d['pr'] else []\n"
            "elif a[:2]==['pr','view']: out=d['pr']\n"
            "elif a[:2]==['pr','create']:\n"
            " if d.get('fail_create'): print('private API error',file=sys.stderr); sys.exit(1)\n"
            " assert a[a.index('--base')+1]=='master'\n"
            " body=Path(a[a.index('--body-file')+1]).read_text()\n"
            " d['pr']=d['new_pr']; d['pr']['body']=body\n"
            " d['creates']=d.get('creates',0)+1; p.write_text(json.dumps(d)); sys.exit(0)\n"
            "elif '/files?' in a[-1]:\n"
            " print('\\n'.join(json.dumps(page) for page in d['files'])); sys.exit(0)\n"
            "elif '/compare/' in a[-1]: out={'behind_by':d['behind']}\n"
            "else: sys.exit(2)\n"
            "print(json.dumps(out))\n"
        )
        fake.chmod(0o755)

    def command(self, operation="check-pr", real_repository=False):
        self.fixture.write_text(json.dumps(self.state))
        before = os.getcwd()
        output, errors = io.StringIO(), io.StringIO()
        try:
            os.chdir(self.work)
            with (
                patch.dict(os.environ, self.env, clear=True),
                patch.object(sys, "argv", ["task_pr.py", operation]),
                contextlib.redirect_stdout(output),
                contextlib.redirect_stderr(errors),
                contextlib.ExitStack() as stack,
            ):
                # Production refuses local remotes. Only this fixture mapping is replaced;
                # publication itself uses real Git and normal hooks against our bare remote.
                if not real_repository:
                    stack.enter_context(patch.object(task_pr, "repository"))
                code = task_pr.main()
        finally:
            os.chdir(before)
        if operation == "create-pr":
            self.state = json.loads(self.fixture.read_text())
            if self.state["pr"] is not None:
                self.pr = self.state["pr"]
        return code, output.getvalue(), errors.getvalue()

    def outcome(self, expected, code):
        result, output, error = self.command()
        self.assertEqual(result, code, error)
        data = json.loads(output.splitlines()[-1])
        self.assertEqual(data["status"], expected)
        return data

    def test_green_and_telemetry_unavailable(self):
        docker = self.bin / "docker"
        docker.write_text("#!/bin/sh\necho private-telemetry-error >&2\nexit 1\n")
        data = self.outcome("ready", 0)
        self.assertEqual(data["ci"], "passed")
        code, _, error = self.command()
        self.assertEqual(code, 0)
        self.assertIn("event not recorded", error)
        self.assertNotIn("private-telemetry-error", error)

    def test_ci_states_and_missing(self):
        for status, conclusion, expected, code in (
            ("IN_PROGRESS", "", "pending", 2),
            ("COMPLETED", "FAILURE", "failed", 1),
            ("COMPLETED", "CANCELLED", "failed", 1),
        ):
            self.pr["statusCheckRollup"][0].update(status=status, conclusion=conclusion)
            self.outcome(expected, code)
        self.pr["statusCheckRollup"] = []
        self.outcome("pending", 2)
        self.state["pr"] = None
        self.outcome("missing", 1)

    def test_legacy_commit_status(self):
        self.pr["statusCheckRollup"] = [{"__typename": "StatusContext", "state": "ERROR"}]
        self.outcome("failed", 1)
        self.pr["statusCheckRollup"][0]["state"] = "SUCCESS"
        self.outcome("ready", 0)

    def test_mergeability_stale_draft_and_unknown(self):
        for field, value, expected, code in (
            ("mergeable", "CONFLICTING", "failed", 1),
            ("mergeable", "UNKNOWN", "pending", 2),
            ("mergeStateStatus", "BLOCKED", "blocked", 3),
            ("mergeStateStatus", "BEHIND", "pending", 2),
            ("isDraft", True, "pending", 2),
            ("headRefOid", "a" * 40, "pending", 2),
        ):
            old = self.pr[field]
            self.pr[field] = value
            self.outcome(expected, code)
            self.pr[field] = old
        self.state["behind"] = 1
        self.assertIn("base-behind", self.outcome("pending", 2)["reasons"])

    def test_review_and_human_policy(self):
        for decision in ("REVIEW_REQUIRED", "CHANGES_REQUESTED"):
            self.pr["reviewDecision"] = decision
            self.outcome("blocked", 3)
        self.pr["reviewDecision"] = "APPROVED"
        for filename in (
            "CLAUDE.md",
            "AGENTS.md",
            "docs/board.md",
            ".claude/hooks/guard.sh",
            ".github/workflows/ci.yml",
            "scripts/pre-commit.sh",
        ):
            self.state["files"] = [[{"filename": filename}]]
            self.assertTrue(self.outcome("blocked", 3)["human_approval_required"])
        self.state["files"] = [[{"filename": "safe.txt"}]]
        for owner in (self.pr, self.state["issue"]):
            owner["labels"] = [{"name": "needs-human"}]
            self.outcome("blocked", 3)
            owner["labels"] = []

    def test_all_file_pages_and_incomplete_files(self):
        self.pr["changedFiles"] = 2
        self.state["files"].append([{"filename": "CLAUDE.md"}])
        self.outcome("blocked", 3)
        self.pr["changedFiles"] = 3
        self.assertEqual(self.command()[0], 1)

    def test_workflow_guard_helper_and_cases_require_human(self):
        self.pr["reviewDecision"] = "APPROVED"
        for filename in ("scripts/workflow_guard.py", "scripts/test_workflow_guard.py"):
            with self.subTest(filename=filename):
                self.state["files"] = [[{"filename": filename}]]
                self.assertTrue(self.outcome("blocked", 3)["human_approval_required"])

    def test_rename_from_protected_path_requires_human(self):
        self.state["files"] = [
            [{"filename": "archived-instructions.md", "previous_filename": "CLAUDE.md"}]
        ]
        self.assertTrue(self.outcome("blocked", 3)["human_approval_required"])

    def test_unknown_host_closed_issue_and_incomplete_response(self):
        self.env["GH_HOST"] = "other.example"
        self.assertEqual(self.command()[0], 1)
        del self.env["GH_HOST"]
        self.state["issue"]["state"] = "CLOSED"
        self.assertEqual(self.command()[0], 1)
        self.state["issue"]["state"] = "OPEN"
        del self.pr["reviewDecision"]
        self.assertEqual(self.command()[0], 1)

    def test_conflicting_pr_refused_before_publication(self):
        for field, value in (
            ("baseRefName", "other"),
            ("headRefName", "other"),
            ("body", "Fixes #7"),
            ("state", "CLOSED"),
            ("isCrossRepository", True),
        ):
            old = self.pr[field]
            self.pr[field] = value
            self.assertEqual(self.command("create-pr")[0], 1)
            self.pr[field] = old
        refs = self.call(["git", "ls-remote", "--heads", "origin", self.task]).stdout
        self.assertEqual(refs, "")

    def test_unpublished_branch_created_then_idempotent(self):
        self.state["new_pr"] = self.pr.copy()
        self.state["pr"] = None
        code, output, error = self.command("create-pr")
        self.assertEqual(code, 0, error)
        self.assertEqual(json.loads(output.splitlines()[-1])["status"], "created")
        self.assertIn(self.task, self.call(["git", "ls-remote", "--heads", "origin"]).stdout)
        code, output, error = self.command("create-pr")
        self.assertEqual(code, 0, error)
        self.assertEqual(json.loads(output.splitlines()[-1])["status"], "existing")
        self.assertEqual(self.state["creates"], 1)

    def test_mutation_failure_is_inspectable_and_private(self):
        self.state.update(pr=None, new_pr=self.pr, fail_create=True)
        code, _, error = self.command("create-pr")
        self.assertEqual(code, 1)
        self.assertNotIn("private API error", error)
        self.assertIn(self.task, self.call(["git", "ls-remote", "--heads", "origin"]).stdout)

    def test_unsafe_context_dirty_and_wrong_origin(self):
        self.assertEqual(self.command(real_repository=True)[0], 1)
        (self.work / "uncommitted").touch()
        self.assertEqual(self.command("create-pr")[0], 1)
        self.outcome("pending", 2)
        self.metadata["task_id"] = "issue-9-wrong"
        self.write_metadata()
        self.assertEqual(self.command()[0], 1)

    def test_pr_telemetry_is_bounded(self):
        statement = task_telemetry.event_sql(
            self.task, "check-pr", 3, 42, {}, pr_number=123, pr_status="blocked", ci_state="passed"
        )
        self.assertIn('"pr_number": 123', statement)
        for kwargs in (
            {"pr_number": True},
            {"pr_status": "private review"},
            {"ci_state": "arbitrary API payload"},
        ):
            with self.assertRaises(ValueError):
                task_telemetry.event_sql(self.task, "check-pr", 1, 1, {}, **kwargs)


del GitTests

if __name__ == "__main__":
    unittest.main()
