"""Lifecycle regressions with disposable Git repositories and fake GitHub/Docker."""

import contextlib
import io
import json
import os
import sqlite3
import subprocess
import sys
import unittest
from unittest.mock import patch

import task_finish as lifecycle
import task_pr
import task_telemetry
import test_task_pr as fixtures
from task_runtime import REEXEC_MARKER, InvalidRuntime


class LifecycleFixture(unittest.TestCase):
    call = fixtures.PrTests.call
    write_metadata = fixtures.PrTests.write_metadata
    runtime_setup = fixtures.PrTests.runtime_setup
    quality_setup = fixtures.PrTests.quality_setup
    git_setup = fixtures.PrTests.git_setup

    def setUp(self):
        fixtures.PrTests.setUp(self)
        ignore = self.work / ".gitignore"
        ignore.write_text(
            ignore.read_text()
            + "__pycache__/\nfrontend/.shots/\nfrontend/src/api/generated/\nloadtest/out/\n"
            + ".testmon/\n"
        )
        self.call(["git", "add", ".gitignore"])
        self.call(["git", "commit", "-m", "ignore generated cache"])
        self.call(["git", "push", "origin", "HEAD:refs/heads/master"])
        self.head = self.call(["git", "rev-parse", "HEAD"]).stdout.strip()
        self.pr["headRefOid"] = self.head
        fake = self.bin / "gh"
        fake.write_text(
            fake.read_text().replace(
                "if a[:2]==['issue','view']:",
                "if a[:2]==['pr','merge']:\n"
                " assert '--admin' not in a and '--auto' not in a and '--delete-branch' not in a\n"
                " assert a[a.index('--match-head-commit')+1]==d['pr']['headRefOid']\n"
                " if d.get('fail_merge'):\n"
                "  print('private merge error',file=sys.stderr); sys.exit(1)\n"
                " d['merges']=d.get('merges',0)+1; d['pr']['state']='MERGED'\n"
                " p.write_text(json.dumps(d)); sys.exit(0)\n"
                "elif a==['api','repos/altharmz/bss']: out=d['policy']\n"
                "elif a[:2]==['api','graphql']:\n"
                " doc=[x for x in a if x.startswith('query=')][0]\n"
                " status=d['issue']['projectItems'][0]['status']\n"
                " if 'mutation' in doc:\n"
                "  if d.get('fail_board'): sys.exit(1)\n"
                "  status['name']='Done'; d['board_sets']=d.get('board_sets',0)+1\n"
                "  p.write_text(json.dumps(d)); out={'data':{'updated':True}}\n"
                " else:\n"
                "  views={'totalCount':1,'nodes':[{'layout':'BOARD_LAYOUT','filter':None,\n"
                "   'sortByFields':{'totalCount':0}}]}\n"
                "  items={'totalCount':1,'pageInfo':{'hasNextPage':False,'endCursor':None},\n"
                "   'nodes':[{'id':'item-77','fieldValueByName':{'name':status['name']},\n"
                "   'content':{'number':77}}]}\n"
                "  out={'data':{'node':{'views':views,'items':items}}}\n"
                "elif a[:2]==['issue','view']:",
            )
        )
        self.state["policy"] = {
            "allow_squash_merge": True,
            "allow_merge_commit": False,
            "allow_rebase_merge": False,
            "archived": False,
        }

    def merge(self):
        self.fixture.write_text(json.dumps(self.state))
        with patch.dict(os.environ, self.env, clear=True):
            result = lifecycle.merge(self.work, self.task)
        self.state = json.loads(self.fixture.read_text())
        self.pr = self.state["pr"]
        return result


class MergeTests(LifecycleFixture):
    def approve(self):
        self.env["HUMAN_APPROVAL"] = f"123:{self.head}"

    def test_explicit_approval_for_protected_files_and_both_labels(self):
        for source in ("files", "issue", "pr"):
            with self.subTest(source=source):
                if source == "files":
                    self.state["files"] = [[{"filename": "CLAUDE.md"}]]
                else:
                    self.state[source]["labels"] = [{"name": "needs-human"}]
                self.approve()
                outcome, code = self.merge()
                self.assertEqual(code, 0)
                self.assertTrue(outcome["human_approval_required"])
                self.assertEqual(outcome["human_approval"], "accepted")
                self.assertEqual(self.state["merges"], 1)
                self.pr["state"] = "OPEN"
                self.state.pop("merges")
                self.state["files"] = [[{"filename": "scripts/task_pr.py"}]]
                self.state["issue"]["labels"] = []
                self.pr["labels"] = []

    def test_invalid_or_stale_approval_never_merges(self):
        self.state["files"] = [[{"filename": "CLAUDE.md"}]]
        for approval, status in (
            ("", "missing"),
            ("yes", "malformed"),
            (f"0123:{self.head}", "malformed"),
            (f"124:{self.head}", "mismatched"),
            ("123:" + "a" * 40, "mismatched"),
        ):
            with self.subTest(approval=approval):
                self.env["HUMAN_APPROVAL"] = approval
                outcome, code = self.merge()
                self.assertEqual(code, 3)
                self.assertEqual(outcome["human_approval"], status)
                self.assertNotIn("merges", self.state)
        self.approve()
        self.pr["headRefOid"] = "b" * 40
        self.assertEqual(self.merge()[0]["human_approval"], "mismatched")
        self.assertNotIn("merges", self.state)

    def test_approval_preserves_all_other_readiness_gates(self):
        self.pr["labels"] = [{"name": "needs-human"}]
        self.approve()
        for field, value in (
            ("mergeable", "CONFLICTING"),
            ("mergeable", "UNKNOWN"),
            ("reviewDecision", "REVIEW_REQUIRED"),
            ("reviewDecision", "CHANGES_REQUESTED"),
            ("reviewDecision", "UNKNOWN"),
            ("mergeStateStatus", "BLOCKED"),
            ("mergeStateStatus", "BEHIND"),
            ("isDraft", True),
            ("statusCheckRollup", []),
            ("statusCheckRollup", [{"__typename": "StatusContext", "state": "PENDING"}]),
            ("statusCheckRollup", [{"__typename": "StatusContext", "state": "FAILURE"}]),
        ):
            with self.subTest(field=field, value=value):
                old = self.pr[field]
                self.pr[field] = value
                outcome, code = self.merge()
                self.assertNotEqual(code, 0)
                self.assertEqual(outcome["human_approval"], "accepted")
                self.assertNotIn("merges", self.state)
                self.pr[field] = old
        self.state["behind"] = 1
        self.assertIn("base-behind", self.merge()[0]["reasons"])
        self.state["behind"] = 0
        (self.work / "Makefile").write_text("dirty\n")
        self.assertIn("uncommitted-changes", self.merge()[0]["reasons"])
        self.assertNotIn("merges", self.state)

    def test_approved_remote_head_still_requires_matching_local_head(self):
        self.pr["labels"] = [{"name": "needs-human"}]
        self.pr["headRefOid"] = "b" * 40
        self.env["HUMAN_APPROVAL"] = "123:" + "b" * 40
        self.assertIn("head-mismatch", self.merge()[0]["reasons"])
        self.assertNotIn("merges", self.state)

    def test_approval_does_not_override_repository_policy_or_github_rejection(self):
        self.pr["labels"] = [{"name": "needs-human"}]
        self.approve()
        self.state["policy"]["allow_merge_commit"] = True
        self.assertEqual(self.merge()[1], 3)
        self.assertNotIn("merges", self.state)
        self.state["policy"]["allow_merge_commit"] = False
        self.state["fail_merge"] = True
        with self.assertRaises(subprocess.CalledProcessError):
            self.merge()
        self.assertNotIn("merges", json.loads(self.fixture.read_text()))

    def test_entrypoint_blocks_human_gate_and_records_result(self):
        self.pr["labels"] = [{"name": "needs-human"}]
        self.fixture.write_text(json.dumps(self.state))
        before = os.getcwd()
        try:
            os.chdir(self.work)
            with (
                patch.dict(os.environ, self.env, clear=True),
                patch.object(sys, "argv", ["task_finish.py", "merge-task"]),
                patch.object(task_pr, "repository"),
                patch.object(lifecycle, "record") as events,
                contextlib.redirect_stdout(io.StringIO()) as output,
            ):
                self.assertEqual(lifecycle.main(), 3)
                data = json.loads(output.getvalue().splitlines()[-1])
                self.assertEqual(data["status"], "blocked")
                self.assertEqual(events.call_args.kwargs["pr_status"], "blocked")
        finally:
            os.chdir(before)
        self.assertNotIn("merges", json.loads(self.fixture.read_text()))

    def test_merge_then_idempotent(self):
        first = self.merge()[0]
        self.assertEqual(first["status"], "merged")
        self.assertTrue(first["next_command"].startswith("make finish-task SLOT="))
        self.assertTrue(self.merge()[0]["next_command"].startswith("make finish-task SLOT="))
        self.assertEqual(self.merge()[1], 0)
        self.assertEqual(self.state["merges"], 1)

    def test_merge_pins_head_validated_by_readiness(self):
        self.pr["labels"] = [{"name": "needs-human"}]
        self.approve()
        initial = {**self.pr, "headRefOid": "a" * 40}
        confirmed = {**self.pr, "state": "MERGED"}
        with patch.object(lifecycle, "merged_pr", side_effect=[initial, confirmed]):
            self.assertEqual(self.merge()[1], 0)
        self.assertEqual(self.state["merges"], 1)

    def test_failed_ci_conflict_review_human_and_repository_policy(self):
        for field, value in (
            ("mergeable", "CONFLICTING"),
            ("reviewDecision", "REVIEW_REQUIRED"),
            ("labels", [{"name": "needs-human"}]),
            ("statusCheckRollup", [{"__typename": "StatusContext", "state": "FAILURE"}]),
        ):
            old = self.pr[field]
            self.pr[field] = value
            self.assertNotEqual(self.merge()[1], 0)
            self.assertNotIn("merges", self.state)
            self.pr[field] = old
        self.state["policy"]["allow_merge_commit"] = True
        self.assertEqual(self.merge()[0]["status"], "blocked")
        self.assertNotIn("merges", self.state)

    def test_protected_files_block_even_with_github_approval(self):
        self.pr["reviewDecision"] = "APPROVED"
        self.state["files"] = [[{"filename": "CLAUDE.md"}]]
        self.assertEqual(self.merge()[1], 3)
        self.assertNotIn("merges", self.state)

    def test_merge_failure_preserves_inspectable_state(self):
        self.state["fail_merge"] = True
        with self.assertRaises(subprocess.CalledProcessError):
            self.merge()
        self.assertTrue(self.work.exists())
        self.assertEqual(json.loads(self.fixture.read_text())["pr"]["state"], "OPEN")


class FinishTests(LifecycleFixture):
    # Reuse fixtures, without inheriting the merge tests into the finish suite.
    def setUp(self):
        super().setUp()
        self.slot = self.base / "primary-agents/worker1"
        self.slot.mkdir(parents=True)
        previous = self.work
        self.work = self.slot / "repo"
        self.call(["git", "worktree", "move", str(previous), str(self.work)], cwd=self.primary)
        (self.slot / "slot.lock").touch()
        (self.slot / "state").mkdir()
        (self.slot / "state/keep").write_text("persistent state")
        self.active = self.slot / "active.json"
        self.active.write_text(json.dumps({"version": 1, "task_id": self.task}))
        self.state["pr"].update(state="MERGED", mergeCommit={"oid": self.head})
        self.state["issue"].update(
            state="CLOSED", projectItems=[{"title": "BSS", "status": {"name": "Done"}}]
        )
        self.docker_state = self.base / "docker.json"
        self.env["BSS_TEST_DOCKER_STATE"] = str(self.docker_state)
        self.env["BSS_SLOT"] = "worker1"
        # The fixture's primary is far behind master (scripts changed), which would hand
        # over to the updated script; only the dedicated tests below exercise that.
        self.env[REEXEC_MARKER] = "1"
        self.env["BSS_BOARD_DONE_WAIT"] = "0"
        self.env["BSS_GH_RETRY_PAUSE"] = "0"
        project = "bss-" + self.task
        self.inventory = {
            "running": True,
            "mismatch": False,
            "fail_down": False,
            "volumes": {
                f"{project}_db": {"com.docker.compose.project": project},
                f"{project}_web_modules": {"com.docker.compose.project": project},
                "bss-telemetry_data": {"com.docker.compose.project": "bss-telemetry"},
            },
            "images": [f"{project}-app:dev", f"{project}-web:dev", "postgres:16"],
        }
        docker = self.bin / "docker"
        docker.write_text(
            "#!/usr/bin/env python3\n"
            "import json,os,sys\nfrom pathlib import Path\n"
            "p=Path(os.environ['BSS_TEST_DOCKER_STATE'])\n"
            "d=json.loads(p.read_text()); a=sys.argv[3:]\n"
            "if a[0]=='compose':\n"
            " if a[-1]=='down':\n"
            "  if d['fail_down']: sys.exit(1)\n"
            "  d['running']=False; p.write_text(json.dumps(d))\n"
            " if 'bss-telemetry' in a: sys.exit(1)\n"
            " sys.exit(0)\n"
            """
def save(): p.write_text(json.dumps(d))
def label(name): return a[-1].split('=', 1)[1] if a[-1].startswith(name) else None
if a[:2] == ['volume', 'ls']:
    want = a[-1].split('=', 2)[2]
    names = [n for n, v in d.get('volumes', {}).items()
             if v.get('com.docker.compose.project') == want]
    print('\\n'.join(names + d.get('mislabelled', []))); sys.exit(0)
if a[:2] == ['volume', 'inspect']:
    print(json.dumps(d['volumes'][a[-1]])); sys.exit(0)
if a[:2] == ['volume', 'rm']:
    if d.get('fail_volume_rm') == a[2]: sys.exit(1)
    del d['volumes'][a[2]]; save(); sys.exit(0)
if a[:2] == ['image', 'ls']:
    print(a[-1] if a[-1] in d.get('images', []) else ''); sys.exit(0)
if a[:2] == ['image', 'rm']:
    d['images'].remove(a[2]); save(); sys.exit(0)
if a[0] == 'ps' and any(x.startswith('volume=') for x in a):
    print('user' if label('volume=') in d.get('volume_users', []) else ''); sys.exit(0)
if a[0] == 'ps' and any(x.startswith('ancestor=') for x in a):
    print('user' if label('ancestor=') in d.get('image_users', []) else ''); sys.exit(0)
"""
            "if a[0]=='ps': print('abc' if d['running'] else ''); sys.exit(0)\n"
            "if a[:2]==['network','ls']: sys.exit(0)\n"
            "if a[0]=='inspect':\n"
            " root=d['root']; task=d['task']\n"
            " print(json.dumps({'com.docker.compose.project':'bss-'+task,"
            "'com.docker.compose.service':'unknown' if d['mismatch'] else 'app',"
            "'com.docker.compose.project.working_dir':root,"
            "'com.docker.compose.project.config_files':root+'/compose.yaml,'+root+'/compose.override.yaml'}))\n"
            "else: sys.exit(2)\n"
        )
        self.inventory.update(root=str(self.work), task=self.task)
        self.docker_state.write_text(json.dumps(self.inventory))

    def finish(self):
        self.fixture.write_text(json.dumps(self.state))
        before = os.getcwd()
        try:
            os.chdir(self.primary)
            with (
                patch.dict(os.environ, self.env, clear=True),
                patch.object(task_pr, "repository"),
                contextlib.redirect_stdout(io.StringIO()),
                contextlib.redirect_stderr(io.StringIO()),
            ):
                return lifecycle.finish(self.primary, self.primary / ".git", self.slot)
        finally:
            os.chdir(before)

    def test_success_preserves_slot_state_and_volumes_removes_credentials(self):
        outcome = self.finish()
        self.assertEqual(outcome["status"], "finished")
        self.assertTrue(outcome["next_command"].startswith("make start-task BSS_TASK_OWNER="))
        self.assertFalse(self.active.exists())
        self.assertFalse(self.work.exists())
        self.assertFalse(self.secret.exists())
        self.assertTrue((self.slot / "state/keep").exists())
        self.assertTrue((self.slot / "slot.lock").exists())
        self.assertEqual(self.call(["git", "status", "--porcelain"], cwd=self.primary).stdout, "")
        self.assertEqual(
            self.call(["git", "rev-parse", "HEAD"], cwd=self.primary).stdout.strip(), self.head
        )

    def docker_now(self):
        return json.loads(self.docker_state.read_text())

    def set_docker(self, **changes):
        self.inventory.update(changes)
        self.docker_state.write_text(json.dumps(self.inventory))

    def test_finish_removes_the_tasks_volumes_images_and_branch_only(self):
        project = "bss-" + self.task
        outcome = self.finish()
        self.assertEqual(
            outcome["removed"],
            {
                "volumes": [f"{project}_db", f"{project}_web_modules"],
                "images": [f"{project}-app:dev", f"{project}-web:dev"],
                "branch": self.task,
            },
        )
        self.assertEqual(outcome["kept"], [])
        now = self.docker_now()
        # The telemetry volume and unrelated images are never touched.
        self.assertEqual(list(now["volumes"]), ["bss-telemetry_data"])
        self.assertEqual(now["images"], ["postgres:16"])
        refs = self.call(["git", "for-each-ref", f"refs/heads/{self.task}"], cwd=self.primary)
        self.assertEqual(refs.stdout, "")

    def test_mislabelled_in_use_and_unmerged_leftovers_are_kept_with_reasons(self):
        project = "bss-" + self.task
        self.set_docker(
            volumes={
                **self.inventory["volumes"],
                "stray_volume": {"com.docker.compose.project": "other-project"},
            },
            mislabelled=["stray_volume"],
            volume_users=[f"{project}_web_modules"],
            image_users=[f"{project}-web:dev"],
        )
        outcome = self.finish()
        kept = {row["item"]: row["reason"] for row in outcome["kept"]}
        self.assertEqual(
            kept,
            {
                "volume stray_volume": "project label mismatch",
                f"volume {project}_web_modules": "in use by a container",
                f"image {project}-web:dev": "in use by a container",
            },
        )
        self.assertEqual(outcome["removed"]["volumes"], [f"{project}_db"])
        self.assertEqual(outcome["removed"]["images"], [f"{project}-app:dev"])
        self.assertIn("stray_volume", self.docker_now()["volumes"])

    def test_branch_with_a_different_tip_is_kept(self):
        import task_cleanup

        with patch.dict(os.environ, self.env, clear=True):
            removed, kept = task_cleanup.remove_leftovers(
                self.primary, self.primary, self.task, "b" * 40, {}
            )
        self.assertNotIn("branch", removed)
        self.assertIn(
            {"item": f"branch {self.task}", "reason": "tip differs from the merged PR head"}, kept
        )
        refs = self.call(["git", "for-each-ref", f"refs/heads/{self.task}"], cwd=self.primary)
        self.assertIn(self.task, refs.stdout)

    def test_partial_removal_failure_resumes_from_the_checkpoint(self):
        project = "bss-" + self.task
        self.set_docker(fail_volume_rm=f"{project}_web_modules")
        with self.assertRaises(subprocess.CalledProcessError):
            self.finish()
        checkpoint = json.loads(next(self.slot.glob("finish-*.json")).read_text())
        self.assertEqual(checkpoint["removed"], {"volumes": [f"{project}_db"]})
        self.assertTrue(self.active.exists())
        self.set_docker(fail_volume_rm=None, volumes=self.docker_now()["volumes"])
        outcome = self.finish()
        self.assertEqual(outcome["removed"]["volumes"], [f"{project}_db", f"{project}_web_modules"])
        self.assertFalse(self.active.exists())
        self.assertEqual(list(self.docker_now()["volumes"]), ["bss-telemetry_data"])

    def advance_master(self, name):
        """Push a commit to origin/master from a separate clone, as another task's merge."""
        clone = self.base / "other-clone"
        self.call(["git", "clone", "-q", str(self.remote), str(clone)], cwd=self.base)
        (clone / name).parent.mkdir(parents=True, exist_ok=True)
        (clone / name).write_text("changed\n")
        self.call(["git", "add", name], cwd=clone)
        self.call(
            ["git", "-c", "user.name=t", "-c", "user.email=t@example.test", "commit", "-qm", name],
            cwd=clone,
        )
        self.call(["git", "push", "-q", "origin", "HEAD:refs/heads/master"], cwd=clone)

    def sync_primary(self):
        self.call(["git", "fetch", "-q", "origin"], cwd=self.primary)
        self.call(["git", "merge", "-q", "--ff-only", "origin/master"], cwd=self.primary)

    def test_changed_scripts_on_master_hand_over_once_then_resume(self):
        self.sync_primary()
        self.advance_master("scripts/task_finish.py")
        del self.env[REEXEC_MARKER]
        with self.assertRaises(lifecycle.Reexec):
            self.finish()
        # Nothing was removed or released; the checkpoint lets the rerun resume.
        self.assertTrue(self.work.exists())
        self.assertTrue(self.active.exists())
        self.assertTrue(list(self.slot.glob("finish-*.json")))
        # The marker (set by the hand-over) prevents a second hand-over.
        self.env[REEXEC_MARKER] = "1"
        self.assertEqual(self.finish()["status"], "finished")
        self.assertFalse(self.active.exists())

    def test_unrelated_changes_on_master_do_not_hand_over(self):
        self.sync_primary()
        self.advance_master("docs/note.md")
        del self.env[REEXEC_MARKER]
        self.assertEqual(self.finish()["status"], "finished")

    def test_entrypoint_runs_the_updated_script_after_the_locks_are_released(self):
        self.fixture.write_text(json.dumps(self.state))
        before = os.getcwd()
        try:
            os.chdir(self.primary)
            with (
                patch.dict(os.environ, self.env, clear=True),
                patch.object(sys, "argv", ["task_finish.py", "finish-task"]),
                patch.object(lifecycle, "finish", side_effect=lifecycle.Reexec(self.primary)),
                patch.object(lifecycle, "rerun", return_value=0) as rerun,
                contextlib.redirect_stdout(io.StringIO()),
                contextlib.redirect_stderr(io.StringIO()),
            ):
                self.assertEqual(lifecycle.main(), 0)
        finally:
            os.chdir(before)
        rerun.assert_called_once_with(self.primary, "task_finish.py", "finish-task")

    def test_primary_entrypoint_with_real_locks(self):
        self.fixture.write_text(json.dumps(self.state))
        before = os.getcwd()
        try:
            os.chdir(self.primary)
            with (
                patch.dict(os.environ, self.env, clear=True),
                patch.object(sys, "argv", ["task_finish.py", "finish-task"]),
                patch.object(task_pr, "repository"),
                contextlib.redirect_stdout(io.StringIO()),
                contextlib.redirect_stderr(io.StringIO()),
            ):
                self.assertEqual(lifecycle.main(), 0)
        finally:
            os.chdir(before)
        self.assertFalse(self.active.exists())
        self.assertTrue(self.slot.exists())

    def test_symlinked_credentials_preserved(self):
        other = self.base / "other-credential"
        other.write_text("preserve me")
        self.secret.unlink()
        self.secret.symlink_to(other)
        with self.assertRaises(lifecycle.start_task.StartupError):
            self.finish()
        self.assertEqual(other.read_text(), "preserve me")
        self.assertTrue(self.work.exists())

    def test_unknown_network_member_preserved(self):
        project = "bss-" + self.task
        with (
            patch.object(
                lifecycle,
                "docker",
                side_effect=[
                    "",
                    "network-id",
                    json.dumps(
                        {"com.docker.compose.project": project, "com.docker.compose.network": "lab"}
                    ),
                    json.dumps({"unknown": {}}),
                ],
            ),
            self.assertRaises(InvalidRuntime),
        ):
            lifecycle.runtime_inventory(self.work, self.task)

    def test_dirty_attempt_records_failure_before_any_cleanup(self):
        (self.work / "Makefile").write_text("changed\n")
        with patch.object(lifecycle, "record") as events:
            with self.assertRaises(InvalidRuntime):
                self.finish()
        self.assertEqual(events.call_args_list[0].args[1], "finish-task-attempt")
        self.assertEqual(events.call_args.args[1:3], ("finish-task", 1))

    def test_modified_worktree_preserved(self):
        (self.work / "Makefile").write_text("changed\n")
        with self.assertRaises(InvalidRuntime):
            self.finish()
        self.assertTrue(self.secret.exists())
        self.assertTrue(self.active.exists())
        self.assertTrue(json.loads(self.docker_state.read_text())["running"])

    def test_unknown_ignored_files_preserved(self):
        (self.work / ".task/keep").touch()
        with self.assertRaises(InvalidRuntime):
            self.finish()
        self.assertTrue(self.work.exists())

    def test_finish_saves_the_test_selection_record_for_the_next_worktree(self):
        record = self.work / ".testmon/.testmondata"
        record.parent.mkdir()
        connection = sqlite3.connect(record)
        connection.execute("CREATE TABLE node (name TEXT)")
        connection.commit()
        connection.close()
        self.assertEqual(self.finish()["status"], "finished")
        self.assertFalse(self.work.exists())
        saved = self.primary / ".git/bss-testmon/testmondata"
        connection = sqlite3.connect(saved)
        self.assertEqual(connection.execute("SELECT count(*) FROM node").fetchone(), (0,))
        connection.close()

    def test_a_damaged_test_selection_record_does_not_stop_the_finish(self):
        record = self.work / ".testmon/.testmondata"
        record.parent.mkdir()
        record.write_bytes(b"not an sqlite file, long enough to be read as a header" * 4)
        self.assertEqual(self.finish()["status"], "finished")
        self.assertFalse((self.primary / ".git/bss-testmon/testmondata").exists())

    def test_make_target_output_is_removed_with_the_worktree(self):
        for name in (
            "frontend/.shots/a.png",
            "frontend/src/api/generated/types.ts",
            "loadtest/out/seed-report.json",
        ):
            (self.work / name).parent.mkdir(parents=True, exist_ok=True)
            (self.work / name).touch()
        self.assertEqual(self.finish()["status"], "finished")
        self.assertFalse(self.work.exists())

    def test_ownership_mismatch_does_not_stop_runtime(self):
        self.inventory["mismatch"] = True
        self.docker_state.write_text(json.dumps(self.inventory))
        with self.assertRaises(InvalidRuntime):
            self.finish()
        self.assertTrue(json.loads(self.docker_state.read_text())["running"])
        self.assertTrue(self.secret.exists())

    def test_partial_cleanup_failure_can_retry(self):
        self.inventory["fail_down"] = True
        self.docker_state.write_text(json.dumps(self.inventory))
        with self.assertRaises(subprocess.CalledProcessError):
            self.finish()
        self.assertTrue(self.active.exists())
        self.assertTrue(self.work.exists())
        self.inventory["fail_down"] = False
        self.docker_state.write_text(json.dumps(self.inventory))
        self.assertEqual(self.finish()["status"], "finished")

    def test_retry_after_worktree_removal(self):
        original = lifecycle.synchronize
        calls = 0

        def fail_last(primary):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise InvalidRuntime("interrupted final fetch")
            original(primary)

        with patch.object(lifecycle, "synchronize", side_effect=fail_last):
            with self.assertRaises(InvalidRuntime):
                self.finish()
        self.assertFalse(self.work.exists())
        self.assertTrue(self.active.exists())
        self.assertEqual(self.finish()["status"], "finished")

    def test_credentials_replaced_during_cleanup_are_preserved(self):
        original = lifecycle.runtime_inventory

        def replace_secret(*args):
            self.secret.unlink()
            self.secret.write_text("POSTGRES_PASSWORD=changed-credential\n")
            return original(*args)

        with patch.object(lifecycle, "runtime_inventory", side_effect=replace_secret):
            with self.assertRaises(InvalidRuntime):
                self.finish()
        self.assertTrue(self.secret.exists())
        self.assertTrue(self.active.exists())

    def test_primary_fast_forward_failure_preserves_worktree(self):
        self.call(["git", "merge", "--ff-only", self.task], cwd=self.primary)
        self.call(
            [
                "git",
                "-c",
                "user.name=t",
                "-c",
                "user.email=t@t",
                "commit",
                "--allow-empty",
                "-m",
                "diverge",
            ],
            cwd=self.primary,
        )
        with self.assertRaises(subprocess.CalledProcessError):
            self.finish()
        self.assertTrue(self.work.exists())
        self.assertTrue(self.secret.exists())

    def test_unclosed_issue_preserves_resources(self):
        self.state["issue"]["state"] = "OPEN"
        with self.assertRaises(InvalidRuntime):
            self.finish()
        self.assertTrue(self.work.exists())
        self.assertTrue(self.secret.exists())
        self.assertNotIn("board_sets", json.loads(self.fixture.read_text()))

    def test_late_board_automation_is_replaced_by_setting_done(self):
        # Merge and closure are confirmed; the board never moved the item to Done.
        self.state["issue"]["projectItems"][0]["status"]["name"] = "Doing"
        self.assertEqual(self.finish()["status"], "finished")
        self.assertEqual(json.loads(self.fixture.read_text())["board_sets"], 1)
        self.assertFalse(self.work.exists())

    def test_board_already_done_is_not_touched(self):
        self.assertEqual(self.finish()["status"], "finished")
        self.assertNotIn("board_sets", json.loads(self.fixture.read_text()))

    def test_failed_board_fallback_preserves_resources(self):
        self.state["issue"]["projectItems"][0]["status"]["name"] = "Doing"
        self.state["fail_board"] = True
        with self.assertRaises(InvalidRuntime) as raised:
            self.finish()
        self.assertIn("could not be set", str(raised.exception))
        self.assertTrue(self.work.exists())
        self.assertTrue(self.secret.exists())

    def test_completion_telemetry_closes_task_and_sessions_idempotently(self):
        statement = task_telemetry.event_sql(self.task, "task-completed", 0, 0, {})
        self.assertIn("outcome = 'completed'", statement)
        self.assertIn("COALESCE(ended_at, now())", statement)
        self.assertIn("UPDATE agent_sessions", statement)
        self.assertTrue(statement.endswith("COMMIT;"))


if __name__ == "__main__":
    unittest.main()
