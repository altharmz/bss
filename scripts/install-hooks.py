#!/usr/bin/env python3
"""Install shared dispatchers without replacing custom user hooks."""

import os
import subprocess
import sys
import tempfile
from pathlib import Path


def dispatcher(name):
    return (
        "#!/usr/bin/env bash\n"
        "# bss worktree hook dispatcher v1\n"
        "set -euo pipefail\n"
        'root="$(git rev-parse --show-toplevel)" || exit 1\n'
        f'exec bash "$root/scripts/{name}.sh" "$@"\n'
    )


def main():
    configured = subprocess.run(
        ["git", "config", "--get", "core.hooksPath"],  # noqa: S607 - fixed Git query
        capture_output=True,
        text=True,
        check=False,
    )
    if configured.returncode != 1:
        sys.exit(
            "hooks: core.hooksPath is configured or unreadable; resolve it before installation"
        )
    common = subprocess.check_output(
        ["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],  # noqa: S607
        text=True,
    ).strip()
    hooks = Path(common) / "hooks"
    names = ("pre-commit", "pre-push")
    # Inspect both before changing either. Only the exact old repository symlink or
    # exact dispatcher is managed; arbitrary scripts with a similar header are not.
    for name in names:
        path = hooks / name
        legacy = path.is_symlink() and os.readlink(path) == f"../../scripts/{name}.sh"
        managed = (
            path.is_file()
            and not path.is_symlink()
            and path.read_bytes() == dispatcher(name).encode()
        )
        if os.path.lexists(path) and not legacy and not managed:
            sys.exit(f"hooks: refusing to overwrite custom hook: {path}")
    hooks.mkdir(parents=True, exist_ok=True)
    for name in names:
        with tempfile.NamedTemporaryFile(mode="w", dir=hooks, delete=False) as output:
            output.write(dispatcher(name))
            temp = Path(output.name)
        temp.chmod(0o755)
        temp.replace(hooks / name)
    print("installed shared worktree-aware pre-commit and pre-push dispatchers")


if __name__ == "__main__":
    main()
