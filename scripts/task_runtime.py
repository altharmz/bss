"""Validate local runtime contexts before invoking Docker Compose."""

import errno
import json
import os
import re
import socket
import subprocess
import sys
import time
from pathlib import Path

from task_telemetry import record

RESERVED_PORTS = frozenset({443, 500, 992, 1194, 4500, 5555, 8444})


class InvalidRuntime(Exception):
    pass


def gh_detail(stderr):
    """One bounded, printable line of gh's error, without URLs or token-like words."""
    line = next((s.strip() for s in (stderr or "").splitlines() if s.strip()), "")
    line = re.sub(r"https?://[^\s)]+", "<url>", line)
    line = re.sub(r"\b(gh[pousr]_|github_pat_)\w+|(?i:bearer|token)\s+\S+", "<redacted>", line)
    return "".join(c for c in line if " " <= c <= "~")[:120].strip()


REEXEC_MARKER = "BSS_SCRIPTS_REEXEC"


class Reexec(Exception):
    """Primary scripts changed on master; unwind (releasing locks), then run the target again."""

    def __init__(self, root):
        super().__init__("harness scripts changed on master")
        self.root = Path(root)


def reexec_if_scripts_changed(root, before):
    """Raise Reexec once when the fast-forward from `before` changed scripts/ or the Makefile."""
    if os.environ.get(REEXEC_MARKER):
        return
    # Git hooks export repository selectors that would redirect `-C`; this reads `root` only.
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_") or k == "GIT_PAGER"}
    changed = subprocess.run(  # noqa: S603 - fixed read-only Git command
        [  # noqa: S607
            "git",
            "-C",
            str(root),
            "diff",
            "--name-only",
            before,
            "HEAD",
            "--",
            "scripts",
            "Makefile",
        ],
        capture_output=True,
        text=True,
        check=True,
        env=env,
    ).stdout
    if changed.strip():
        raise Reexec(root)


def rerun(root, script, *arguments):
    """Run the updated primary script once more; the marker prevents a second hand-over."""
    print("harness scripts changed on master; continuing with the updated scripts", flush=True)
    return subprocess.run(  # noqa: S603 - fixed interpreter, primary's own script
        [sys.executable, "-B", str(Path(root) / "scripts" / script), *arguments],
        cwd=root,
        env={**os.environ, REEXEC_MARKER: "1"},
        check=False,
    ).returncode


def printable(value, limit=32):
    text = re.sub(r"https?://\S+", "<url>", str(value))
    return "".join(c for c in text if " " <= c <= "~")[:limit]


def diagnostic(error):
    """Bounded cause for a generic failure message, safe to print.

    Names the exception class, a failing command's first two arguments and exit status, and
    for gh the sanitized first stderr line. Never stderr text of other tools or exception
    messages, which can carry tokens, URLs or file contents.
    """
    parts = [type(error).__name__]
    argv = getattr(error, "cmd", None)
    if isinstance(argv, (list, tuple)) and argv:
        # `git -C <path> <subcommand>`: name the subcommand, not the directory.
        shown = [*argv[:1], *argv[3:4]] if list(argv[1:2]) == ["-C"] else list(argv[:2])
        parts.append("command " + " ".join(printable(a) for a in shown))
        if isinstance(getattr(error, "returncode", None), int):
            parts.append(f"exit {error.returncode}")
        if argv[0] == "gh":
            stderr = getattr(error, "stderr", None)
            if isinstance(stderr, bytes):
                stderr = stderr.decode(errors="replace")
            detail = gh_detail(stderr)
            if detail:
                parts.append(detail)
    if isinstance(error, subprocess.TimeoutExpired):
        parts.append("timed out")
    if isinstance(error, OSError) and error.errno:
        parts.append(errno.errorcode.get(error.errno, "errno " + str(error.errno)))
    return "; ".join(parts)


def unique_fields(pairs):
    data = {}
    for key, value in pairs:
        if key in data:
            raise InvalidRuntime("runtime metadata contains duplicate fields")
        data[key] = value
    return data


def git(*args):
    # All Git arguments are fixed queries supplied by this module, never shell text.
    # Discover from cwd rather than letting a selector redirect us to a sibling.
    env = {
        key: value
        for key, value in os.environ.items()
        if key not in {"GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR"}
    }
    result = subprocess.run(  # noqa: S603
        ["git", *args],  # noqa: S607 - fixed Git query
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    if result.returncode:
        raise InvalidRuntime("run inside a Git task worktree")
    return result.stdout.strip()


def validate_metadata(data, branch=None):
    """Validate identity/ports only, without Git, Docker or development secrets."""
    keys = {"version", "task_id", "app_port", "mcp_port", "web_port"}
    if not isinstance(data, dict) or set(data) != keys:
        raise InvalidRuntime(
            "runtime metadata must contain exactly version, task_id and three ports"
        )
    if type(data["version"]) is not int or data["version"] != 1:
        raise InvalidRuntime("unsupported runtime metadata version (expected 1)")
    task = data["task_id"]
    if (
        not isinstance(task, str)
        or len(task) > 80
        or not re.fullmatch(r"issue-[1-9][0-9]*-[a-z0-9]+(?:-[a-z0-9]+)*", task)
    ):
        raise InvalidRuntime(
            "task_id must be issue-<number>-<lowercase-kebab-slug>, at most 80 characters"
        )
    if branch is not None and branch != task:
        raise InvalidRuntime("task_id must match the current branch; do not switch task branches")
    ports = [data[key] for key in ("app_port", "mcp_port", "web_port")]
    if any(type(port) is not int or not 1024 <= port <= 65535 for port in ports):
        raise InvalidRuntime("ports must be integers from 1024 through 65535")
    if len(set(ports)) != 3 or set(ports) & RESERVED_PORTS:
        raise InvalidRuntime("ports must be distinct and must not use reserved VPN ports")
    return task, ports


def validate_inherited(values, root, own, common):
    for key, value in os.environ.items():
        expected_git = {"GIT_DIR": own, "GIT_WORK_TREE": root, "GIT_COMMON_DIR": common}
        if key in expected_git and Path(value).resolve() != expected_git[key]:
            raise InvalidRuntime(f"conflicting inherited {key}; unset it")
        if key in {"DOCKER_HOST", "DOCKER_CONTEXT"}:
            raise InvalidRuntime(f"inherited {key} is not supported; unset it")
        if key in values and value != values[key]:
            raise InvalidRuntime(f"conflicting inherited {key}; unset it")
        if key.startswith("COMPOSE_") and key not in values:
            raise InvalidRuntime(f"inherited {key} is not supported; unset it")
        if key in {
            "POSTGRES_PASSWORD",
            "POSTGRES_USER",
            "POSTGRES_DB",
            "BSS_SERVICE_SECRET",
            "BSS_SMOKE_SECRET",
            "APP_ENV",
            "DATABASE_URL",
        }:
            raise InvalidRuntime(
                f"inherited {key} is not supported; use the external development file"
            )


def deploy_web_build(args):
    if len(args) != 1 or not args[0]:
        raise InvalidRuntime("--deploy-web-build requires exactly one nonempty build tag")
    root = Path(git("rev-parse", "--show-toplevel")).resolve()
    common = Path(git("rev-parse", "--path-format=absolute", "--git-common-dir")).resolve()
    own = Path(git("rev-parse", "--absolute-git-dir")).resolve()
    if own != common:
        raise InvalidRuntime("--deploy-web-build is allowed only from the primary checkout")
    values = {
        "COMPOSE_PROJECT_NAME": "bss",
        "IMAGE_NAME": "bss-app",
        "WEB_IMAGE_NAME": "bss-web",
        "TAG": "dev",
        "APP_PORT": "18080",
        "MCP_PORT": "18081",
        "WEB_PORT": "15173",
        "MCP_PUBLIC_URL": "http://localhost:18081",
    }
    validate_inherited(values, root, own, common)
    env = dict(os.environ, **values, POSTGRES_PASSWORD="offline-ui-build-only")  # noqa: S106 - offline build placeholder
    command = [
        "docker",
        "--context",
        "default",
        "compose",
        "--project-directory",
        str(root),
        "--env-file",
        "/dev/null",
        "--project-name",
        values["COMPOSE_PROJECT_NAME"],
        "-f",
        str(root / "compose.yaml"),
        "-f",
        str(root / "compose.override.yaml"),
    ]
    checked = subprocess.run(  # noqa: S603 - fixed quiet validation, no shell
        command + ["config", "--quiet"], env=env, cwd=root, capture_output=True, check=False
    )
    if checked.returncode:
        raise InvalidRuntime("offline UI Compose validation failed")
    result = subprocess.run(  # noqa: S603 - fixed offline build, no shell
        command
        + [
            "run",
            "--rm",
            "--no-deps",
            "-e",
            f"VITE_BSS_BUILD={args[0]}",
            "web-offline",
            "sh",
            "-c",
            "sh scripts/check-deps.sh && npm run build",
        ],
        env=env,
        cwd=root,
        check=False,
    )
    sys.exit(result.returncode)


def context():
    root = Path(git("rev-parse", "--show-toplevel")).resolve()
    common = Path(git("rev-parse", "--path-format=absolute", "--git-common-dir")).resolve()
    own = Path(git("rev-parse", "--absolute-git-dir")).resolve()
    if own == common:
        raise InvalidRuntime("primary checkout is administration-only; use a linked task worktree")
    try:
        data = json.loads(
            (root / ".task/runtime.json").read_text(), object_pairs_hook=unique_fields
        )
    except (OSError, ValueError):
        raise InvalidRuntime("missing or invalid .task/runtime.json") from None
    task, ports = validate_metadata(data, git("symbolic-ref", "--quiet", "--short", "HEAD"))
    config = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    if not config.is_absolute():
        raise InvalidRuntime("XDG_CONFIG_HOME must be absolute")
    secret = (config / "bss/tasks" / task / ".env").resolve()
    worktrees = [
        Path(line[9:]).resolve()
        for line in git("worktree", "list", "--porcelain", "-z").split("\0")
        if line.startswith("worktree ")
    ]
    if any(secret.is_relative_to(worktree) for worktree in worktrees):
        raise InvalidRuntime("task secrets must be outside the worktree and its siblings")
    if not secret.is_file():
        raise InvalidRuntime(f"development secret file is missing: {secret}")
    # Keep this file exclusively for development inputs. In particular, dotenv must
    # not supply COMPOSE_FILE, COMPOSE_PROFILES or another runtime selector.
    try:
        lines = secret.read_text().splitlines()
    except (OSError, UnicodeError):
        raise InvalidRuntime("cannot read external development secret file") from None
    allowed_secrets = {
        "POSTGRES_PASSWORD",
        "POSTGRES_USER",
        "POSTGRES_DB",
        "BSS_SERVICE_SECRET",
    }
    seen = set()
    for line in lines:
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        match = re.match(r"^\s*([A-Z_]+)\s*=", line)
        if not match or match[1] not in allowed_secrets or match[1] in seen:
            raise InvalidRuntime(
                "external development file contains an unsupported setting or format"
            )
        seen.add(match[1])
        if match[1] == "POSTGRES_PASSWORD" and not line.split("=", 1)[1].strip().strip("\"'"):
            raise InvalidRuntime("POSTGRES_PASSWORD is required in the external development file")
    if "POSTGRES_PASSWORD" not in seen:
        raise InvalidRuntime("POSTGRES_PASSWORD is required in the external development file")
    for name in (".env", ".env.prod", ".env.smoke"):
        if (root / name).exists():
            raise InvalidRuntime("task worktrees must not contain .env, .env.prod or .env.smoke")
    values = {
        "COMPOSE_PROJECT_NAME": f"bss-{task}",
        "IMAGE_NAME": f"bss-{task}-app",
        "WEB_IMAGE_NAME": f"bss-{task}-web",
        "TAG": "dev",
        "APP_PORT": str(ports[0]),
        "MCP_PORT": str(ports[1]),
        "WEB_PORT": str(ports[2]),
        "MCP_PUBLIC_URL": f"http://localhost:{ports[1]}",
    }
    for key, value in os.environ.items():
        expected_git = {"GIT_DIR": own, "GIT_WORK_TREE": root, "GIT_COMMON_DIR": common}
        if key in expected_git and Path(value).resolve() != expected_git[key]:
            raise InvalidRuntime(f"conflicting inherited {key}; unset it")
        if key in {"DOCKER_HOST", "DOCKER_CONTEXT"}:
            raise InvalidRuntime(f"inherited {key} is not supported; unset it")
        if key in values and value != values[key]:
            raise InvalidRuntime(f"conflicting inherited {key}; unset it")
        if key.startswith("COMPOSE_") and key not in values:
            raise InvalidRuntime(f"inherited {key} is not supported; unset it")
        if key in {
            "POSTGRES_PASSWORD",
            "POSTGRES_USER",
            "POSTGRES_DB",
            "BSS_SERVICE_SECRET",
            "BSS_SMOKE_SECRET",
            "APP_ENV",
            "DATABASE_URL",
        }:
            raise InvalidRuntime(
                f"inherited {key} is not supported; use the external development file"
            )
    return root, secret, values


def compose_args(args):
    # Global Compose options must not override our files, directory, env-file or project.
    profiles = []
    while args[:1] == ["--profile"] and len(args) >= 2:
        profiles.extend(args[:2])
        args = args[2:]
    allowed = {
        "up",
        "down",
        "build",
        "run",
        "exec",
        "logs",
        "ps",
        "port",
        "rm",
        "stop",
        "start",
        "restart",
        "config",
    }
    if not args or args[0] not in allowed:
        raise InvalidRuntime(
            "expected a supported Compose command (global overrides are forbidden)"
        )
    if args[0] == "config" and args[1:] not in (["--quiet"], ["-q"]):
        raise InvalidRuntime(
            "only config --quiet is allowed; resolved configuration contains secrets"
        )
    return profiles, args


def check_ports(command, env, cwd, values):
    # Only the current project's running containers may already own these exact ports.
    result = subprocess.run(  # noqa: S603 - validated Compose argv, no shell
        command + ["ps", "--format", "json"],
        env=env,
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode:
        raise InvalidRuntime("cannot inspect task containers before startup")
    try:
        raw = result.stdout.strip()
        rows = json.loads(raw) if raw.startswith("[") else [json.loads(s) for s in raw.splitlines()]
        owned = {
            p["PublishedPort"]
            for row in rows
            for p in (row.get("Publishers") or [])
            if p.get("URL") == "127.0.0.1" and p.get("Protocol") == "tcp"
        }
    except (ValueError, KeyError, TypeError):
        raise InvalidRuntime("cannot parse task container port ownership") from None
    for key in ("APP_PORT", "MCP_PORT", "WEB_PORT"):
        port = int(values[key])
        if port in owned:
            continue
        with socket.socket() as listener:
            try:
                listener.bind(("127.0.0.1", port))
            except OSError:
                raise InvalidRuntime(
                    f"{key} {port} is occupied; choose an explicit free port"
                ) from None


def main():
    try:
        args = sys.argv[1:]
        if args[:1] == ["--deploy-web-build"]:
            deploy_web_build(args[1:])
        root, secret, values = context()
        if args == ["--check"]:
            return
        if len(args) == 2 and args[0] == "--url" and args[1] in {"app", "mcp", "web"}:
            port = values[f"{args[1].upper()}_PORT"]
            print(f"http://localhost:{port}")
            return
        profiles, args = compose_args(args)
        env = dict(os.environ, **values)
        # --env-file prevents discovery of checkout .env files. Explicit identity values
        # in the process environment take precedence over anything in that file.
        command = [
            "docker",
            "--context",
            "default",
            "compose",
            "--project-directory",
            str(root),
            "--env-file",
            str(secret),
            "--project-name",
            values["COMPOSE_PROJECT_NAME"],
            "-f",
            str(root / "compose.yaml"),
            "-f",
            str(root / "compose.override.yaml"),
        ]
        checked = subprocess.run(  # noqa: S603 - fixed quiet validation, no shell
            command + ["config", "--quiet"], env=env, cwd=root, capture_output=True, check=False
        )
        if checked.returncode:
            raise InvalidRuntime(
                "local Compose validation failed; check configuration and development secrets"
            )
        if args[0] in {"up", "start", "restart", "run"}:
            check_ports(command, env, root, values)
        os.chdir(root)
        started = time.monotonic()
        result = subprocess.run(command + profiles + args, env=env, check=False)  # noqa: S603
        record(
            values["COMPOSE_PROJECT_NAME"].removeprefix("bss-"),
            args[0],
            result.returncode,
            int((time.monotonic() - started) * 1000),
        )
        sys.exit(result.returncode)
    except (InvalidRuntime, OSError) as error:
        print(f"task-compose: {error}", file=sys.stderr)
        sys.exit(1)
