"""make_help.py: the grouped `make help` listing (#307).

Usage: make_help.py <makefile> [group]

Every help comment in the Makefile starts with a group tag: `target: ## [group] text`.
With no group it prints the daily groups; with a group name only that group; with `all`
every group. The grouping only changes the listing: it restricts no target.
"""

import re
import sys
from pathlib import Path

GROUPS = {
    "task": "Task workflow",
    "stack": "Local stack",
    "test": "Tests and lint",
    "ui": "Admin UI",
    "docs": "Docs and tracking",
    "mcp": "MCP connector",
    "loadtest": "Load test",
    "telemetry": "Usage telemetry",
    "ops": "Human-only operations (deploy, VPS)",
}
DEFAULT_GROUPS = ("task", "stack", "test", "ui", "docs", "mcp")
FOOTER = (
    "More groups: make help GROUP=loadtest | telemetry | ops | all",
    "Human-only operations: make help-ops",
)
DOCUMENTED = re.compile(r"^([A-Za-z0-9_-]+):.*?## ")
TAGGED = re.compile(r"^([A-Za-z0-9_-]+):.*?## \[([a-z]+)\] (.*)$")
WIDTH = 26


class HelpError(Exception):
    pass


def collect(text):
    """(target, group, description) for each documented target, in Makefile order."""
    entries = []
    for line in text.splitlines():
        documented = DOCUMENTED.match(line)
        if not documented:
            continue
        tagged = TAGGED.match(line)
        if not tagged or tagged.group(2) not in GROUPS:
            raise HelpError(f"make help: {documented.group(1)} has no valid group tag")
        entries.append(tagged.groups())
    return entries


def render(entries, group):
    if group == "":
        names, footer = DEFAULT_GROUPS, FOOTER
    elif group == "all":
        names, footer = tuple(GROUPS), ()
    elif group in GROUPS:
        names, footer = (group,), ()
    else:
        choices = " ".join([*GROUPS, "all"])
        raise HelpError(f"make help: unknown GROUP '{group}'; groups: {choices}")
    blocks = []
    for name in names:
        lines = [GROUPS[name]]
        lines += [f"  {t:<{WIDTH - 1}} {d}" for t, g, d in entries if g == name]
        blocks.append("\n".join(lines))
    if footer:
        blocks.append("\n".join(footer))
    return "\n\n".join(blocks) + "\n"


def main(argv):
    if len(argv) not in (2, 3):
        print("usage: make_help.py <makefile> [group]", file=sys.stderr)
        return 2
    group = argv[2].strip() if len(argv) == 3 else ""
    try:
        output = render(collect(Path(argv[1]).read_text()), group)
    except HelpError as error:
        print(error, file=sys.stderr)
        return 1
    sys.stdout.write(output)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
