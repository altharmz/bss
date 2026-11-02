"""Publish the two human-requested, content-free issue usage checkpoints."""

# All SQL values and GitHub paths are constructed from bounded task identities.
# ruff: noqa: S608
import argparse
import json
import os
import re
import shutil
import subprocess
import sys

from task_telemetry import literal, sql
from task_usage import REPORT_SQL, TOKENS, report


def github(*args, payload=None):
    executable = shutil.which("gh")
    if executable is None:
        raise OSError("GitHub CLI unavailable")
    result = subprocess.run(  # noqa: S603 - argv and JSON stdin, never a shell
        [executable, "api", *args, *(["--input", "-"] if payload is not None else [])],
        input=json.dumps(payload) if payload is not None else None,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if result.returncode:
        raise RuntimeError("issue statistics publication failed")
    if "--paginate" in args:
        # Older supported gh versions emit adjacent JSON documents and do not
        # implement --slurp. Decode the page stream without shell/jq evaluation.
        decoder, pages, remaining = json.JSONDecoder(), [], result.stdout.strip()
        while remaining:
            page, end = decoder.raw_decode(remaining)
            if not isinstance(page, list):
                raise ValueError("invalid comment page")
            pages.append(page)
            remaining = remaining[end:].lstrip()
        if not pages:
            raise ValueError("missing comment pages")
        return pages
    return json.loads(result.stdout)


def snapshot(task, phase):
    if phase not in {"start", "pr"} or not re.fullmatch(r"issue-[1-9][0-9]*-[a-z0-9-]+", task):
        raise ValueError("invalid statistics checkpoint")
    # The starting checkpoint is immutable across retries and resumed sessions.
    existing = sql(
        "SELECT COALESCE(json_agg(s),'[]') FROM usage_issue_snapshots s "
        f"WHERE task_id={literal(task)} AND phase={literal(phase)};"
    )
    rows = json.loads(existing)
    if phase == "start" and rows:
        return rows[0]["stats"]
    data = report(json.loads(sql(REPORT_SQL)))
    stats = next(t for t in data["tasks"] if t["task_id"] == task)
    # Explicit projection: the stored/published payload contains only usage metadata.
    selected = {
        k: stats[k]
        for k in (
            "task_id",
            "tokens",
            "usage_identities",
            "sample_count",
            "known_cost_by_currency",
            "cost_complete",
            "collection_complete",
            "missing_clients",
            "missing_sessions",
            "collection_status",
            "pricing_provenance",
        )
    }
    if phase == "start":
        # A retry can reconstruct a start comment later. Freeze the actual binding
        # baseline, never relabel the intervening work as the starting counter.
        for row in selected["collection_status"]:
            row["latest_tokens"] = row.get("baseline_tokens")
        selected.update(
            tokens=dict.fromkeys(TOKENS),
            sample_count=0,
            usage_identities=[],
            known_cost_by_currency={},
            cost_complete=False,
            collection_complete=False,
            pricing_provenance=[],
        )
    sql(
        "INSERT INTO usage_issue_snapshots(task_id,phase,stats) VALUES "
        f"({literal(task)},{literal(phase)},{literal(json.dumps(selected))}::jsonb) "
        + (
            "ON CONFLICT(task_id,phase) DO NOTHING;"
            if phase == "start"
            else "ON CONFLICT(task_id,phase) DO UPDATE SET stats=EXCLUDED.stats,captured_at=now();"
        )
    )
    if phase == "start":
        return json.loads(
            sql(
                "SELECT stats FROM usage_issue_snapshots "
                f"WHERE task_id={literal(task)} AND phase='start';"
            )
        )
    return selected


def display(value):
    return "unknown" if value is None else str(value)


def render(stats, phase, pr_number=None):
    title = "Start of work" if phase == "start" else "PR created"
    lines = [
        f"<!-- bss-agent-usage:{stats['task_id']}:{phase} -->",
        f"### Agent usage — {title}",
        "",
        "Machine-observed counters only. Input includes cached input; "
        "costs are rate-card estimates.",
    ]
    if pr_number:
        lines += ["", f"PR: #{pr_number}"]
    for row in stats["collection_status"]:
        client, sid = row["client"], row["external_session_id"]
        lines += [
            "",
            f"**{client} / `{sid}`** — {row['status']} ({row['reason']})",
            f"Binding: {row.get('bound_at', 'unknown')}. Source: {row.get('source', 'unknown')}.",
            "",
            "| Tokens | Start counter | Latest counter | Observed difference |",
            "|---|---:|---:|---:|",
        ]
        baseline, latest = row.get("baseline_tokens") or {}, row.get("latest_tokens") or {}
        for key in TOKENS:
            before, after = baseline.get(key), latest.get(key)
            delta = after - before if before is not None and after is not None else None
            lines.append(f"| {key} | {display(before)} | {display(after)} | {display(delta)} |")
    lines += [
        "",
        "Provider / client / model identities: "
        + (
            "; ".join(
                " / ".join(display(r[k]) for k in ("provider", "client", "model"))
                for r in stats["usage_identities"]
            )
            or "not yet observed"
        ),
        "",
        f"Collection complete: {str(stats['collection_complete']).lower()}. "
        f"Pricing complete: {str(stats['cost_complete']).lower()}.",
        "Known estimated cost: "
        + (", ".join(f"{v} {c}" for c, v in stats["known_cost_by_currency"].items()) or "unknown"),
        "",
        "Counters cover each session from its issue binding. New sessions receive their own "
        "starting counter. Unknown values are not zero. PR creation is a checkpoint; "
        "later review/merge work and the current unfinished response can add usage.",
    ]
    if stats["missing_clients"]:
        lines += ["", "Missing client collection: " + ", ".join(stats["missing_clients"])]
    if stats.get("missing_sessions"):
        lines += [
            "",
            "Session coverage gaps: "
            + "; ".join(
                f"{r['client']} / {r['session_id']}: {r['reason']}"
                for r in stats["missing_sessions"]
            ),
        ]
    if stats.get("pricing_provenance"):
        lines += [
            "",
            "Rate cards: "
            + "; ".join(
                f"{p['version']} ({p['source_url']}, {p['currency']}, "
                f"{p['effective_from']} to {p['effective_until']})"
                for p in stats["pricing_provenance"]
            ),
        ]
    return "\n".join(lines)


def post(task, phase, pr_number=None):
    if os.environ.get("GH_HOST", "github.com") != "github.com":
        raise ValueError("unexpected GitHub host")
    stats = snapshot(task, phase)
    if phase == "pr" and pr_number is None:
        observed = json.loads(
            sql(
                "SELECT COALESCE(json_agg(p),'[]') FROM "
                "(SELECT (metadata->>'pr_number')::bigint AS number "
                f"FROM workflow_events WHERE task_id={literal(task)} "
                "AND event_type='create-pr' AND result=0 "
                "ORDER BY occurred_at DESC LIMIT 1) p;"
            )
        )
        if not observed or type(observed[0].get("number")) is not int:
            raise ValueError("PR creation has not been observed")
        pr_number = observed[0]["number"]
    body = render(stats, phase, pr_number)
    number = int(task.split("-")[1])
    route = f"repos/altharmz/bss/issues/{number}/comments"
    login = github("user")["login"]
    pages = github(route, "--method", "GET", "--paginate")
    marker = body.splitlines()[0]
    matches = [
        r
        for page in pages
        for r in page
        if r.get("user", {}).get("login") == login
        and (r.get("body") or "").startswith(marker + "\n")
    ]
    if len(matches) > 1:
        raise ValueError("ambiguous issue statistics checkpoints")
    if matches:
        comment = matches[0]
        if comment["body"] == body:
            return comment["html_url"]
        if phase == "start":
            raise ValueError("starting statistics comment changed; preserved for inspection")
        cid = comment["id"]
        if type(cid) is not int or cid <= 0:
            raise ValueError("invalid comment identity")
        response = github(
            f"repos/altharmz/bss/issues/comments/{cid}", "--method", "PATCH", payload={"body": body}
        )
    else:
        response = github(route, "--method", "POST", payload={"body": body})
    url = response.get("html_url")
    if not isinstance(url, str) or not re.fullmatch(
        rf"https://github.com/altharmz/bss/issues/{number}#issuecomment-[1-9][0-9]*", url
    ):
        raise ValueError("unconfirmed issue statistics comment; retry safely")
    return url


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=["start", "pr"])
    args = parser.parse_args()
    try:
        from task_collect import collect, current
        from task_quality import context

        task = os.environ.get("BSS_COLLECT_TASK") or context()[2]
        client, session = current(os.environ)
        collect(task, client if not os.environ.get("BSS_COLLECT_TASK") else None, session)
        print(json.dumps({"url": post(task, args.phase)}, sort_keys=True))
        return 0
    except (
        OSError,
        ValueError,
        RuntimeError,
        TypeError,
        KeyError,
        StopIteration,
        subprocess.SubprocessError,
    ):
        print(
            "telemetry: issue statistics comment failed; "
            "retry make telemetry-comment PHASE=start|pr",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    sys.exit(main())
