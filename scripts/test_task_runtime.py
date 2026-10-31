#!/usr/bin/env python3
"""Host-only regression tests; use disposable Git repositories and fake Docker."""

import json
import os
import shutil
import socket
import subprocess
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="bss-runtime-test-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.primary = self.base / "primary"
        self.task = "issue-77-example-task"
        self.work = self.base / self.task
        self.env = {
            key: value
            for key, value in os.environ.items()
            if (not key.startswith(("COMPOSE_", "GIT_")) or key == "GIT_PAGER")
            and key
            not in {
                "IMAGE_NAME",
                "WEB_IMAGE_NAME",
                "TAG",
                "APP_PORT",
                "MCP_PORT",
                "WEB_PORT",
                "MCP_PUBLIC_URL",
                "POSTGRES_PASSWORD",
                "POSTGRES_USER",
                "POSTGRES_DB",
                "MCP_USERS",
                "BSS_SERVICE_SECRET",
                "BSS_SMOKE_SECRET",
                "DATABASE_URL",
                "APP_ENV",
            }
        }
        self.env["XDG_CONFIG_HOME"] = str(self.base / "config")
        self.call(["git", "init", "-q", str(self.primary)], cwd=self.base)
        self.call(
            [
                "git",
                "-c",
                "user.name=test",
                "-c",
                "user.email=test@example.test",
                "commit",
                "-q",
                "--allow-empty",
                "-m",
                "initial",
            ],
            cwd=self.primary,
        )
        self.call(
            ["git", "worktree", "add", "-q", "-b", self.task, str(self.work)],
            cwd=self.primary,
        )
        (self.work / "scripts").mkdir()
        for name in ("task-compose", "task_runtime.py", "task_telemetry.py", "install-hooks.py"):
            shutil.copy2(SCRIPTS / name, self.work / "scripts" / name)
        self.metadata = {
            "version": 1,
            "task_id": self.task,
            "app_port": 21000,
            "mcp_port": 21001,
            "web_port": 21002,
        }
        (self.work / ".task").mkdir()
        self.write_metadata()
        self.secret = self.base / "config/bss/tasks" / self.task / ".env"
        self.secret.parent.mkdir(parents=True)
        self.secret.write_text("POSTGRES_PASSWORD=fixture-secret-never-output\n")
        self.bin = self.base / "bin"
        self.bin.mkdir()
        docker = self.bin / "docker"
        docker.write_text(
            "#!/usr/bin/env python3\nimport json,os,sys\n"
            "if sys.argv[-3:] == ['ps','--format','json']: print('[]')\n"
            "elif sys.argv[-2:] != ['config','--quiet']:\n"
            " print(json.dumps({'args':sys.argv[1:],'cwd':os.getcwd(),"
            "'values':{k:os.environ[k] for k in ['COMPOSE_PROJECT_NAME','IMAGE_NAME',"
            "'WEB_IMAGE_NAME','TAG','APP_PORT','MCP_PORT','WEB_PORT','MCP_PUBLIC_URL']}}))\n"
        )
        docker.chmod(0o755)
        self.env["PATH"] = str(self.bin) + os.pathsep + self.env["PATH"]

    def call(self, command, cwd=None, check=True, **kwargs):
        return subprocess.run(  # noqa: S603 - test-owned commands in disposable repositories
            command,
            cwd=cwd or self.work,
            env=self.env,
            text=True,
            capture_output=True,
            check=check,
            **kwargs,
        )

    def write_metadata(self):
        (self.work / ".task/runtime.json").write_text(json.dumps(self.metadata))

    def wrapper(self, *args, cwd=None):
        return self.call([str(self.work / "scripts/task-compose"), *args], cwd=cwd, check=False)

    def rejected(self, result, message):
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(message, result.stderr)
        self.assertNotIn("fixture-secret-never-output", result.stdout + result.stderr)

    def test_derivations_and_explicit_arguments(self):
        result = self.wrapper("ps")
        self.assertEqual(result.returncode, 0, result.stderr)
        output = json.loads(result.stdout)
        self.assertEqual(output["cwd"], str(self.work))
        self.assertEqual(
            output["values"],
            {
                "COMPOSE_PROJECT_NAME": f"bss-{self.task}",
                "IMAGE_NAME": f"bss-{self.task}-app",
                "WEB_IMAGE_NAME": f"bss-{self.task}-web",
                "TAG": "dev",
                "APP_PORT": "21000",
                "MCP_PORT": "21001",
                "WEB_PORT": "21002",
                "MCP_PUBLIC_URL": "http://localhost:21001",
            },
        )
        args = output["args"]
        self.assertEqual(args[args.index("--env-file") + 1], str(self.secret))
        self.assertIn(str(self.work / "compose.yaml"), args)
        self.assertIn(str(self.work / "compose.override.yaml"), args)
        self.assertNotIn("fixture-secret-never-output", result.stdout)

    def test_workflow_result_survives_telemetry_failure(self):
        (self.bin / "docker").write_text(
            "#!/usr/bin/env python3\nimport sys\n"
            "if 'psql' in sys.argv: sys.exit(1)\n"
            "if sys.argv[-2:] == ['config','--quiet']: sys.exit(0)\n"
            "print('workflow-output')\nsys.exit(7)\n"
        )
        result = self.wrapper("ps")
        self.assertEqual(result.returncode, 7)
        self.assertEqual(result.stdout, "workflow-output\n")
        self.assertIn("telemetry: event not recorded", result.stderr)
        self.assertNotIn("fixture-secret-never-output", result.stderr)

    def test_missing_or_invalid_metadata(self):
        path = self.work / ".task/runtime.json"
        path.unlink()
        self.rejected(self.wrapper("ps"), "missing or invalid")
        path.write_text("{")
        self.rejected(self.wrapper("ps"), "missing or invalid")

    def test_schema_rejects_unknown_fields_types_and_ports(self):
        original = self.metadata.copy()
        for key, value in [
            ("extra", "x"),
            ("version", True),
            ("app_port", True),
            ("app_port", 80),
            ("mcp_port", 21000),
            ("web_port", 8444),
            ("task_id", "../other"),
            ("version", 2),
        ]:
            with self.subTest(key=key, value=value):
                self.metadata = dict(original, **{key: value})
                self.write_metadata()
                self.assertNotEqual(self.wrapper("ps").returncode, 0)

    def test_branch_mismatch(self):
        self.metadata["task_id"] = "issue-78-other"
        self.write_metadata()
        self.rejected(self.wrapper("ps"), "must match the current branch")

    def test_primary_rejected(self):
        self.rejected(self.wrapper("ps", cwd=self.primary), "administration-only")

    def test_missing_external_secret(self):
        self.secret.unlink()
        self.rejected(self.wrapper("ps"), "development secret file is missing")

    def test_required_development_password(self):
        for contents in ("", "MCP_USERS=\n", "POSTGRES_PASSWORD=\n", 'POSTGRES_PASSWORD=""\n'):
            self.secret.write_text(contents)
            self.rejected(self.wrapper("--check"), "POSTGRES_PASSWORD is required")

    def test_duplicate_metadata(self):
        (self.work / ".task/runtime.json").write_text('{"version":1,"version":2}')
        self.rejected(self.wrapper("ps"), "duplicate fields")

    def test_external_file_cannot_select_runtime(self):
        self.secret.write_text("COMPOSE_FILE=fixture-secret-never-output\n")
        self.rejected(self.wrapper("ps"), "unsupported setting")

    def test_local_secret_rejected(self):
        (self.work / ".env").touch()
        self.rejected(self.wrapper("ps"), "must not contain")

    def test_secret_must_be_external(self):
        self.env["XDG_CONFIG_HOME"] = str(self.work / "config")
        self.rejected(self.wrapper("ps"), "outside the worktree")

    def test_secret_cannot_live_in_primary(self):
        self.env["XDG_CONFIG_HOME"] = str(self.primary / "config")
        self.rejected(self.wrapper("ps"), "outside the worktree")

    def test_compose_validation_error_does_not_print_secrets(self):
        docker = self.bin / "docker"
        docker.write_text("#!/bin/sh\necho fixture-secret-never-output >&2\nexit 1\n")
        self.rejected(self.wrapper("ps"), "Compose validation failed")

    def test_conflicting_environment(self):
        for key in (
            "IMAGE_NAME",
            "TAG",
            "APP_PORT",
            "COMPOSE_FILE",
            "COMPOSE_PROJECT_NAME",
            "COMPOSE_PROFILES",
            "POSTGRES_PASSWORD",
            "MCP_PUBLIC_URL",
        ):
            with self.subTest(key=key):
                self.env[key] = "fixture-secret-never-output"
                self.rejected(self.wrapper("ps"), key)
                del self.env[key]

    def test_matching_hook_git_dir_is_accepted_but_sibling_is_not(self):
        self.env["GIT_DIR"] = self.call(["git", "rev-parse", "--absolute-git-dir"]).stdout.strip()
        self.assertEqual(self.wrapper("--check").returncode, 0)
        self.env["GIT_DIR"] = str(self.primary / ".git")
        self.rejected(self.wrapper("--check"), "conflicting inherited GIT_DIR")

    def test_global_overrides_and_secret_config_are_rejected(self):
        for args in [
            ("-p", "other", "down"),
            ("--env-file", "other", "up"),
            ("config",),
            ("config", "--environment"),
        ]:
            self.assertNotEqual(self.wrapper(*args).returncode, 0)
        self.assertEqual(self.wrapper("config", "--quiet").returncode, 0)

    def test_url_and_check(self):
        self.assertEqual(self.wrapper("--url", "mcp").stdout.strip(), "http://localhost:21001")
        self.assertEqual(self.wrapper("--check").returncode, 0)

    def test_occupied_port(self):
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            self.metadata["app_port"] = listener.getsockname()[1]
            self.write_metadata()
            self.rejected(self.wrapper("up", "-d"), "is occupied")

    def install(self):
        return self.call(["python3", str(SCRIPTS / "install-hooks.py")], check=False)

    def test_unknown_custom_hook_is_preserved(self):
        hook = self.primary / ".git/hooks/pre-push"
        hook.write_text("custom user hook\n")
        self.rejected(self.install(), "refusing to overwrite custom hook")
        self.assertEqual(hook.read_text(), "custom user hook\n")
        self.assertFalse((hook.parent / "pre-commit").exists())

    def test_custom_hooks_path_is_rejected(self):
        self.call(["git", "config", "core.hooksPath", str(self.base / "custom")])
        self.rejected(self.install(), "core.hooksPath")

    def test_legacy_hooks_idempotence_and_worktree_dispatch(self):
        hooks = self.primary / ".git/hooks"
        for name in ("pre-commit", "pre-push"):
            (hooks / name).symlink_to(f"../../scripts/{name}.sh")
        self.assertEqual(self.install().returncode, 0)
        self.assertEqual(self.install().returncode, 0)
        other = self.base / "other"
        self.call(["git", "worktree", "add", "-q", "-b", "issue-78-other", str(other)])
        (other / "scripts").mkdir()
        for work, marker in [(self.work, "A"), (other, "B")]:
            (work / "scripts/pre-push.sh").write_text(f'printf "{marker}:%s:" "$1"; cat; exit 7\n')
        for work, marker in [(self.work, "A"), (other, "B")]:
            result = self.call(
                [str(hooks / "pre-push"), "argument"],
                cwd=work,
                check=False,
                input="stdin preserved",
            )
            self.assertEqual(result.returncode, 7)
            self.assertEqual(result.stdout, f"{marker}:argument:stdin preserved")
        (self.work / "scripts/pre-push.sh").write_text("echo changed-A; exit 9\n")
        result = self.call(
            [str(hooks / "pre-push"), "again"], cwd=other, check=False, input="still B"
        )
        self.assertEqual(result.returncode, 7)
        self.assertEqual(result.stdout, "B:again:still B")


if __name__ == "__main__":
    unittest.main(verbosity=2)
