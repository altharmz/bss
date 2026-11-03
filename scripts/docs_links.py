"""Check Markdown links and the commands the agent-facing docs name. Standard library only.

Everywhere: every relative link must point to an existing file, and a #anchor must match a
heading of that file (GitHub's slug rules). In the agent-facing files (CLAUDE.md, AGENTS.md,
.claude/commands/, docs/worktrees.md, docs/board.md, docs/telemetry-usage.md,
docs/decisions/harness.md): a `scripts/<file>` named in code must exist, and a
`make <target>` named in code must be a target of the Makefile. docs/archive/ is history and
is skipped; generated docs/api and docs/db are not read.
"""

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKIPPED = ("docs/archive/", "docs/api/", "docs/db/")
COMMAND_DOCS = (
    "CLAUDE.md",
    "AGENTS.md",
    ".claude/commands/",
    "docs/worktrees.md",
    "docs/board.md",
    "docs/telemetry-usage.md",
    "docs/decisions/harness.md",
)
FENCE = re.compile(r"^\s*(```|~~~)")
LINK = re.compile(r"(?<!\!)\[[^\]]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
CODE = re.compile(r"`([^`\n]+)`")


def markdown_files(root):
    names = ["CLAUDE.md", "AGENTS.md", "README.md"]
    files = [root / n for n in names if (root / n).is_file()]
    for pattern in ("docs/**/*.md", ".claude/commands/*.md"):
        files += sorted(root.glob(pattern))
    return [f for f in files if not str(f.relative_to(root)).startswith(SKIPPED)]


def prose_lines(path):
    """(line number, text) outside fenced code blocks."""
    fenced = False
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if FENCE.match(line):
            fenced = not fenced
        elif not fenced:
            yield number, line


def slug(heading):
    text = re.sub(r"`([^`]*)`", r"\1", heading)
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"[*_]{1,2}([^*_]+)[*_]{1,2}", r"\1", text).strip().lower()
    return re.sub(r"[^\w\- ]", "", text).replace(" ", "-")


def anchors(path, cache={}):  # noqa: B006 - a per-process cache
    if path not in cache:
        seen, found = {}, set()
        for _number, line in prose_lines(path):
            match = re.match(r"^#{1,6}\s+(.*?)\s*#*\s*$", line)
            if match:
                base = slug(match.group(1))
                count = seen.get(base, 0)
                seen[base] = count + 1
                found.add(base if count == 0 else f"{base}-{count}")
        cache[path] = found
    return cache[path]


def make_targets(root):
    targets = set()
    for line in (root / "Makefile").read_text(encoding="utf-8").splitlines():
        if line.startswith(".PHONY:"):
            targets.update(line.split(":", 1)[1].split())
            continue
        match = re.match(r"^([A-Za-z0-9_.% -]+?)\s*:(?!=)", line)
        if match and not line.startswith(("\t", " ", "#")):
            targets.update(t for t in match.group(1).split() if not t.startswith((".", "%")))
    return targets


def check(root=ROOT):
    root = Path(root)
    problems = []
    targets = make_targets(root)
    for path in markdown_files(root):
        rel = str(path.relative_to(root))
        commands = rel.startswith(COMMAND_DOCS)
        for number, line in prose_lines(path):
            for target in LINK.findall(line):
                if re.match(r"[a-z][a-z0-9+.-]*:", target):
                    continue
                name, _, fragment = target.partition("#")
                destination = (path.parent / name).resolve() if name else path
                if not destination.exists():
                    problems.append(f"{rel}:{number}: broken link {target}")
                elif fragment and destination.suffix == ".md":
                    if fragment not in anchors(destination):
                        problems.append(f"{rel}:{number}: no heading for anchor {target}")
            if not commands:
                continue
            for code in CODE.findall(line):
                for script in re.findall(r"(?<![\w/.-])scripts/([\w./-]+\.(?:py|sh))", code):
                    if not (root / "scripts" / script).exists():
                        problems.append(f"{rel}:{number}: scripts/{script} does not exist")
                for target in re.findall(r"\bmake\s+([a-z][a-z0-9-]*)\b", code):
                    if target not in targets:
                        problems.append(f"{rel}:{number}: make {target} is not a Makefile target")
    return problems


def main():
    problems = check()
    for problem in problems:
        print(problem)
    if problems:
        print(f"docs-links: {len(problems)} problem(s)", file=sys.stderr)
        return 1
    print("docs-links: links and named commands are consistent")
    return 0


if __name__ == "__main__":
    sys.exit(main())
