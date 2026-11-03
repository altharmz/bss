"""Project only machine usage metadata from explicitly bound client sessions.

Source records may contain conversation content. Never persist, print, hash or otherwise
export those records: only the allowlisted usage/identity projection leaves the parser.
"""

# Validated SQL identifiers/values, no shell or provider API calls.
# ruff: noqa: S608
import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

from task_telemetry import literal, sql
from task_usage import TOKENS, identifier, price, timestamp, usage_sql, validate

CLIENTS = {"codex": "codex-rollout-v1", "claude": "claude-request-v1", "kimi": "kimi-wire-1.5"}


class CollectionError(ValueError):
    pass


def records(path):
    """A torn final append is retryable; malformed complete lines are not ignored."""
    with path.open() as stream:
        for line in stream:
            if not line.endswith("\n"):
                yield {"incomplete_append": True}
                break
            try:
                row = json.loads(line)
                if not isinstance(row, dict):
                    raise ValueError
            except (ValueError, TypeError):
                raise CollectionError("source_invalid") from None
            yield row


def count(value):
    if value is not None and (type(value) is not int or not 0 <= value <= 2**63 - 1):
        raise CollectionError("source_invalid")
    return value


def add(*values):
    values = [count(v) for v in values]
    return sum(values) if all(v is not None for v in values) else None


def project(client, path, session, with_state=False, unsupported_pricing=None):
    """Return request metadata, final turn times, and parser coverage limitations.

    Codex cumulative totals become adjacent deltas, never last_usage + total_usage.
    Claude streaming copies are collapsed by message ID before immutable ingestion.
    Kimi 2.4.x usage.record is per request; context-size events are never usage.
    """
    output, ended = [], []
    previous = None
    provider = model = None
    seen = {}
    reason = None
    active = False
    for index, row in enumerate(records(path)):
        if row.get("incomplete_append"):
            active = True
            continue
        kind = row.get("type")
        if client == "codex":
            if kind not in {"session_meta", "turn_context", "event_msg"}:
                continue
            payload = row.get("payload") or {}
            if not isinstance(payload, dict):
                raise CollectionError("source_invalid")
            if kind == "session_meta":
                if payload.get("id") != session:
                    raise CollectionError("source_invalid")
                provider = payload.get("model_provider")
            if kind == "turn_context":
                model = payload.get("model")
                active = True
            if kind != "event_msg":
                continue
            if payload.get("type") in {"task_complete", "turn_complete"}:
                ended.append(row["timestamp"])
                active = False
            if payload.get("type") in {"task_started", "turn_started"}:
                active = True
            if payload.get("type") != "token_count" or not payload.get("info"):
                continue
            info = payload["info"]
            if not isinstance(info, dict):
                raise CollectionError("source_invalid")
            raw = info.get("total_token_usage")
            if not isinstance(raw, dict):
                reason = "counters_missing"
                continue
            current = {k: count(raw.get(k)) for k in TOKENS}
            if previous == current:
                continue
            active = True
            if previous is None:
                # This is the first cumulative event in the complete session source.
                # Its totals cover the session prefix, not a last-request counter.
                previous = {k: 0 if current[k] is not None else None for k in TOKENS}
            delta = {
                k: current[k] - previous[k]
                if current[k] is not None and previous[k] is not None
                else None
                for k in TOKENS
            }
            previous = current
            if any(v is not None and v < 0 for v in delta.values()):
                reason = "counter_reset"
                continue
            if unsupported_pricing is not None and raw.get("cache_write_input_tokens", 0):
                unsupported_pricing.add(f"counter-{index}")
            output.append(
                dict(
                    sample_id=f"counter-{index}",
                    provider=provider or "unknown",
                    model=model,
                    sampled_at=row["timestamp"],
                    **delta,
                )
            )
        elif client == "claude":
            if kind == "user":
                active = True
            if kind != "assistant":
                continue
            if row.get("isSidechain") and ":agent:" not in session:
                continue
            if row.get("sessionId") != session.split(":agent:")[0]:
                raise CollectionError("source_invalid")
            message = row.get("message") or {}
            if not isinstance(message, dict):
                raise CollectionError("source_invalid")
            raw = message.get("usage")
            mid = message.get("id")
            if not mid or not isinstance(raw, dict):
                continue
            # Only finalized API messages; intermediate streaming usage is mutable.
            if message.get("stop_reason") is None:
                continue
            inp = add(
                raw.get("input_tokens"),
                raw.get("cache_read_input_tokens"),
                raw.get("cache_creation_input_tokens"),
            )
            out = count(raw.get("output_tokens"))
            item = dict(
                sample_id=mid,
                provider=message.get("provider") or "unknown",
                model=message.get("model"),
                sampled_at=row["timestamp"],
                input_tokens=inp,
                cached_input_tokens=count(raw.get("cache_read_input_tokens")),
                output_tokens=out,
                total_tokens=add(inp, out),
            )
            if mid in seen:
                if any(seen[mid][k] != item[k] for k in item if k != "sampled_at"):
                    raise CollectionError("source_invalid")
            else:
                # Claude emits the same completed request once per content block,
                # with different envelope timestamps. Keep its first final copy.
                seen[mid] = item
            if message.get("stop_reason") == "end_turn":
                ended.append(row["timestamp"])
                active = False
            else:
                active = True
        else:
            expected_agent = session.partition(":agent:")[2] or "main"
            if row.get("agentId") not in {None, expected_agent}:
                continue
            if kind == "metadata" and row.get("protocol_version") != "1.5":
                raise CollectionError("source_invalid")
            if kind == "llm.request":
                provider = row.get("provider")
                active = True
            if kind in {"agent.turn.started", "turn.prompt"}:
                active = True
            if kind in {"agent.turn.ended", "turn.ended"}:
                ended.append(datetime.fromtimestamp(row["time"] / 1000, UTC).isoformat())
                active = False
            if kind != "usage.record":
                continue
            raw = row.get("usage") or {}
            if not isinstance(raw, dict):
                raise CollectionError("source_invalid")
            inp = add(
                raw.get("inputOther"), raw.get("inputCacheRead"), raw.get("inputCacheCreation")
            )
            out = count(raw.get("output"))
            if unsupported_pricing is not None and raw.get("inputCacheCreation") != 0:
                unsupported_pricing.add(f"request-{index}")
            output.append(
                dict(
                    sample_id=f"request-{index}",
                    provider=provider or "unknown",
                    model=row.get("model"),
                    sampled_at=datetime.fromtimestamp(row["time"] / 1000, UTC).isoformat(),
                    input_tokens=inp,
                    cached_input_tokens=count(raw.get("inputCacheRead")),
                    output_tokens=out,
                    total_tokens=add(inp, out),
                )
            )
    if client == "claude":
        output = list(seen.values())
    if with_state:
        return output, ended, reason, active
    return output, ended, reason


def locate(client, session, home=None):
    identifier(session)
    # Session IDs are filename components, never caller-supplied glob expressions.
    parent, _, agent = session.partition(":agent:")
    if any(c in parent + agent for c in "/:@+"):
        raise CollectionError("source_invalid")
    home = Path.home() if home is None else Path(home)
    if client == "codex":
        root = Path(os.environ.get("CODEX_HOME", home / ".codex")) / "sessions"
        files = list(root.glob(f"**/*{parent}.jsonl"))
    elif client == "claude":
        root = Path(os.environ.get("CLAUDE_CONFIG_DIR", home / ".claude")) / "projects"
        files = (
            list(root.glob(f"*/{parent}/subagents/agent-{agent}.jsonl"))
            if agent
            else list(root.glob(f"*/{parent}.jsonl"))
        )
    else:
        root = Path(os.environ.get("KIMI_CODE_HOME", home / ".kimi-code")) / "sessions"
        files = list(root.glob(f"*/session_{parent}/agents/{agent or 'main'}/wire.jsonl"))
    if not files:
        raise CollectionError("source_missing")
    if len(files) != 1:
        raise CollectionError("source_ambiguous")
    path = files[0]
    if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
        raise CollectionError("source_invalid")
    return path


def current(environ):
    client = environ.get("BSS_TASK_OWNER")
    if client is None:
        client = (
            "codex"
            if environ.get("CODEX_THREAD_ID")
            else (
                "claude"
                if environ.get("CLAUDE_SESSION_ID")
                else ("kimi" if environ.get("KIMI_SESSION_ID") else None)
            )
        )
    if client is None and claude_session():
        client = "claude"
    if client not in CLIENTS:
        return None, None
    session = environ.get("BSS_TELEMETRY_SESSION_ID") or environ.get(
        {"codex": "CODEX_THREAD_ID", "claude": "CLAUDE_SESSION_ID", "kimi": "KIMI_SESSION_ID"}[
            client
        ]
    )
    if not session and client == "claude":
        session = claude_session()
    if not session and client == "kimi":
        session = kimi_session()
    if session:
        identifier(session)
    return client, session or "unavailable"


def kimi_session(proc="/proc"):
    """Match Kimi's process-owned session lock to our ancestry, never 'latest session'."""
    home = Path(os.environ.get("KIMI_CODE_HOME", Path.home() / ".kimi-code"))
    root = home / "locks/sessions"
    hostname = os.uname().nodename
    proc = Path(proc)
    pid = os.getpid()
    for _ in range(64):
        try:
            fields = (proc / f"{pid}/stat").read_text().rsplit(")", 1)[1].split()
            sessions = set()
            for registry in root.glob("session_*.lock"):
                try:
                    if registry.is_symlink() or registry.stat().st_size > 65536:
                        continue
                    row = json.loads(registry.read_text())
                    token = row.get("token")
                    if (
                        row.get("version") != 1
                        or row.get("pid") != pid
                        or row.get("hostname") != hostname
                        or not isinstance(token, str)
                        or not token.startswith(f"{pid}:")
                    ):
                        continue
                    identifier(token)
                    session = registry.name.removeprefix("session_").removesuffix(".lock")
                    identifier(session)
                    locate("kimi", session)
                    sessions.add(session)
                except (OSError, ValueError, TypeError, AttributeError):
                    continue
            if sessions:
                return sessions.pop() if len(sessions) == 1 else None
            pid = int(fields[1])
            if pid <= 1:
                break
        except (OSError, ValueError, TypeError, IndexError, AttributeError):
            break
    return None


def claude_session():
    """Match Claude's machine process registry to our ancestry, never 'latest session'."""
    root = Path(os.environ.get("CLAUDE_CONFIG_DIR", Path.home() / ".claude")) / "sessions"
    pid = os.getpid()
    for _ in range(64):
        try:
            fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
            registry = root / f"{pid}.json"
            if registry.exists() and not registry.is_symlink() and registry.stat().st_size <= 65536:
                row = json.loads(registry.read_text())
                if row.get("pid") == pid and str(row.get("procStart")) == fields[19]:
                    session = row.get("sessionId")
                    identifier(session)
                    return session
            pid = int(fields[1])
            if pid <= 1:
                break
        except (OSError, ValueError, TypeError, IndexError, AttributeError):
            break
    return None


def bind(task, client, session, bound_at=None):
    """The first binding wins. Rebinding a session closes its previous issue window."""
    identifier(task)
    task_match = re.fullmatch(r"issue-([1-9][0-9]*)-[a-z0-9-]+", task)
    if not task_match or len(task) > 80:
        raise ValueError("invalid task identity")
    identifier(session)
    if client not in CLIENTS:
        raise ValueError("invalid client")
    t, c, s = map(literal, (task, client, session))
    source = literal(CLIENTS[client])
    # Initial client binding starts at its validated startup event, even when this
    # collector was introduced partway through an already-running task.
    boundary = (
        literal(bound_at) + "::timestamptz"
        if bound_at
        else (
            "CASE WHEN NOT EXISTS (SELECT 1 FROM usage_collections "
            f"WHERE task_id={t} AND client={c}) "
            "THEN COALESCE((SELECT min(occurred_at) FROM workflow_events "
            f"WHERE task_id={t} AND event_type='start-task' AND metadata->>'client'={c}),now()) "
            "ELSE now() END"
        )
    )
    if bound_at:
        timestamp(bound_at)
    sql(f"""BEGIN; SELECT pg_advisory_xact_lock(82003);
        INSERT INTO tasks(task_id,issue_number,branch)
         VALUES({t},{int(task_match[1])},{t}) ON CONFLICT DO NOTHING;
        UPDATE usage_collections SET closed_at = COALESCE(closed_at, now())
         WHERE client={c} AND external_session_id={s} AND task_id<>{t};
        INSERT INTO usage_collections(task_id,client,external_session_id,source,bound_at)
         VALUES({t},{c},{s},{source},{boundary}) ON CONFLICT DO NOTHING; COMMIT;""")


def delegates(row):
    """Discover only machine parent relationships and session-owned agent directories."""
    client, session = row["client"], row["external_session_id"]
    if session == "unavailable" or ":agent:" in session:
        return []
    try:
        source = locate(client, session)
    except CollectionError:
        return []
    found = []
    if client == "codex":
        root = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")) / "sessions"
        for path in root.glob("**/*.jsonl"):
            try:
                first = next(records(path), {})
                payload = first.get("payload") or {}
                relation = payload.get("source") or {}
                if isinstance(relation, dict):
                    spawned = relation.get("subagent", {}).get("thread_spawn", {})
                    if spawned.get("parent_thread_id") == session:
                        child = payload.get("id")
                        identifier(child)
                        found.append(child)
            except (OSError, ValueError, TypeError, AttributeError):
                continue
    elif client == "claude":
        for path in (source.parent / session / "subagents").glob("agent-*.jsonl"):
            found.append(session + ":agent:" + path.stem.removeprefix("agent-"))
    else:
        for path in source.parent.parent.glob("*/wire.jsonl"):
            if path.parent.name != "main":
                found.append(session + ":agent:" + path.parent.name)
    return found


def collection_rows(task):
    identifier(task)
    return json.loads(
        sql(
            "SELECT COALESCE(json_agg(c),'[]') FROM "
            "(SELECT a.*, (SELECT min(b.bound_at) FROM usage_collections b "
            "WHERE b.client=a.client AND b.external_session_id=a.external_session_id "
            "AND b.bound_at>a.bound_at) AS next_bound_at "
            f"FROM usage_collections a WHERE task_id={literal(task)}) c;"
        )
    )


def collect_one(row, pricing=None, home=None):
    client, session = row["client"], row["external_session_id"]
    key = " AND ".join(
        f"{k}={literal(row[k])}" for k in ("task_id", "client", "external_session_id")
    )
    status, reason, samples = "unavailable", "counters_missing", []
    baseline = latest = None
    try:
        if session == "unavailable":
            raise CollectionError("session_identity_missing")
        path = locate(client, session, home)
        unsupported_pricing = set()
        items, ended, limitation, active = project(
            client, path, session, with_state=True, unsupported_pricing=unsupported_pricing
        )
        start = timestamp(row["bound_at"])
        close = timestamp(row["closed_at"]) if row.get("closed_at") else None
        # A make command runs before its enclosing model request finishes. Wait for
        # the next machine turn end before declaring a closed window collected.
        end = min((timestamp(t) for t in ended if close and timestamp(t) >= close), default=None)
        # Handoff/delegation sources often finish before the task closes. A
        # terminal source with no later activity already has its final counter.
        if close and end is None and ended and not active:
            terminal = max(timestamp(t) for t in ended)
            if all(timestamp(i["sampled_at"]) <= terminal for i in items):
                end = terminal
        next_bound = timestamp(row["next_bound_at"]) if row.get("next_bound_at") else None
        capped = False
        if next_bound and (end is None or next_bound < end):
            end = next_bound
            capped = True
            limitation = limitation or "final_counter_pending"

        def total(selected):
            return {
                k: sum(s[k] for s in selected)
                if selected and all(s[k] is not None for s in selected)
                else None
                for k in TOKENS
            }

        baseline = total([i for i in items if timestamp(i["sampled_at"]) < start])
        latest = total(
            [
                i
                for i in items
                if end is None
                or (
                    timestamp(i["sampled_at"]) < end
                    if capped
                    else timestamp(i["sampled_at"]) <= end
                )
            ]
        )
        for item in items:
            when = timestamp(item["sampled_at"])
            if when < start or (end and (when >= end if capped else when > end)):
                continue
            sample = validate(
                dict(
                    version=1,
                    task_id=row["task_id"],
                    client=client,
                    session_id=session,
                    source=CLIENTS[client],
                    kind="delta",
                    **item,
                )
            )
            # Cache-write tokens need a separate rate for Anthropic pricing. The
            # legacy three-rate interchange cannot price them safely.
            applicable = (
                pricing
                if client != "claude"
                and pricing
                and item["sample_id"] not in unsupported_pricing
                and all(pricing.get(k) == sample[k] for k in ("provider", "model"))
                else None
            )
            if applicable:
                try:
                    price(sample, applicable)
                except ValueError:
                    # An unsupported/expired rate card is a pricing gap, not a
                    # reason to discard trustworthy machine token counts.
                    applicable = None
            samples.append((sample, applicable))
        if samples:
            status = "partial"
            missing_counts = any(any(s[k] is None for k in TOKENS) for s, _ in samples)
            reason = limitation or (
                "counters_missing"
                if missing_counts
                else ("final_counter_pending" if close else "session_open")
            )
            if (
                end
                and not limitation
                and all(all(s[k] is not None for k in TOKENS) for s, _ in samples)
            ):
                status, reason = "complete", "collected"
        elif limitation:
            reason = limitation
    except CollectionError as error:
        reason = str(error)
    except (OSError, ValueError, TypeError, KeyError, OverflowError, AttributeError):
        reason = "source_invalid"
    # One transaction for the entire projection and its coverage update. A failed
    # sample never advances coverage, and large sessions need only one DB roundtrip.
    statements = []
    for sample, card in samples:
        statements.append(
            usage_sql(sample, card, preserve_pricing=True)
            .removeprefix("BEGIN;")
            .removesuffix("COMMIT;")
        )
    statements.append(
        f"UPDATE usage_collections SET checked_at=now(),status={literal(status)},"
        f"reason={literal(reason)},baseline_tokens="
        f"CASE WHEN baseline_tokens IS NULL OR baseline_tokens='null'::jsonb "
        f"THEN {literal(json.dumps(baseline))}::jsonb ELSE baseline_tokens END,"
        f"latest_tokens={literal(json.dumps(latest))}::jsonb WHERE {key};"
    )
    sql("BEGIN;\n" + "\n".join(statements) + "\nCOMMIT;")
    return dict(
        client=client,
        session_id=session,
        status=status,
        reason=reason,
        observed_samples=len(samples),
    )


def collect(task, client=None, session=None, close=False, pricing=None):
    if pricing is not None and not isinstance(pricing, dict):
        raise ValueError("invalid pricing card")
    if client:
        bind(task, client, session or "unavailable")
    expected = json.loads(
        sql(
            "SELECT COALESCE(json_agg(x.client),'[]') FROM "
            "(SELECT DISTINCT metadata->>'client' AS client FROM workflow_events "
            f"WHERE task_id={literal(task)} AND event_type='start-task') x;"
        )
    )
    existing = {r["client"] for r in collection_rows(task)}
    for missing in set(expected) - existing:
        if missing in CLIENTS:
            bind(task, missing, "unavailable")
    # Recursively include nested delegates. Parent binding times constrain their
    # sources too; no client/provider/model identity is derived from a worker slot.
    rows = collection_rows(task)
    known = {(r["client"], r["external_session_id"]) for r in rows}
    for row in rows:
        for child in delegates(row):
            if (row["client"], child) not in known:
                bind(task, row["client"], child, row["bound_at"])
                known.add((row["client"], child))
                rows.extend(
                    r
                    for r in collection_rows(task)
                    if r["client"] == row["client"] and r["external_session_id"] == child
                )
    if close:
        sql(
            f"UPDATE usage_collections SET closed_at=COALESCE(closed_at,now()) "
            f"WHERE task_id={literal(task)};"
        )
    return [collect_one(row, pricing) for row in collection_rows(task)]


def watch(task, attempts=60, pause=2):
    """Bounded final-tail capture after the enclosing agent response finishes.

    Runs from surviving primary sources, independently of disposable task runtime.
    The persisted ledger remains partial if the final machine counter never arrives.
    """
    for _ in range(attempts):
        results = collect(task)
        if results and all(r["status"] in {"complete", "unavailable"} for r in results):
            return results
        time.sleep(pause)
    return results


def launch_final_capture(task):
    import task_telemetry

    # finish-task has synchronized primary before emitting task-completed. Never
    # launch a process whose source files are about to be removed with a worktree.
    primary = task_telemetry.ROOT
    subprocess.Popen(  # noqa: S603 - fixed interpreter, validated task, no shell
        [sys.executable, "-B", str(primary / "scripts/task_collect.py"), "--watch", "--task", task],
        cwd=primary,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
        close_fds=True,
        env={
            k: os.environ[k]
            for k in ("HOME", "PATH", "CODEX_HOME", "CLAUDE_CONFIG_DIR", "KIMI_CODE_HOME")
            if k in os.environ
        },
    )


def automatic(task, operation, client=None, result=0, pr_number=None):
    """Fail open but visibly; retries reread bound sources, including closed sessions."""
    try:
        detected, session = current(os.environ)
        client = client or detected
        if operation.startswith(("finish-task", "cleanup-task")) or operation == "task-completed":
            client = session = None
        results = collect(task, client, session, close=operation == "task-completed")
        if operation == "task-completed" and any(r["status"] == "partial" for r in results):
            launch_final_capture(task)
        if operation in {"start-task", "finish-task", "task-completed"}:
            print("telemetry collection: " + json.dumps(results, sort_keys=True), file=sys.stderr)
        if operation == "start-task" and any(
            r["reason"] == "counters_missing" and r["observed_samples"] == 0 for r in results
        ):
            print(
                "telemetry: no usage observed after the binding yet - normal at startup; "
                "later lifecycle targets capture it",
                file=sys.stderr,
            )
    except (
        OSError,
        ValueError,
        RuntimeError,
        TypeError,
        KeyError,
        AttributeError,
        subprocess.SubprocessError,
    ):
        print(
            "telemetry: collection failed; retry make telemetry-collect after make telemetry-up",
            file=sys.stderr,
        )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task", default=os.environ.get("BSS_COLLECT_TASK"))
    parser.add_argument("--probe", action="store_true")
    parser.add_argument("--watch", action="store_true")
    args = parser.parse_args()
    try:
        detected, detected_session = current(os.environ)
        client = os.environ.get("BSS_COLLECT_CLIENT") or detected
        session = os.environ.get("BSS_COLLECT_SESSION") or (
            detected_session if client == detected else None
        )
        if args.probe:
            if client not in CLIENTS or not session:
                raise ValueError("probe requires a client and session")
            items, ended, reason = project(client, locate(client, session), session)
            projection = json.dumps(items, sort_keys=True, separators=(",", ":"))
            result = dict(
                client=client,
                source=CLIENTS[client],
                session_id=session,
                observed_samples=len(items),
                observed_turn_ends=len(ended),
                reason=reason,
                tokens={
                    k: sum(r[k] for r in items)
                    if items and all(r[k] is not None for r in items)
                    else None
                    for k in TOKENS
                },
                projection_sha256=hashlib.sha256(projection.encode()).hexdigest(),
            )
            print(json.dumps(result, sort_keys=True))
            return int(not items or reason is not None)
        task = args.task
        if task:
            explicit_client = os.environ.get("BSS_COLLECT_CLIENT")
            explicit_session = os.environ.get("BSS_COLLECT_SESSION")
            if explicit_client and not explicit_session:
                raise ValueError("external-task registration requires an explicit session")
            client, session = explicit_client, explicit_session
        if not task:
            from task_quality import context

            task = context()[2]
        if args.watch:
            print(json.dumps({"collections": watch(task)}, sort_keys=True))
            return 0
        result = collect(task, client, session, close=os.environ.get("BSS_COLLECT_CLOSE") == "1")
        print(json.dumps({"collections": result}, sort_keys=True))
        return int(not result or any(r["status"] == "unavailable" for r in result))
    except (
        OSError,
        ValueError,
        RuntimeError,
        TypeError,
        KeyError,
        AttributeError,
        subprocess.SubprocessError,
    ):
        print(
            "telemetry: collection failed; check session binding, source and storage",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    sys.exit(main())
