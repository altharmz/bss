"""Workflow hook regressions; command strings are inspected, never executed."""

import json
import os
import subprocess
import unittest
from pathlib import Path

from workflow_guard import violation


class WorkflowGuardTests(unittest.TestCase):
    def test_readonly_and_source_editing(self):
        for command in (
            "git status --short", "git diff --cached", "git log -5", "git worktree list",
            "git branch --show-current", "git stash list", "git stash show", "rg lifecycle scripts",
            "git branch --list feature", "gh issue close 93", "gh issue edit 93 --add-label P1",
            "gh pr edit 123 --body-file /tmp/body.md", "./scripts/task-compose --check",
            "cat scripts/task_git.py", "sed -n '1,30p' CLAUDE.md", "gh pr checks 12 --watch",
            "gh api repos/altharmz/bss", "docker logs owned-container", "docker compose ps",
            "gh api search/issues --method GET -f q=repo:altharmz/bss",
            "gh api search/issues --method=GET -f q=repo:altharmz/bss",
            "gh api graphql -f query='query { viewer { login } }'",
            "make commit MESSAGE='git commit is Make-owned' FILES='[\"a.md\"]'",
            "make start-task SLOT=worker2", "make finish-task SLOT=worker2",
            "python3 scripts/test_task_finish.py", "echo hi > scripts/example.py",
            "python3 -c 'from pathlib import Path; Path(\"x.py\").write_text(\"git push\")'",
        ):
            with self.subTest(command=command):
                self.assertIsNone(violation(command))

    def test_raw_mutations_explain_make_path(self):
        for command in (
            "git add x", "git commit -m x", "git push", "git -C /tmp/repo push",
            "git fetch origin", "git merge feature", "git worktree add /tmp/task",
            "git branch -d feature", "git branch feature", "git reset HEAD", "git stash pop",
            "gh pr create", "gh pr merge 12 --squash", "gh issue edit 93 --add-label agent:codex",
            "gh project item-edit --id x --single-select-option-id 47fc9ee4", "gh api -X PATCH repos/o/r/issues/1",
            "gh api graphql -f query='mutation { claim }'", "docker compose up -d",
            "gh api repos/o/r/pulls -f title=example", "gh api repos/o/r/issues --input body.json",
            "gh api repos/o/r/pulls --method=POST", "gh api repos/o/r/pulls -XPOST",
            "gh api graphql -F query=@mutation.graphql",
            "gh project item-edit --id x --single-select-option-id=47fc9ee4",
            "docker --context default compose run app pytest", "./scripts/task-compose up -d",
            "python3 -B scripts/task_git.py commit", "env CLIENT=codex git push",
            "command git push", "/usr/bin/git push", "bash -c 'git push'",
            "env -u CLIENT git push", "bash -lc 'git push'", "git restore --staged x",
            "make push || git push", "make commit; git commit -m bypass",
            "git status && git push", "echo $(git push)",
        ):
            with self.subTest(command=command):
                self.assertIn("make", violation(command))

    def test_failed_make_never_authorizes_fallback(self):
        self.assertIsNone(violation("make push"))
        self.assertIn("make push", violation("git push"))
        self.assertIsNone(violation("git status"))

    def test_shared_and_codex_hooks_apply_the_same_contract(self):
        root = Path(__file__).resolve().parent.parent
        environ = dict(os.environ)
        environ.pop("CLAUDE_CODE_REMOTE", None)
        for hook in (".claude/hooks/guard.sh", ".codex/guard.sh"):
            for command, expected in (("git status", 0), ("git push", 2), ("make push || git push", 2)):
                with self.subTest(hook=hook, command=command):
                    result = subprocess.run(
                        ["bash", str(root / hook)],
                        input=json.dumps({"tool_input": {"command": command}}),
                        capture_output=True, text=True, env=environ,
                    )
                    self.assertEqual(result.returncode, expected, result.stderr)
                    if expected:
                        self.assertIn("make push", result.stderr)


if __name__ == "__main__":
    unittest.main()
