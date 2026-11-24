"""The GitHub project board: reading it in board order, item status and adding an issue."""

import json
import os
import time

from task_repo import StartupError, run

PROJECT = "PVT_kwHOEpM-b84BpHA0"
STATUS_FIELD = "PVTSSF_lAHOEpM-b84BpHA0zhnkE7M"
READY, DOING, DONE = "abe5d139", "47fc9ee4", "98236657"
INBOX, WAITING = "f75ad846", "98f1210e"
# The single-select Priority field is the one source of priority (no P0-P3 labels).
PRIORITY_FIELD = "PVTSSF_lAHOEpM-b84BpHA0zhrP5BI"
PRIORITIES = {"P0": "0c8ba284", "P1": "08fef3c3", "P2": "fd42a13c", "P3": "1df9b3bf"}
# POSITION is the board's manual order, not creation time or issue number.
BOARD_QUERY = """
query($project: ID!, $cursor: String) {
  node(id: $project) { ... on ProjectV2 {
    views(first: 2) { totalCount nodes {
      layout filter sortByFields(first: 1) { totalCount }
    } }
    items(first: 100, after: $cursor, orderBy: {field: POSITION, direction: ASC}) {
      totalCount pageInfo { hasNextPage endCursor }
      nodes { id fieldValueByName(name: "Status") {
        ... on ProjectV2ItemFieldSingleSelectValue { name }
      } priority: fieldValueByName(name: "Priority") {
        ... on ProjectV2ItemFieldSingleSelectValue { name }
      } content { ... on Issue {
        id number title state repository { nameWithOwner }
        labels(first: 100) { totalCount nodes { name } }
        blockedBy(first: 100) { totalCount nodes { state } }
        linkedBranches(first: 100) { totalCount nodes { ref { name } } }
        closedByPullRequestsReferences(first: 100, includeClosedPrs: false) {
          totalCount nodes { state }
        }
      } } }
    }
  } }
}
"""


class PaginationChanged(StartupError):
    """The board's item count changed between two pages of one read."""


def query(document, **variables):
    args = ["gh", "api", "graphql", "-f", f"query={document}"]
    for key, value in variables.items():
        if value is not None:
            args.extend(["-f", f"{key}={value}"])
    try:
        # One retry after a short pause: every GraphQL call here is a read or an idempotent
        # mutation (status, position, add to board), so repeating it is safe.
        try:
            output = run(*args).stdout
        except StartupError:
            time.sleep(float(os.environ.get("BSS_GH_RETRY_PAUSE", "2")))
            output = run(*args).stdout
        data = json.loads(output)
        if data.get("errors"):
            raise ValueError
        return data["data"]
    except (ValueError, KeyError, TypeError):
        raise StartupError(
            "invalid or incomplete GitHub response; check repository/project access"
        ) from None


def pages(document, connection, **variables):
    cursor, seen, expected, rows = None, set(), None, []
    while True:
        response = query(document, cursor=cursor, **variables)
        page = connection(response)
        if expected is None:
            expected = page["totalCount"]
        if page["totalCount"] != expected:
            raise PaginationChanged("GitHub state changed during pagination; retry")
        rows.extend(page["nodes"])
        if not page["pageInfo"]["hasNextPage"]:
            if len(rows) != expected:
                raise StartupError("incomplete GitHub pagination; refusing task selection")
            return rows
        cursor = page["pageInfo"]["endCursor"]
        if not cursor or cursor in seen:
            raise StartupError("ambiguous GitHub pagination")
        seen.add(cursor)


def board_page(response):
    project = response["node"]
    views = project["views"]
    if views["totalCount"] != 1 or len(views["nodes"]) != 1:
        raise StartupError("ambiguous board order: policy requires one canonical board view")
    view = views["nodes"][0]
    if view["layout"] != "BOARD_LAYOUT" or view["filter"] or view["sortByFields"]["totalCount"]:
        raise StartupError("ambiguous board order: filtered/sorted views need an explicit policy")
    return project["items"]


def nodes(connection):
    if connection["totalCount"] != len(connection["nodes"]):
        raise StartupError("incomplete issue relationships; refusing task selection")
    return connection["nodes"]


def snapshot():
    return pages(BOARD_QUERY, board_page, project=PROJECT)


def settled_snapshot(tries=3):
    """A board read for checking our own just-made change, not for selecting work: a
    concurrent addition (e.g. a late board automation) is retried, not fatal. Task
    selection keeps snapshot(), which refuses a board that changes while it reads."""
    for attempt in range(tries):
        try:
            return snapshot()
        except PaginationChanged:
            if attempt == tries - 1:
                raise
            time.sleep(float(os.environ.get("BSS_GH_RETRY_PAUSE", "2")))
    raise AssertionError("unreachable")


def ownership(item):
    return {n["name"] for n in nodes(item["content"]["labels"])}


def status(item):
    return (item.get("fieldValueByName") or {}).get("name")


def priority(item):
    """The item's Priority field value (P0-P3), or None when it is empty."""
    name = (item.get("priority") or {}).get("name")
    if name is not None and name not in PRIORITIES:
        number = (item.get("content") or {}).get("number")
        raise StartupError(f"#{number}: unknown Priority field value {name!r}")
    return name


def set_status(issue, option):
    set_field(issue, STATUS_FIELD, option)


def write_priority(issue, name):
    set_field(issue, PRIORITY_FIELD, PRIORITIES[name])


def set_field(issue, field, option):
    query(
        """mutation($project: ID!, $item: ID!, $field: ID!, $option: String!) {
          updateProjectV2ItemFieldValue(input: {projectId: $project, itemId: $item,
            fieldId: $field, value: {singleSelectOptionId: $option}}) {
            projectV2Item { id }
          }
        }""",
        project=PROJECT,
        item=issue["item_id"],
        field=field,
        option=option,
    )


def board_item(number):
    matches = [item for item in snapshot() if (item.get("content") or {}).get("number") == number]
    if len(matches) > 1:
        raise StartupError("new issue has ambiguous project items")
    return matches[0] if matches else None


def wait_for_board_item(number, seconds):
    """Poll for the issue's board item; the project's own automation adds it, usually in seconds."""
    for attempt in range(int(seconds) + 1):
        try:
            created = board_item(number)
        except PaginationChanged:
            # The board has more than one page, and the automation this loop waits for
            # added the item between two of them: the item is arriving, poll again.
            created = None
        if created is not None:
            return created
        if attempt < int(seconds):
            time.sleep(1)
    return None


def add_to_board(number):
    """Fallback when the project's auto-add did not run (adding twice returns the same item)."""
    issue = query(
        """query($number: Int!) { repository(owner: "altharmz", name: "bss") {
          issue(number: $number) { id }
        } }""",
        number=number,
    )["repository"]["issue"]
    query(
        """mutation($project: ID!, $content: ID!) {
          addProjectV2ItemById(input: {projectId: $project, contentId: $content}) {
            item { id }
          }
        }""",
        project=PROJECT,
        content=issue["id"],
    )
