"""scripts/deploy.sh: releases, pointers, retention and the rollback (stdlib only).

No Docker, no network: every test runs the real deploy.sh in a throwaway git repository
with fake `ssh`, `rsync`, `docker`, `timeout` and `curl` first on PATH, and fakes of the
smoke scripts at their real paths. Each fake appends its argv (one JSON line) to
calls.log and answers from a rules file: lines `regex<TAB>exit code<TAB>stdout`, matched
against the space-joined argv (program name first); the first match wins, the default is
exit 0 with empty output. `\\n` in stdout is a newline; `a ||| b` answers a, then b.
Remote state (pointers, release.env, health) is simulated through those rules;
assertions are on the order of the recorded remote commands, matched by regex.
"""

import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

FAKE = r"""#!/usr/bin/env python3
import json, os, re, subprocess, sys
name = os.path.basename(sys.argv[0])
args = [name] + sys.argv[1:]
with open(os.environ["FAKE_LOG"], "a") as log:
    log.write(json.dumps(args) + "\n")
if name == "timeout":
    sys.exit(subprocess.call(sys.argv[2:]))
joined = " ".join(args)
if name == "ssh" and "docker load" in joined:
    sys.stdin.buffer.read()  # the image stream
with open(os.environ["FAKE_RULES"]) as rules:
    for number, line in enumerate(rules):
        line = line.rstrip("\n")
        if not line:
            continue
        pattern, code, out = (line.split("\t") + ["", ""])[:3]
        if re.search(pattern, joined):
            if " ||| " in out:
                # successive answers, the last one repeats
                answers = out.split(" ||| ")
                counter = os.environ["FAKE_RULES"] + f".{number}"
                seen = int(open(counter).read()) if os.path.exists(counter) else 0
                open(counter, "w").write(str(seen + 1))
                out = answers[min(seen, len(answers) - 1)]
            if out:
                sys.stdout.write(out.replace("\\n", "\n") + "\n")
            sys.exit(int(code or 0))
sys.exit(0)
"""

TASK_COMPOSE = """#!/usr/bin/env bash
# fake: the UI build writes index.html with the bss-build marker of the given tag
mkdir -p frontend/dist/assets
printf '<html><meta name="bss-build" content="%s"></html>\\n' "$2" > frontend/dist/index.html
echo 'x' > frontend/dist/assets/a.js
"""

NEW = "new1234"
PREV = "prev123"
OLD = "old0000"


# Under the pre-commit hook git exports GIT_DIR, GIT_INDEX_FILE and friends; inherited,
# they would point every git call here (and deploy.sh's own) at the real repository.
CLEAN_ENV = {k: v for k, v in os.environ.items() if not k.startswith("GIT_") or k == "GIT_PAGER"}


def run(cmd, **kwargs):
    kwargs.setdefault("env", CLEAN_ENV)
    return subprocess.run(cmd, check=False, **kwargs)  # noqa: S603 - test-owned commands in disposable repositories


def release_env(tag, rev):
    return f"tag={tag}\\ncommit={tag}full\\nalembic_rev={rev}\\ndeployed_at=2026-11-01T00:00:00Z"


class DeployHarness(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="bss-deploy-test-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.repo = self.tmp / "repo"
        self.bin = self.tmp / "bin"
        self.log = self.tmp / "calls.log"
        self.rules = self.tmp / "rules"
        self.bin.mkdir()
        for name in ("ssh", "rsync", "docker", "timeout", "curl"):
            self._exe(self.bin / name, FAKE)
        scripts = self.repo / "scripts"
        scripts.mkdir(parents=True)
        shutil.copy(ROOT / "scripts/deploy.sh", scripts / "deploy.sh")
        shutil.copy(ROOT / "scripts/billing-status-check.sh", scripts / "billing-status-check.sh")
        for name in ("web-smoke.sh", "mcp-smoke.sh", "mcp-oauth-smoke.py"):
            self._exe(scripts / name, FAKE)
        self._exe(scripts / "task-compose", TASK_COMPOSE)
        for name in ("compose.yaml", "compose.prod.yaml"):
            shutil.copy(ROOT / name, self.repo / name)
        versions = self.repo / "alembic/versions"
        versions.mkdir(parents=True)
        (versions / "0001_x.py").write_text('revision = "0001"\n')
        (self.repo / ".gitignore").write_text(
            ".env.prod\n.env.smoke\n.deployed\n.deployed.tmp\nfrontend/\n"
        )
        (self.repo / ".env.prod").write_text("BSS_SERVICE_SECRET=x\nBSS_SMOKE_SECRET=y\n")
        (self.repo / ".env.smoke").write_text("BSS_SMOKE_SECRET=y\n")
        git = [shutil.which("git"), "-C", str(self.repo)]
        for step in (
            ["init", "-q"],
            ["add", "."],
            ["-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "x"],
        ):
            run([*git, *step]).check_returncode()

    @staticmethod
    def _exe(path, text):
        path.write_text(text)
        path.chmod(0o755)

    # A healthy remote: CURRENT=prev123 (rev 0001), PREVIOUS=old0000, both stored with
    # their images; every release goes healthy unless a rule says otherwise.
    def base_rules(self, current=PREV, previous=OLD, cur_rev="0001", prev_rev="0001"):
        rules = [
            (r"cat bss/CURRENT", 0, current),
            (r"cat bss/PREVIOUS", 0, previous),
            (rf"cat bss/releases/{NEW}/release\.env", 0, release_env(NEW, "0001")),
            (rf"cat bss/releases/{PREV}/release\.env", 0, release_env(PREV, cur_rev)),
            (rf"cat bss/releases/{OLD}/release\.env", 0, release_env(OLD, prev_rev)),
            (r"cat bss/DEPLOYED", 0, "DEPLOYED-PLACEHOLDER"),
            (r"TAG=(\w+) .* ps -q (\w+)", 0, "cid"),
            (r"docker inspect -f '\{\{\.State\.Health\.Status\}\}'", 0, "healthy"),
            (r"billing-status", 0, '{"status":"ok"} 200'),
        ]
        return rules

    def run_deploy(self, rules, *args, env=None):
        self.rules.write_text("".join(f"{p}\t{c}\t{o}\n" for p, c, o in rules))
        self.log.write_text("")
        full_env = {
            **CLEAN_ENV,
            "PATH": f"{self.bin}:{os.environ['PATH']}",
            "FAKE_LOG": str(self.log),
            "FAKE_RULES": str(self.rules),
            "TAG": NEW,
            "HOME": str(self.tmp),
            **(env or {}),
        }
        full_env.pop("DRY_RUN", None)
        full_env.update(env or {})
        result = run(
            [shutil.which("bash"), "scripts/deploy.sh", *args],
            cwd=self.repo,
            env=full_env,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=120,
        )
        self.out = result.stdout + result.stderr
        self.calls = [
            " ".join(json.loads(line)) for line in self.log.read_text().splitlines() if line
        ]
        return result.returncode

    def commit(self):
        result = run(
            [shutil.which("git"), "-C", str(self.repo), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
        )
        result.check_returncode()
        return result.stdout.strip()

    def deployed_rules(self, rules):
        """The new commit reads back from DEPLOYED (the deploy's own record)."""
        return [(p, c, self.commit() if o == "DEPLOYED-PLACEHOLDER" else o) for p, c, o in rules]

    def index(self, pattern, start=0):
        for i, call in enumerate(self.calls[start:], start):
            if re.search(pattern, call):
                return i
        return -1

    def assertCalled(self, pattern, start=0):
        i = self.index(pattern, start)
        self.assertGreaterEqual(i, 0, f"no call matches {pattern!r}:\n" + "\n".join(self.calls))
        return i

    def assertNotCalled(self, pattern, start=0):
        i = self.index(pattern, start)
        self.assertEqual(i, -1, f"unexpected call {pattern!r}: {self.calls[i] if i >= 0 else ''}")

    def assertBefore(self, first, second, start=0):
        a = self.assertCalled(first, start)
        b = self.assertCalled(second, start)
        self.assertLess(a, b, f"{first!r} should come before {second!r}")


# The deploy's own `up -d` of the new tag; everything after it is armed.
UP_NEW = rf"TAG={NEW} .* up -d"
UP_PREV = rf"TAG={PREV} .* up -d"
NEW_UNHEALTHY = [(rf"TAG={NEW} .* ps -q \w+", 0, "cidnew"), (r"inspect .* cidnew", 0, "unhealthy")]


class AutomaticRollback(DeployHarness):
    def assertRolledBackToPrev(self):
        up = self.assertCalled(UP_NEW)
        stop = self.assertCalled(rf"TAG={NEW} .* stop app worker mcp", up)
        prev_up = self.assertCalled(UP_PREV, stop)
        self.assertCalled(rf"releases/{PREV}/compose\.yaml", stop)
        web = self.assertCalled(
            rf"rsync -a --exclude index\.html releases/{PREV}/web/ web/", prev_up
        )
        self.assertCalled(rf"rsync -a --delete releases/{PREV}/web/ web/", web)
        self.assertCalled(
            rf"printf '%s\\n' '{PREV}' > CURRENT\.tmp && mv CURRENT\.tmp CURRENT", prev_up
        )
        self.assertCalled(r"rm -f PREVIOUS", prev_up)
        self.assertNotCalled(rf"'{NEW}' > CURRENT\.tmp")
        self.assertIn(f"ROLLED BACK to {PREV}", self.out)
        return up

    def test_1_health_gate_failure_rolls_back_without_downgrade(self):
        rules = NEW_UNHEALTHY + self.base_rules()
        rules = [(r"cat bss/DEPLOYED", 0, f"{PREV}full")] + rules
        self.assertEqual(self.run_deploy(rules), 1, self.out)
        self.assertRolledBackToPrev()
        self.assertNotCalled(r"alembic downgrade")
        self.assertIn("containers did not become healthy", self.out)
        record = (self.repo / ".deployed").read_text()
        self.assertIn(f"tag={PREV}\n", record)
        self.assertIn("checks=rolled-back\n", record)
        self.assertIn(f"rolled_back_from={NEW}\n", record)

    def test_2_schema_downgrade_runs_in_the_new_image_before_the_previous_up(self):
        rules = [(rf"cat bss/releases/{NEW}/release\.env", 0, release_env(NEW, "0042"))]
        rules += NEW_UNHEALTHY + [(r"cat bss/DEPLOYED", 0, f"{PREV}full")]
        rules += self.base_rules(cur_rev="0041")
        self.assertEqual(self.run_deploy(rules), 1, self.out)
        self.assertRolledBackToPrev()
        down = self.assertCalled(rf"TAG={NEW} .* run --rm --no-deps app alembic downgrade 0041")
        self.assertLess(down, self.index(UP_PREV, self.index(UP_NEW)))

    def test_3_refused_downgrade_brings_the_new_release_back(self):
        rules = [
            (rf"cat bss/releases/{NEW}/release\.env", 0, release_env(NEW, "0042")),
            (r"alembic downgrade", 1, "refusing: prepaid data exists"),
        ]
        rules += NEW_UNHEALTHY + self.base_rules(cur_rev="0041")
        self.assertEqual(self.run_deploy(rules), 1, self.out)
        up = self.assertCalled(UP_NEW)
        down = self.assertCalled(r"alembic downgrade 0041", up)
        self.assertCalled(UP_NEW, down + 1)
        self.assertNotCalled(UP_PREV)
        self.assertNotCalled(r"> CURRENT\.tmp")
        self.assertNotCalled(r"rm -f PREVIOUS")
        self.assertIn("ROLLBACK REFUSED", self.out)
        self.assertIn("docs/operations.md", self.out)

    def test_4_migration_failure_changes_nothing(self):
        rules = [(r"alembic upgrade head", 1, "boom")] + self.base_rules()
        self.assertEqual(self.run_deploy(rules), 1, self.out)
        self.assertNotCalled(r" up -d")
        self.assertNotCalled(r"stop app worker mcp")
        self.assertNotCalled(r"alembic downgrade")
        self.assertNotCalled(r"> CURRENT\.tmp|> PREVIOUS\.tmp|rm -f PREVIOUS")
        self.assertIn("nothing changed on the server", self.out)
        self.assertIn(f"{PREV} is still serving", self.out)
        self.assertNotIn("ROLLED BACK", self.out)

    def test_5_late_web_smoke_failure_rolls_back(self):
        # DEPLOYED reads back the new commit, then (after the rollback) the previous one
        rules = [
            (rf"web-smoke\.sh .* {NEW}$", 1, ""),
            (r"cat bss/DEPLOYED", 0, f"{self.commit()} ||| {PREV}full"),
        ]
        rules += self.base_rules()
        self.assertEqual(self.run_deploy(rules), 1, self.out)
        sync = self.assertCalled(rf"rsync -a --delete releases/{NEW}/web/ web/")
        self.assertLess(self.index(UP_NEW), sync)
        self.assertGreater(self.assertCalled(rf"web-smoke\.sh .* {NEW}$"), sync)
        self.assertRolledBackToPrev()
        self.assertCalled(rf"web-smoke\.sh .* {PREV}$", self.index(UP_PREV))
        self.assertIn(
            f"ROLLED BACK to {PREV} ({PREV}full); the failure was: web smoke test failed", self.out
        )

    def test_11_drill_forces_a_rollback_after_the_health_gate(self):
        rules = [(r"cat bss/DEPLOYED", 0, f"{PREV}full")] + self.base_rules()
        code = self.run_deploy(rules, env={"BSS_DEPLOY_DRILL": "fail-after-health"})
        self.assertEqual(code, 1, self.out)
        self.assertIn("drill: forced failure after the health gate", self.out)
        self.assertIn("BSS_DEPLOY_DRILL", self.out)
        self.assertRolledBackToPrev()


class ManualRollback(DeployHarness):
    def manual_rules(self, **kw):
        # the server runs prev123 (CURRENT) and keeps old0000 (PREVIOUS)
        return [(r"cat bss/DEPLOYED", 0, f"{OLD}full")] + self.base_rules(**kw)

    def test_6_no_previous_release(self):
        rules = [(r"cat bss/PREVIOUS", 1, "")] + self.manual_rules()
        self.assertEqual(self.run_deploy(rules, "--rollback"), 1, self.out)
        self.assertIn("cannot roll back: no previous release", self.out)
        self.assertNotCalled(r" up -d")
        self.assertNotCalled(r"stop app worker mcp")

    def test_6b_previous_image_missing(self):
        rules = [(rf"docker image inspect {re.escape('bss-app')}:{OLD}", 1, "")]
        rules += self.manual_rules()
        self.assertEqual(self.run_deploy(rules, "--rollback"), 1, self.out)
        self.assertIn("cannot roll back: no previous release", self.out)
        self.assertNotCalled(r" up -d")

    def test_7_manual_rollback_succeeds(self):
        self.assertEqual(self.run_deploy(self.manual_rules(), "--rollback"), 0, self.out)
        stop = self.assertCalled(rf"TAG={PREV} .* stop app worker mcp")
        self.assertCalled(rf"TAG={OLD} .* up -d", stop)
        self.assertCalled(rf"'{OLD}' > CURRENT\.tmp", stop)
        self.assertCalled(r"rm -f PREVIOUS", stop)
        self.assertNotCalled(r"docker build|alembic upgrade|mcp-oauth-smoke")
        self.assertCalled(rf"web-smoke\.sh .* {OLD}$")
        record = (self.repo / ".deployed").read_text()
        self.assertIn(f"commit={OLD}full\n", record)
        self.assertIn(f"tag={OLD}\n", record)
        self.assertIn("checks=rolled-back\n", record)
        self.assertIn(f"rolled_back_from={PREV}\n", record)

    def test_8_dry_run_changes_nothing(self):
        rules = self.manual_rules(cur_rev="0041", prev_rev="0040")
        code = self.run_deploy(rules, "--rollback", env={"DRY_RUN": "1"})
        self.assertEqual(code, 0, self.out)
        self.assertIn(PREV, self.out)
        self.assertIn(OLD, self.out)
        self.assertIn("schema downgrade: yes", self.out)
        for pattern in (r" up -d", r"\bstop\b", r"downgrade", r"\bmv\b", r"\brm\b", r"> \w"):
            remote = [
                c for c in self.calls if c.startswith(("ssh", "rsync")) and "--dry-run" not in c
            ]
            self.assertFalse(
                [c for c in remote if re.search(pattern, c)], f"mutating call {pattern!r}"
            )
        self.assertFalse((self.repo / ".deployed").exists())

    def test_make_deployed_reads_a_rolled_back_record(self):
        self.assertEqual(self.run_deploy(self.manual_rules(), "--rollback"), 0, self.out)
        makefile = (ROOT / "Makefile").read_text()
        recipe = re.search(r"^deployed:.*\n((?:\t.*\n)+)", makefile, re.M).group(1)
        (self.repo / "Makefile").write_text("deployed:\n" + recipe)
        result = run(
            [shutil.which("make"), "-s", "deployed"], cwd=self.repo, capture_output=True, text=True
        )
        self.assertIn(f"rolled back from {PREV}", result.stdout + result.stderr)


class Retention(DeployHarness):
    def test_9_image_prune_keeps_current_and_previous(self):
        tags = [NEW, "aaa1111", "bbb2222", "ccc3333", PREV, "ddd4444", OLD, "<none>"]
        images = "\\n".join(
            f"bss-app:{tag} id{tag.strip('<>')} 2026-11-{8 - n:02d} 10:00:00 +0000 UTC"
            for n, tag in enumerate(tags)
        )
        rules = [
            (r"docker images", 0, images),
            (r"ls bss/releases", 0, f"{NEW}\\n{PREV}\\n{OLD}\\naaa1111"),
        ]
        rules += self.deployed_rules(self.base_rules())
        self.assertEqual(self.run_deploy(rules), 0, self.out)
        rmi = self.calls[self.assertCalled(r"docker rmi")]
        self.assertIn("bss-app:ccc3333", rmi)
        self.assertIn("bss-app:ddd4444", rmi)
        self.assertIn(f"bss-app:{OLD}", rmi)  # neither CURRENT nor PREVIOUS any more
        self.assertIn("idnone", rmi)  # a dangling image goes by its ID
        for kept in (NEW, PREV, "aaa1111", "bbb2222"):
            self.assertNotIn(f"bss-app:{kept}", rmi)
        # pointers moved: PREVIOUS = the old CURRENT, CURRENT = the new tag
        moved = self.calls[self.assertCalled(r"> CURRENT\.tmp")]
        self.assertIn(f"'{PREV}' > PREVIOUS.tmp", moved)
        self.assertIn(f"'{NEW}' > CURRENT.tmp", moved)
        rm = self.calls[self.assertCalled(r"rm -rf")]
        self.assertIn(f"releases/{OLD}", rm)
        self.assertIn("releases/aaa1111", rm)
        self.assertNotIn(f"releases/{NEW}", rm)
        self.assertNotIn(f"releases/{PREV}", rm)
        self.assertIn("checks=passed", (self.repo / ".deployed").read_text())

    def test_redeploying_current_keeps_previous(self):
        rules = self.deployed_rules(self.base_rules(current=NEW, previous=PREV))
        self.assertEqual(self.run_deploy(rules), 0, self.out)
        self.assertNotCalled(r"> PREVIOUS\.tmp|rm -f PREVIOUS")
        self.assertCalled(rf"'{NEW}' > CURRENT\.tmp")

    def test_release_dir_is_stored_before_the_new_release_starts(self):
        rules = self.deployed_rules(self.base_rules())
        self.assertEqual(self.run_deploy(rules), 0, self.out)
        env = self.assertCalled(
            rf"tag={NEW}\\ncommit=\w+\\nalembic_rev=0001\\ndeployed_at=.* > release\.env\.tmp"
        )
        self.assertLess(env, self.index(UP_NEW))
        self.assertCalled(
            rf"rsync .*compose\.yaml compose\.prod\.yaml vps-test:bss/releases/{NEW}/"
        )
        self.assertCalled(rf"rsync .*frontend/dist/ vps-test:bss/releases/{NEW}/web/")
        self.assertBefore(
            rf"rsync -a --exclude index\.html releases/{NEW}/web/ web/",
            rf"rsync -a releases/{NEW}/web/index\.html web/index\.html",
        )

    def test_10_bootstrap_records_the_running_release(self):
        rules = [
            # missing until the bootstrap writes it
            (r"cat bss/CURRENT", 1, " ||| old123"),
            (r"cat bss/PREVIOUS", 1, ""),
            (r"docker ps -q --filter label=com\.docker\.compose\.project=bss", 0, "cidold"),
            (r"docker inspect -f '\{\{\.Config\.Image\}\}' cidold", 0, "bss-app:old123"),
            (r"exec -T app alembic current", 0, "0001 (head)"),
        ]
        rules += self.deployed_rules(self.base_rules())
        # bootstrap reads DEPLOYED before the deploy writes it; the same rule serves both
        self.assertEqual(self.run_deploy(rules), 0, self.out)
        made = self.assertCalled(r"mkdir -p releases/old123")
        bootstrap = self.calls[made]
        self.assertIn("cp compose.yaml compose.prod.yaml releases/old123/", bootstrap)
        self.assertIn("alembic_rev=0001", bootstrap)
        cur = self.assertCalled(r"'old123' > CURRENT\.tmp")
        self.assertLess(cur, self.index(UP_NEW))
        self.assertLess(
            cur, self.index(r"rsync .*compose\.yaml compose\.prod\.yaml vps-test:bss/$")
        )
        moved = self.calls[self.assertCalled(rf"'{NEW}' > CURRENT\.tmp")]
        self.assertIn("'old123' > PREVIOUS.tmp", moved)

    @unittest.skipUnless(shutil.which("rsync"), "needs rsync")
    def test_deploy_dry_run_lists_the_release_files_and_touches_no_server(self):
        (self.bin / "rsync").unlink()  # the real rsync, into a local scratch directory
        code = self.run_deploy(self.base_rules(), env={"DRY_RUN": "1"})
        self.assertEqual(code, 0, self.out)
        self.assertIn(f"releases/{NEW}/compose.yaml", self.out)
        self.assertIn(f"releases/{NEW}/web/index.html", self.out)
        self.assertIn(f"1 files under releases/{NEW}/web/assets/", self.out)
        self.assertNotIn(".env.smoke", self.out.replace(".env.smoke is not shipped", ""))
        self.assertFalse([c for c in self.calls if c.startswith("ssh")])

    def test_first_deploy_ever_says_no_rollback_is_possible(self):
        rules = [(r"cat bss/CURRENT", 1, ""), (r"cat bss/PREVIOUS", 1, "")]
        rules += self.deployed_rules(self.base_rules())
        self.assertEqual(self.run_deploy(rules), 0, self.out)
        self.assertIn("no rollback possible", self.out)
        moved = self.calls[self.assertCalled(rf"'{NEW}' > CURRENT\.tmp")]
        self.assertNotIn("PREVIOUS.tmp", moved)


if __name__ == "__main__":
    unittest.main()
