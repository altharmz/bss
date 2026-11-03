"""Content-free usage validation and task usage reporting; no conversation scanning."""

# All SQL values pass validators and literal quoting, never a shell.
# ruff: noqa: S608

import argparse
import hashlib
import json
import re
import subprocess
import sys
from datetime import datetime
from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation, localcontext

from task_telemetry import literal, sql

TOKENS = ("input_tokens", "cached_input_tokens", "output_tokens", "total_tokens")
IDENTITY = ("sample_id", "provider", "client", "model", "session_id", "source")
REQUIRED = {
    "version",
    "task_id",
    "sample_id",
    "provider",
    "client",
    "session_id",
    "source",
    "sampled_at",
    "kind",
}
PRICING_KEYS = {
    "version",
    "source_url",
    "effective_from",
    "effective_until",
    "provider",
    "model",
    "currency",
    "input_per_million",
    "cached_input_per_million",
    "output_per_million",
}


def timestamp(value):
    if not isinstance(value, str) or len(value) > 40:
        raise ValueError("invalid timestamp")
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("timestamp must include timezone")
    return result


def identifier(value, nullable=False):
    if value is None and nullable:
        return
    if not isinstance(value, str) or not re.fullmatch(
        r"[A-Za-z0-9][A-Za-z0-9_.:/@+-]{0,159}", value
    ):
        raise ValueError("invalid bounded identity")


def validate(sample):
    if not isinstance(sample, dict) or not REQUIRED <= sample.keys():
        raise ValueError("missing usage fields")
    if sample.keys() - REQUIRED - set(TOKENS) - {"model"}:
        raise ValueError("unknown usage fields")
    if type(sample["version"]) is not int or sample["version"] != 1:
        raise ValueError("unsupported usage version")
    task = sample["task_id"]
    if (
        not isinstance(task, str)
        or len(task) > 80
        or not re.fullmatch(r"issue-[1-9][0-9]*-[a-z0-9-]+", task)
    ):
        raise ValueError("invalid task identity")
    for key in IDENTITY:
        identifier(sample.get(key), nullable=key == "model")
    timestamp(sample["sampled_at"])
    if not isinstance(sample["kind"], str) or sample["kind"] not in {"delta", "unavailable"}:
        raise ValueError("only nonoverlapping deltas or unavailable usage are supported")
    result = dict(sample)
    for key in TOKENS:
        value = sample.get(key)
        if value is not None and (type(value) is not int or not 0 <= value <= 2**63 - 1):
            raise ValueError("invalid token count")
        if sample["kind"] == "unavailable" and value is not None:
            raise ValueError("unavailable usage must be null")
        result[key] = value
    result.setdefault("model", None)
    inp, cached, out, total = (result[key] for key in TOKENS)
    if inp is not None and cached is not None and cached > inp:
        raise ValueError("cached input exceeds inclusive input")
    if total is not None and (
        (inp is not None and total < inp)
        or (out is not None and total < out)
        or (inp is not None and out is not None and total != inp + out)
    ):
        raise ValueError("inconsistent token total")
    return result


def price(sample, pricing):
    if pricing is None:
        return None, None, None
    if not isinstance(pricing, dict) or pricing.keys() != PRICING_KEYS:
        raise ValueError("invalid pricing fields")
    for key in ("version", "provider", "model"):
        identifier(pricing[key])
    if any(pricing[key] != sample[key] for key in ("provider", "model")):
        raise ValueError("pricing identity mismatch")
    url = pricing["source_url"]
    if not isinstance(url, str) or not re.fullmatch(r"https://[A-Za-z0-9./_-]{1,250}", url):
        raise ValueError("pricing requires a public source URL without credentials or query")
    if not isinstance(pricing["currency"], str) or not re.fullmatch(
        r"[A-Z]{3}", pricing["currency"]
    ):
        raise ValueError("invalid currency")
    if (
        not timestamp(pricing["effective_from"])
        <= timestamp(sample["sampled_at"])
        < timestamp(pricing["effective_until"])
    ):
        raise ValueError("sample outside pricing validity")
    rates = []
    for key in ("input_per_million", "cached_input_per_million", "output_per_million"):
        value = pricing[key]
        if not isinstance(value, str) or not re.fullmatch(r"[0-9]{1,6}(\.[0-9]{1,10})?", value):
            raise ValueError("rates must be bounded decimal strings")
        rates.append(Decimal(value))
    metadata = json.dumps(pricing, sort_keys=True, separators=(",", ":"))
    if len(metadata.encode()) > 1024:
        raise ValueError("pricing metadata too large")
    # Missing cache counts cannot be treated as zero, even if total usage is known.
    if any(sample[key] is None for key in TOKENS[:3]):
        return None, pricing["currency"], metadata
    with localcontext() as ctx:
        ctx.prec = 50
        ctx.rounding = ROUND_HALF_EVEN
        cost = (
            (sample["input_tokens"] - sample["cached_input_tokens"]) * rates[0]
            + sample["cached_input_tokens"] * rates[1]
            + sample["output_tokens"] * rates[2]
        ) / Decimal(1000000)
        cost = cost.quantize(Decimal("0.0000000001"))
    if cost >= Decimal("10000000000"):
        raise ValueError("cost exceeds storage precision")
    return format(cost, "f"), pricing["currency"], metadata


def usage_sql(raw, pricing=None, preserve_pricing=False):
    sample = validate(raw)
    cost, currency, metadata = price(sample, pricing)
    # Namespace externally reported sessions separately from workflow thread identities.
    identity = json.dumps([sample[k] for k in ("provider", "client", "session_id")])
    sid = sample["task_id"] + ":usage:" + hashlib.sha256(identity.encode()).hexdigest()
    values = {
        "session_id": sid,
        "sample_key": sample["sample_id"],
        "source": sample["source"],
        "kind": sample["kind"],
        "provider": sample["provider"],
        "client": sample["client"],
        "model": sample["model"],
        "external_session_id": sample["session_id"],
        "sampled_at": sample["sampled_at"],
        **{key: sample[key] for key in TOKENS},
        "estimated_cost": cost,
        "currency": currency,
        "pricing_metadata": metadata,
    }

    def value(key, item):
        if item is None:
            return "NULL"
        if key in TOKENS or key == "estimated_cost":
            return str(item)
        quoted = literal(item)
        if key == "pricing_metadata":
            return quoted + "::jsonb"
        if key == "sampled_at":
            return quoted + "::timestamptz"
        return quoted

    equal = " AND ".join(
        f"{key} IS NOT DISTINCT FROM {value(key, item)}"
        for key, item in values.items()
        if not preserve_pricing or key not in {"estimated_cost", "currency", "pricing_metadata"}
    )
    # A changed payload/pricing with an existing identity is an error, never an overwrite.
    # Serialize imports so concurrent retries cannot silently win a conflict.
    return (
        "BEGIN; SELECT pg_advisory_xact_lock(82002);\n"
        "DO $$ BEGIN IF EXISTS (SELECT 1 FROM usage_samples u JOIN agent_sessions a "
        "USING(session_id) WHERE "
        f"a.task_id={literal(sample['task_id'])} AND u.client={literal(sample['client'])} "
        f"AND u.external_session_id={literal(sample['session_id'])} "
        f"AND u.source<>{literal(sample['source'])}) THEN "
        "RAISE EXCEPTION 'conflicting usage source'; END IF; END $$;\n"
        f"INSERT INTO agent_sessions(session_id, task_id, provider, model, external_session_id) "
        f"VALUES ({literal(sid)}, {literal(sample['task_id'])}, {literal(sample['provider'])}, "
        f"{value('model', sample['model'])}, {literal(sample['session_id'])}) "
        "ON CONFLICT DO NOTHING;\n"
        f"INSERT INTO usage_samples({', '.join(values)}) VALUES "
        f"({', '.join(value(k, v) for k, v in values.items())}) ON CONFLICT DO NOTHING;\n"
        "DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM usage_samples WHERE " + equal + ") THEN "
        "RAISE EXCEPTION 'conflicting usage sample'; END IF; END $$; COMMIT;"
    )


REPORT_SQL = """
SELECT json_build_object(
 'tasks', COALESCE((SELECT json_agg(t) FROM
   (SELECT task_id, started_at, ended_at, outcome FROM tasks) t), '[]'),
 'events', COALESCE((SELECT json_agg(e) FROM
   (SELECT w.task_id, w.event_type, w.occurred_at, w.result,
    w.metadata->>'slot' AS slot, w.metadata->>'ci_state' AS ci_state,
    w.metadata->>'client' AS client, a.external_session_id
    FROM workflow_events w LEFT JOIN agent_sessions a ON a.session_id=w.session_id) e), '[]'),
 'collections', COALESCE((SELECT json_agg(c) FROM
   (SELECT task_id,client,external_session_id,source,bound_at,closed_at,
    checked_at,status,reason,baseline_tokens,latest_tokens FROM usage_collections) c), '[]'),
 'usage', COALESCE((SELECT json_agg(u) FROM
   (SELECT a.task_id, s.provider, s.client, s.model, s.input_tokens,
    s.cached_input_tokens, s.output_tokens, s.total_tokens,
    CASE WHEN s.sample_key IS NOT NULL AND s.pricing_metadata IS NOT NULL
         THEN s.estimated_cost::text END AS estimated_cost, s.currency,
    s.pricing_metadata FROM usage_samples s
    JOIN agent_sessions a USING(session_id)) u), '[]'));
"""


def report(data):
    tasks = []
    groups = {}
    for task in data["tasks"]:
        tid = task["task_id"]
        events = [e for e in data["events"] if e["task_id"] == tid]
        samples = [u for u in data["usage"] if u["task_id"] == tid]
        collections = [c for c in data.get("collections", []) if c["task_id"] == tid]
        startup_observed = any(e["event_type"] == "start-task" for e in events)
        expected = {e["client"] for e in events if e.get("client")}
        observed = {c["client"] for c in collections}
        missing = sorted(expected - observed)
        missing_sessions = []
        for event in events:
            if event["event_type"] != "start-task" or not event.get("external_session_id"):
                continue
            matches = [
                c
                for c in collections
                if c["client"] == event.get("client")
                and c["external_session_id"] == event["external_session_id"]
            ]
            reason = (
                "session_unregistered"
                if not matches
                else (
                    "binding_started_after_startup"
                    if all(
                        timestamp(c["bound_at"]) > timestamp(event["occurred_at"]) for c in matches
                    )
                    else None
                )
            )
            if reason:
                missing_sessions.append(
                    dict(
                        client=event.get("client"),
                        session_id=event["external_session_id"],
                        reason=reason,
                    )
                )
        merged = task["outcome"] == "completed" or any(
            e["event_type"] == "merge-task" and e["result"] == 0 for e in events
        )

        def elapsed(predicate, events=events, task=task):
            times = [timestamp(e["occurred_at"]) for e in events if predicate(e)]
            return (min(times) - timestamp(task["started_at"])).total_seconds() if times else None

        totals = {
            key: sum(u[key] for u in samples)
            if samples and all(u[key] is not None for u in samples)
            else None
            for key in TOKENS
        }
        costs = {}
        for u in samples:
            if u["estimated_cost"] is not None and u["currency"] is not None:
                c = u["currency"]
                costs[c] = costs.get(c, Decimal(0)) + Decimal(u["estimated_cost"])
        slots = sorted({e["slot"] for e in events if e.get("slot")})
        dimensions = sorted({(u["provider"], u["client"], u["model"]) for u in samples}, key=str)
        tasks.append(
            {
                "task_id": tid,
                "outcome": task["outcome"],
                "merged": merged,
                "slots": slots,
                "usage_identities": [
                    dict(zip(("provider", "client", "model"), d, strict=True)) for d in dimensions
                ],
                "sample_count": len(samples),
                "collection_complete": bool(collections)
                and startup_observed
                and not missing
                and not missing_sessions
                and all(c["status"] == "complete" for c in collections),
                "missing_clients": missing,
                "missing_startup": not startup_observed,
                "missing_sessions": missing_sessions,
                "collection_status": collections,
                "tokens": totals,
                "known_cost_by_currency": {c: str(v) for c, v in sorted(costs.items())},
                "pricing_provenance": [
                    json.loads(p)
                    for p in sorted(
                        {
                            json.dumps(u["pricing_metadata"], sort_keys=True)
                            for u in samples
                            if u.get("pricing_metadata")
                        }
                    )
                ],
                "cost_complete": bool(samples)
                and all(
                    u["estimated_cost"] is not None and u["currency"] is not None for u in samples
                ),
                "time_to_pr_seconds": elapsed(
                    lambda e: e["event_type"] == "create-pr" and e["result"] == 0
                ),
                "time_to_green_ci_seconds": elapsed(lambda e: e.get("ci_state") == "passed"),
                "time_to_merge_seconds": elapsed(
                    lambda e: e["event_type"] == "merge-task" and e["result"] == 0
                ),
                "validation_failures": sum(
                    e["event_type"] == "check" and e["result"] != 0 for e in events
                ),
                "validation_retries": max(0, sum(e["event_type"] == "check" for e in events) - 1),
                "ci_failed_observations": sum(e.get("ci_state") == "failed" for e in events),
                "ci_retries": None,
            }
        )
        if task["outcome"] == "completed":
            for provider, client, model in dimensions or [(None, None, None)]:
                for slot in slots or [None]:
                    key = (provider, client, model, slot)
                    groups[key] = groups.get(key, 0) + 1
    return {
        "tasks": sorted(tasks, key=lambda t: t["task_id"]),
        "completed_tasks": [
            dict(zip(("provider", "client", "model", "slot"), key, strict=True), count=value)
            for key, value in sorted(groups.items(), key=lambda pair: str(pair[0]))
        ],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["report"])
    parser.parse_args()
    try:
        print(json.dumps(report(json.loads(sql(REPORT_SQL))), sort_keys=True))
    except (
        OSError,
        ValueError,
        TypeError,
        KeyError,
        InvalidOperation,
        RuntimeError,
        subprocess.SubprocessError,
    ):
        # Never echo parser errors, database stderr or paths containing credentials.
        print("telemetry: usage report failed; check storage", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
