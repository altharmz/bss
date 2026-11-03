"""make clean-tasks and the shared leftover removal, with fake docker and gh."""

import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import task_cleanup as cleanup

DONE, ABANDONED, OPEN, TREE, RUNNING, RESERVED, BORROWED = (
    "issue-5-done-task",
    "issue-6-abandoned",
    "issue-7-open-pr",
    "issue-8-has-worktree",
    "issue-9-running",
    "issue-10-reserved",
    "issue-11-moved-branch",
)
ALL = [DONE, ABANDONED, OPEN, TREE, RUNNING, RESERVED, BORROWED]

DOCKER = r"""#!PYTHON
import json, os, sys
from pathlib import Path
p = Path(os.environ['BSS_TEST_DOCKER']); d = json.loads(p.read_text()); a = sys.argv[3:]
def save(): p.write_text(json.dumps(d))
if a[:2] == ['volume', 'ls']:
    if '--format' in a:
        print('\n'.join(project for project in d['volumes'].values()))
    else:
        want = a[-1].split('=', 2)[2]
        print('\n'.join(n for n, project in d['volumes'].items() if project == want))
elif a[:2] == ['volume', 'inspect']:
    print(json.dumps({'com.docker.compose.project': d['volumes'][a[-1]]}))
elif a[:2] == ['volume', 'rm']:
    del d['volumes'][a[2]]; d['removed'].append(a[2]); save()
elif a[:2] == ['image', 'ls']:
    if a[-1].startswith('reference='):
        print('\n'.join(d['images']))
    else:
        print(a[-1] if a[-1] in d['images'] else '')
elif a[:2] == ['image', 'rm']:
    d['images'].remove(a[2]); d['removed'].append(a[2]); save()
elif a[0] == 'ps':
    if any(x.startswith('label=') for x in a):
        project = a[-1].split('=', 2)[2]
        print('abc' if project in d['running'] else '')
    else:
        print('')
else:
    sys.exit(2)
"""

GH = r"""#!PYTHON
import json, os, sys
from pathlib import Path
d = json.loads(Path(os.environ['BSS_TEST_GH']).read_text()); a = sys.argv[1:]
assert '--repo' in a
if a[:2] == ['issue', 'view']:
    row = d['issues'][a[2]]
    status = {'name': row['board']}
    out = {'state': row['state'], 'projectItems': [{'title': 'BSS', 'status': status}]}
elif a[:2] == ['pr', 'list']:
    out = d['prs'].get(a[a.index('--head') + 1], [])
else:
    sys.exit(2)
print(json.dumps(out))
"""


class CleanTasksTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="bss-clean-test-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.primary = self.base / "primary"
        self.bin = self.base / "bin"
        self.bin.mkdir()
        self.config = self.base / "config"
        self.git("init", "-q", "-b", "master", str(self.primary), cwd=self.base)
        self.commit("initial")
        self.heads = {}
        for task in ALL:
            self.git("branch", task)
            self.heads[task] = self.git("rev-parse", task).strip()
        # A task whose branch moved on after its PR head: kept, never deleted.
        self.git("checkout", "-q", BORROWED)
        self.commit("extra work after the merged head")
        self.git("checkout", "-q", "master")
        self.worktree = self.base / "tree"
        self.git("worktree", "add", "-q", str(self.worktree), TREE)
        projects = {t: "bss-" + t for t in ALL}
        self.docker_state = self.base / "docker.json"
        self.docker_state.write_text(
            json.dumps(
                {
                    "volumes": {
                        **{f"{projects[t]}_db": projects[t] for t in ALL},
                        "bss-telemetry_data": "bss-telemetry",
                    },
                    "images": [
                        *(f"{projects[t]}-app:dev" for t in ALL),
                        "bss-telemetry:latest",
                        "postgres:16",
                    ],
                    "running": [projects[RUNNING]],
                    "removed": [],
                }
            )
        )
        merged = lambda t: [{"number": 1, "state": "MERGED", "headRefOid": self.heads[t]}]  # noqa: E731
        closed = {"state": "CLOSED", "board": "Done"}
        self.gh_state = self.base / "gh.json"
        self.gh_state.write_text(
            json.dumps(
                {
                    "issues": {
                        **{
                            str(t.split("-")[1]): closed
                            for t in (DONE, TREE, RUNNING, RESERVED, BORROWED)
                        },
                        "6": {"state": "OPEN", "board": "Doing"},
                        "7": {"state": "CLOSED", "board": "Done"},
                    },
                    "prs": {
                        **{t: merged(t) for t in (DONE, TREE, RUNNING, RESERVED, BORROWED)},
                        OPEN: [{"number": 2, "state": "OPEN", "headRefOid": self.heads[OPEN]}],
                    },
                }
            )
        )
        for name, body in (("docker", DOCKER), ("gh", GH)):
            path = self.bin / name
            path.write_text(body.replace("PYTHON", sys.executable, 1))
            path.chmod(0o755)
        for task in (DONE, ABANDONED, RESERVED):
            (self.config / "bss/tasks" / task).mkdir(parents=True)
            (self.config / "bss/tasks" / task / ".env").write_text("POSTGRES_PASSWORD=x\n")
        slots = self.base / "primary-agents/worker1"
        slots.mkdir(parents=True)
        (slots / "active.json").write_text(json.dumps({"version": 1, "task_id": RESERVED}))
        self.env = {
            "PATH": f"{self.bin}{os.pathsep}{os.environ['PATH']}",
            "HOME": str(self.base),
            "XDG_CONFIG_HOME": str(self.config),
            "BSS_TEST_DOCKER": str(self.docker_state),
            "BSS_TEST_GH": str(self.gh_state),
            "BSS_GH_RETRY_PAUSE": "0",
        }

    def git(self, *args, cwd=None):
        return subprocess.run(  # noqa: S603 - test-owned disposable repository
            ["git", "-c", "user.name=t", "-c", "user.email=t@example.test", *args],  # noqa: S607
            cwd=cwd or self.primary,
            env={k: v for k, v in os.environ.items() if not k.startswith("GIT_")},
            capture_output=True,
            text=True,
            check=True,
        ).stdout

    def commit(self, message):
        self.git("commit", "-q", "--allow-empty", "-m", message)

    def run_clean(self, confirm=False):
        env = {**self.env, **({"CONFIRM": "1"} if confirm else {})}
        output = io.StringIO()
        before = os.getcwd()
        try:
            os.chdir(self.primary)
            with (
                patch.dict(os.environ, env, clear=True),
                contextlib.redirect_stdout(output),
                contextlib.redirect_stderr(io.StringIO()),
            ):
                code = cleanup.main()
        finally:
            os.chdir(before)
        self.assertEqual(code, 0)
        return json.loads(output.getvalue().splitlines()[0]), output.getvalue()

    def docker_now(self):
        return json.loads(self.docker_state.read_text())

    def test_listing_names_exactly_the_finished_tasks_and_removes_nothing(self):
        listing, text = self.run_clean()
        by_task = {c["task"]: c for c in listing["candidates"]}
        self.assertEqual(sorted(by_task), sorted([DONE, BORROWED]))
        done = by_task[DONE]
        self.assertEqual(done["volumes"], [f"bss-{DONE}_db"])
        self.assertEqual(done["images"], [f"bss-{DONE}-app:dev"])
        self.assertEqual((done["branch"], done["credentials"]), (DONE, True))
        moved = by_task[BORROWED]
        self.assertIsNone(moved["branch"])
        self.assertEqual(moved["kept"][0]["reason"], "tip differs from the merged PR head")
        kept = {row["task"]: row["reason"] for row in listing["kept"]}
        self.assertEqual(
            kept,
            {
                ABANDONED: "issue is not closed and Done",
                OPEN: "open PR",
                TREE: "registered worktree",
                RUNNING: "running container",
                RESERVED: "slot reservation",
            },
        )
        self.assertIn("nothing removed", text)
        self.assertEqual(self.docker_now()["removed"], [])
        self.assertTrue((self.config / "bss/tasks" / DONE / ".env").exists())
        self.assertIn(DONE, self.git("branch", "--list", DONE))

    def test_confirm_removes_exactly_what_the_listing_named(self):
        listing, _ = self.run_clean()
        applied, text = self.run_clean(confirm=True)
        self.assertNotIn("nothing removed", text)
        listed = {
            *(v for c in listing["candidates"] for v in c["volumes"]),
            *(i for c in listing["candidates"] for i in c["images"]),
        }
        self.assertEqual(set(self.docker_now()["removed"]), listed)
        now = self.docker_now()
        # Telemetry, unrelated and kept tasks' resources stay.
        self.assertIn("bss-telemetry_data", now["volumes"])
        self.assertIn("postgres:16", now["images"])
        self.assertIn("bss-telemetry:latest", now["images"])
        for task in (ABANDONED, OPEN, TREE, RUNNING, RESERVED):
            self.assertIn(f"bss-{task}_db", now["volumes"])
            self.assertIn(f"bss-{task}-app:dev", now["images"])
            self.assertIn(task, self.git("branch", "--list", task))
        self.assertEqual(self.git("branch", "--list", DONE), "")
        self.assertIn(BORROWED, self.git("branch", "--list", BORROWED))
        self.assertFalse((self.config / "bss/tasks" / DONE).exists())
        # Abandoned and reserved tasks keep their credentials.
        self.assertTrue((self.config / "bss/tasks" / ABANDONED / ".env").exists())
        self.assertTrue((self.config / "bss/tasks" / RESERVED / ".env").exists())
        by_task = {c["task"]: c for c in applied["candidates"]}
        self.assertEqual(by_task[DONE]["removed"]["branch"], DONE)

    def test_a_second_listing_after_cleanup_has_nothing_to_remove(self):
        self.run_clean(confirm=True)
        listing, _ = self.run_clean()
        self.assertEqual([c["task"] for c in listing["candidates"]], [BORROWED])
        self.assertEqual(listing["candidates"][0]["volumes"], [])


if __name__ == "__main__":
    unittest.main()
