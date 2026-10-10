#!/usr/bin/env python3
"""Prove that slimming CLAUDE.md lost no decision or rule (docs/decisions/README.md).

Takes CLAUDE.md as it was at BASE (the commit before the move) and splits it into blocks:
a paragraph, one list item (its continuation lines included, its sub-items separate), a
table row, a line inside a code fence. Every block with a line that carries a decision id
(D-W.., Q.., a slice or a checkpoint) or a never/must/always rule is tracked whole - the
rule with its rationale. Each one must still appear, word for word, in the current
CLAUDE.md or a file in docs/decisions/ (retired.md included: that is where a block
removed on purpose goes). Compared after collapsing whitespace and unwrapping Markdown
links, so reflowed text and a section reference turned into a link still match; anything
reworded does not.

Also checks that every relative link in CLAUDE.md and docs/decisions/ resolves: the file
exists and, for a Markdown target, the #anchor is one of its headings.

Standard library only; runs on the host (`make decisions-check`, part of `make check`).
Exit 0 = everything found, 1 = something missing or a broken link, 2 = cannot run.
"""

import re
import subprocess
import sys
from pathlib import Path

BASE = "910ec0a"
ROOT = Path(__file__).resolve().parent.parent
DECISIONS = ROOT / "docs" / "decisions"

RULE = re.compile(r"\b(never|must|always)\b", re.IGNORECASE)
DECISION_ID = re.compile(
    r"\bD-W\d+\b"  # stage 4 design decisions
    r"|\bQ\d+(?:-Q\d+)?\b"  # stage 4 planning questions
    r"|\b[Ss]lices? (?:[a-h]\d?|\d(?:-\d)?)\b"  # slice notes (stage 4 a-h, bill lifecycle 1-4)
    r"|\bcheckpoint \d+\b"
)
LIST_MARKER = re.compile(r"^(?:[-*]|\d+\.)\s+")
LINK = re.compile(r"\[([^\[\]]*)\]\(([^()\s]*)\)")


def normalize(text: str) -> str:
    text = " ".join(text.split())
    return LINK.sub(r"\1", text)


def old_claude_md() -> str:
    try:
        return subprocess.run(  # noqa: S603 - fixed argv, no shell
            ["git", "show", f"{BASE}:CLAUDE.md"],  # noqa: S607 - git from PATH, as in make
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError) as exc:
        sys.exit(f"check-decisions: cannot read CLAUDE.md at {BASE} ({exc}); fetch more history")


def blocks(text: str) -> list[tuple[int, list[str]]]:
    """(first line number, raw lines) per paragraph, list item, table row or fenced line."""
    result: list[tuple[int, list[str]]] = []
    current: list[str] = []
    start = 0
    in_fence = False

    def close() -> None:
        nonlocal current
        if current:
            result.append((start, current))
        current = []

    for number, raw in enumerate(text.splitlines(), start=1):
        stripped = raw.strip()
        if stripped.startswith("```"):
            close()
            in_fence = not in_fence
            continue
        single = in_fence or stripped.startswith(("|", "#"))
        if not stripped or single or LIST_MARKER.match(stripped):
            close()
        if not stripped:
            continue
        if not current:
            start = number
        current.append(raw)
        if single:
            close()
    close()
    return result


def tracked_blocks(old: str) -> list[tuple[int, str, list[str]]]:
    """(first line number, normalized block, decision ids) for every block worth tracking."""
    found = []
    for number, lines in blocks(old):
        ids = [i for line in lines for i in DECISION_ID.findall(line)]
        if not ids and not any(RULE.search(line) for line in lines):
            continue
        block = normalize(LIST_MARKER.sub("", " ".join(lines).strip()))
        found.append((number, block, ids))
    return found


def slug(heading: str) -> str:
    """GitHub's anchor for a heading."""
    text = heading.replace("`", "").strip().lower()
    text = re.sub(r"[^\w\- ]", "", text)
    return text.replace(" ", "-")


def anchors(path: Path) -> set[str]:
    seen: dict[str, int] = {}
    result = set()
    in_fence = False
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("```"):
            in_fence = not in_fence
        if in_fence:
            continue
        match = re.match(r"^#{1,6}\s+(.*)$", line)
        if match:
            base = slug(LINK.sub(r"\1", match.group(1)))
            count = seen.get(base, 0)
            seen[base] = count + 1
            result.add(base if count == 0 else f"{base}-{count}")
    return result


def broken_links(files: list[Path]) -> list[str]:
    problems = []
    for source in files:
        text = " ".join(source.read_text(encoding="utf-8").split())
        for _label, target in LINK.findall(text):
            if not target or re.match(r"^[a-z]+:", target):
                continue  # external (https:, mailto:) - not checked here
            path_part, _, anchor = target.partition("#")
            dest = (source.parent / path_part).resolve() if path_part else source
            name = f"{source.relative_to(ROOT)} -> {target}"
            if not dest.exists():
                problems.append(f"{name}: no such file")
            elif anchor and dest.suffix == ".md" and anchor not in anchors(dest):
                problems.append(f"{name}: no heading with that anchor")
    return problems


def main() -> int:
    files = [ROOT / "CLAUDE.md", *sorted(DECISIONS.glob("*.md"))]
    corpus = {f: normalize(f.read_text(encoding="utf-8")) for f in files}
    tracked = tracked_blocks(old_claude_md())

    missing = []
    retired = 0
    ids: set[str] = set()
    for number, block, block_ids in tracked:
        ids.update(block_ids)
        where = [f for f, text in corpus.items() if block in text]
        if not where:
            missing.append((number, block))
        elif where == [DECISIONS / "retired.md"]:
            retired += 1
    missing_ids = sorted(i for i in ids if not any(i in text for text in corpus.values()))

    links = broken_links(files)
    print(
        f"check-decisions: CLAUDE.md@{BASE}: {len(tracked)} tracked blocks"
        f" ({sum(1 for _, _, i in tracked if i)} with a decision id, {len(ids)} distinct ids;"
        f" the rest with a never/must/always rule); searched {len(files)} files"
        " (CLAUDE.md + docs/decisions/)"
    )
    for number, block in missing:
        shown = block if len(block) <= 160 else block[:157] + "..."
        print(f"  MISSING (old line {number}): {shown}", file=sys.stderr)
    for decision_id in missing_ids:
        print(f"  MISSING id: {decision_id}", file=sys.stderr)
    for problem in links:
        print(f"  BROKEN LINK: {problem}", file=sys.stderr)
    if missing or missing_ids or links:
        print(
            f"check-decisions: FAILED - {len(missing)} blocks, {len(missing_ids)} ids missing,"
            f" {len(links)} broken links. Restore the text, or quote the old block in"
            " docs/decisions/retired.md with the reason.",
            file=sys.stderr,
        )
        return 1
    print(
        f"check-decisions: all {len(tracked)} blocks and {len(ids)} ids found"
        f" ({retired} only in retired.md); all relative links resolve"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
