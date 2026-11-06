"""Public Make lifecycle dogfood in disposable repos, with fake GitHub and Docker.

Real Git, locks, hooks, reservations and credential cleanup are exercised. The only
repository validation substitution accepts this fixture's local bare origin.
"""

import json
import shutil
import sys
import unittest
from pathlib import Path

import test_start_task as startup_fixtures

SCRIPTS = startup_fixtures.SCRIPTS
issue = startup_fixtures.issue


class WorkflowLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.f = startup_fixtures.StartupTests(methodName="runTest")
        self.addCleanup(self.f.doCleanups)
        self.f.setUp()
        f = self.f
        for name in (
            "task_quality.py", "task_git.py", "task_pr.py", "task_finish.py",
            "install-hooks.py", "task-compose",
        ):
            shutil.copy2(SCRIPTS / name, f.primary / "scripts" / name)
        # Keep production validation unchanged: only the disposable fixture accepts a
        # local origin. Imported functions and the public Make entry points are real.
        pr = f.primary / "scripts/task_pr.py"
        pr.rename(pr.with_name("task_pr_impl.py"))
        pr.write_text(
            "from task_pr_impl import *\nimport task_pr_impl as impl\n"
            "impl.repository = lambda root: None\nrepository = impl.repository\n"
            "if __name__ == '__main__':\n import sys; sys.exit(impl.main())\n"
        )
        for name in ("pre-commit", "pre-push"):
            (f.primary / "scripts" / (name + ".sh")).write_text("exit 0\n")
        # Format's language tools are simulated, leaving quality orchestration real.
        with (f.primary / "Makefile").open("a") as out:
            out.write("\nfmt web-fmt:\n\t@true\n")
        # This fixture captures telemetry through a fake docker, so it opts back in:
        # a checkout with the compose file and no disable switch reaches "storage".
        shutil.copy2(SCRIPTS.parent / "compose.telemetry.yaml", f.primary)
        f.env.pop("BSS_TELEMETRY_DISABLED", None)
        f.git("add", ".")
        f.git("commit", "-qm", "lifecycle fixture")
        f.git("push", "-q", "origin", "master")
        f.call(["make", "hooks"])
        for key in ("GIT_CONFIG_GLOBAL", "GIT_CONFIG_NOSYSTEM"):
            f.env.pop(key, None)
        self.events = f.base / "events.sql"
        # Telemetry intentionally strips arbitrary environment variables. Embed only
        # this fixture-owned output path in the executable rather than changing policy.
        docker = f.fake_bin / "docker"
        docker.write_text(
            f"#!{sys.executable}\nimport sys\nfrom pathlib import Path\n"
            "a=sys.argv[1:]\n"
            f"if 'bss-telemetry' in a:\n Path({str(self.events)!r}).open('a').write(sys.stdin.read()); sys.exit(0)\n"
            "if a[-3:]==['ps','--format','json']: print('[]')\n"
        )
        docker.chmod(0o755)
        gh = f.fake_bin / "gh"
        gh.rename(f.fake_bin / "startup-gh")
        # A tiny GitHub simulator shares startup's board and records PRs by task.
        gh.write_text(
            f"#!{sys.executable}\n"
            "import json,os,subprocess,sys\nfrom pathlib import Path\n"
            "a=sys.argv[1:]\n"
            "if a[:2] not in (['pr','list'],['pr','create'],['pr','view'],['pr','merge'],['issue','view']) and not (a[0]=='api' and 'graphql' not in a):\n"
            " os.execv(sys.executable,[sys.executable,str(Path(__file__).with_name('startup-gh')),*a])\n"
            f"p=Path({str(f.base / 'lifecycle.json')!r})\n"
            "d=json.loads(p.read_text()) if p.exists() else {}\n"
            "def git(*v): return subprocess.check_output(['git','-c','user.name=Lifecycle fixture','-c','user.email=lifecycle@example.test',*v],text=True).strip()\n"
            "def save(): p.write_text(json.dumps(d))\n"
            "if a[:2]==['pr','list']:\n"
            " branch=a[a.index('--head')+1]; out=[{'number':v['number']} for v in d.values() if v['headRefName']==branch]\n"
            "elif a[:2]==['pr','create']:\n"
            " branch=a[a.index('--head')+1]; number=int(branch.split('-')[1]); head=git('rev-parse','HEAD')\n"
            " d[str(number)]={'number':number,'url':f'https://github.com/altharmz/bss/pull/{number}','state':'OPEN',"
            "'body':Path(a[a.index('--body-file')+1]).read_text(),'baseRefName':'master','headRefName':branch,"
            "'headRefOid':head,'isCrossRepository':False,'isDraft':False,'mergeable':'MERGEABLE',"
            "'mergeStateStatus':'CLEAN','reviewDecision':'','labels':[],'changedFiles':1,"
            "'statusCheckRollup':[{'__typename':'CheckRun','status':'COMPLETED','conclusion':'SUCCESS'}]}\n"
            " save(); sys.exit(0)\n"
            "elif a[:2]==['pr','view']: out=d[a[2]]\n"
            "elif a[:2]==['pr','merge']:\n"
            " pr=d[a[2]]; assert a[a.index('--match-head-commit')+1]==pr['headRefOid']\n"
            " assert '--squash' in a and '--admin' not in a\n"
            " remote=git('remote','get-url','origin'); head=pr['headRefOid']\n"
            " parent=git('--git-dir',remote,'rev-parse','master')\n"
            " tree=git('--git-dir',remote,'rev-parse',head+'^{tree}')\n"
            " merge=git('--git-dir',remote,'commit-tree',tree,'-p',parent,'-m','squashed fixture')\n"
            " git('--git-dir',remote,'update-ref','refs/heads/master',merge)\n"
            " pr.update(state='MERGED',mergeCommit={'oid':merge}); save()\n"
            " board=Path(os.environ['BSS_TEST_BOARD']); pages=json.loads(board.read_text())\n"
            " for page in pages:\n"
            "  for row in page['node']['items']['nodes']:\n"
            "   if row['content']['number']==int(a[2]): row['content']['state']='CLOSED'; row['fieldValueByName']['name']='Done'\n"
            " board.write_text(json.dumps(pages)); sys.exit(0)\n"
            "elif a[:2]==['issue','view']:\n"
            " pr=d.get(a[2]); done=pr and pr['state']=='MERGED'\n"
            " out={'number':int(a[2]),'state':'CLOSED' if done else 'OPEN','labels':[],"
            "'projectItems':[{'title':'BSS','status':{'name':'Done' if done else 'Doing'}}]}\n"
            "elif '/files?' in a[-1]: out=[{'filename':'notes.md'}]\n"
            "elif '/compare/' in a[-1]: out={'behind_by':0}\n"
            "else: out={'allow_squash_merge':True,'allow_merge_commit':False,'allow_rebase_merge':False,'archived':False}\n"
            "print(json.dumps(out))\n"
        )
        gh.chmod(0o755)

    def make(self, target, cwd, *args):
        result = self.f.call(["make", target, *args], cwd=cwd, check=False)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result.stdout

    def start(self, number, slot, client):
        f = self.f
        f.env.update(BSS_SLOT=slot, BSS_TASK_OWNER=client)
        f.board([issue(number)])
        result = f.result()
        self.assertEqual(result["agent"], "agent:" + client)
        self.assertEqual(result["slot"], slot)
        return result

    def complete(self, result, cleanup=True):
        f = self.f
        work = Path(result["worktree"])
        slot = work.parent
        f.env["BSS_SLOT"] = slot.name
        self.make("sync-task", work)
        self.make("format", work)
        filename = "notes-" + result["branch"].split("-")[1] + ".md"
        (work / filename).write_text("Focused disposable dogfood task.\n")
        self.make("review-task", work)
        self.make("commit", work, "MESSAGE=Dogfood fixture", "FILES=" + json.dumps([filename]))
        self.make("push", work)
        self.make("create-pr", work)
        self.assertIn('"status": "ready"', self.make("check-pr", work))
        self.assertIn('"status": "merged"', self.make("merge-task", work))
        (slot / "state").mkdir(exist_ok=True)
        (slot / "state/keep").write_text("persistent operator state")
        if not cleanup:
            return
        self.assertIn('"status": "finished"', self.make("finish-task", f.primary))
        self.assertTrue((slot / "state/keep").exists())
        self.assertTrue((slot / "slot.lock").exists())
        self.assertFalse((slot / "active.json").exists())
        self.assertFalse(work.exists())
        self.assertFalse(Path(result["secret_file"]).exists())
        # Squash heads are retained safely when Git cannot delete without force.
        self.assertEqual(f.git("status", "--porcelain"), "")

    def test_bare_startup_recovers_confirmed_completed_slot(self):
        first = self.start(81, "worker1", "codex")
        self.complete(first, cleanup=False)
        self.f.env.pop("BSS_SLOT", None)
        self.f.board([issue(82)])
        second = self.f.result()
        self.assertEqual(second["slot"], "worker1")
        self.assertEqual(first["worktree"], second["worktree"])
        self.assertFalse(Path(first["secret_file"]).exists())
        slot = Path(second["worktree"]).parent
        self.assertEqual((slot / "state/keep").read_text(), "persistent operator state")
        self.assertTrue((slot / ("finish-" + first["branch"] + ".json")).exists())
        self.complete(second)

    def test_slots_clients_reuse_and_telemetry_through_public_make(self):
        first = self.start(81, "worker1", "codex")
        second = self.start(82, "worker2", "claude")
        self.assertTrue(Path(first["worktree"]).exists())
        self.assertNotEqual(first["app_port"], second["app_port"])
        self.complete(second)
        # Finishing worker2 must preserve worker1's resources and reservation.
        self.assertTrue(Path(first["secret_file"]).exists())
        self.assertTrue((Path(first["worktree"]).parent / "active.json").exists())
        self.complete(first)
        third = self.start(83, "worker1", "kimi")
        self.assertEqual(first["worktree"], third["worktree"])
        self.assertNotEqual(first["branch"], third["branch"])
        self.complete(third)
        statements = self.events.read_text()
        for result in (first, second, third):
            # Every lifecycle event retains task identity, even after worktree removal.
            for operation in ("start-task", "sync-task", "format", "review-task", "commit", "push", "create-pr", "check-pr", "merge-task", "task-completed", "finish-task"):
                self.assertTrue(any(result["branch"] in line and "'" + operation + "'" in line for line in statements.splitlines()), operation)
        for client in ("codex", "claude", "kimi"):
            self.assertIn('"client":"' + client + '"', statements)


if __name__ == "__main__":
    unittest.main()
