"""Make-owned lint/format for selected Python harness sources in the pinned image."""

import json
import os
import re
import subprocess
import sys
from pathlib import Path

from task_quality import changed_paths


def select(root, raw=None):
    explicit = bool(raw)
    if explicit:
        names = json.loads(raw)
        if not isinstance(names, list) or not names or len(names) > 128:
            raise ValueError("SCRIPT_FILES must be a nonempty JSON list of at most 128 paths")
    else:
        changed = changed_paths(root)
        names = (
            sorted(changed)
            if changed is not None
            else [p.relative_to(root).as_posix() for p in (root / "scripts").rglob("*.py")]
        )
        names = [
            n
            for n in names
            if n.startswith("scripts/") and n.endswith(".py") and (root / n).exists()
        ]
    selected = []
    for name in names:
        if not isinstance(name, str) or not re.fullmatch(
            r"scripts/(?:[A-Za-z0-9_-]+/)*[A-Za-z0-9_-]+\.py", name
        ):
            raise ValueError("only Python source paths below scripts/ are supported")
        path = root / name
        if (
            not path.is_file()
            or path.is_symlink()
            or not path.resolve().is_relative_to((root / "scripts").resolve())
        ):
            raise ValueError("missing or redirected Python harness source")
        selected.append("/app/" + name)
    return sorted(set(selected))


def run(root, operation, files):
    if operation not in {"lint", "format"}:
        raise ValueError("unsupported harness quality operation")
    if not files:
        print("scripts quality: no changed Python harness sources")
        return 0
    base = [str(root / "scripts/task-compose"), "run", "--rm", "--no-deps"]
    base += ["scripts-tools-fmt" if operation == "format" else "scripts-tools", "ruff"]
    stages = (
        [["check"], ["format", "--check"]]
        if operation == "lint"
        else [["format"], ["check", "--fix"]]
    )
    result = 0
    for stage in stages:
        completed = subprocess.run(  # noqa: S603 - validated source argv, no shell
            [*base, *stage, "--config", "/app/pyproject.toml", *files], cwd=root, check=False
        )
        result = completed.returncode or result
    return result


def main():
    try:
        root = Path.cwd().resolve()
        return run(root, sys.argv[1], select(root, os.environ.get("BSS_SCRIPT_FILES")))
    except (OSError, ValueError, TypeError, IndexError):
        print("scripts quality: invalid selection or unavailable tooling", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
