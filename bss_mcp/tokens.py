"""Token tooling: static API tokens and OAuth grants in the connector's store.

    python -m bss_mcp.tokens create <name> --scope read|write   # the token is printed once
    python -m bss_mcp.tokens list                               # static tokens + live grants
    python -m bss_mcp.tokens revoke <token id>
    python -m bss_mcp.tokens revoke-grant <grant id>

Run in the mcp service, which has the store's volume: `make mcp-token-*` locally,
`make remote SERVICE=mcp CMD="python -m bss_mcp.tokens ..."` on the VPS (docs/mcp.md).
Revocation takes effect on the next request - the server reads the store every time.
"""

import argparse
import json
import logging
import sys
from datetime import UTC, datetime
from typing import TextIO

from bss_mcp.audit import audit
from bss_mcp.clock import Clock
from bss_mcp.config import Settings
from bss_mcp.store import SCOPES, Store


def _when(epoch: float | None) -> str:
    if epoch is None:
        return "-"
    return datetime.fromtimestamp(epoch, UTC).strftime("%Y-%m-%d %H:%M:%SZ")


def _client_name(store: Store, client_id: str) -> str:
    info = store.client(client_id)
    return (json.loads(info).get("client_name") or "-") if info else "(unknown client)"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m bss_mcp.tokens")
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("create", help="a new static token, printed once")
    create.add_argument("name")
    create.add_argument("--scope", choices=SCOPES, required=True)
    commands.add_parser("list", help="static tokens and live OAuth grants")
    revoke = commands.add_parser("revoke", help="revoke a static token by id")
    revoke.add_argument("id")
    revoke_grant = commands.add_parser("revoke-grant", help="revoke an OAuth grant by id")
    revoke_grant.add_argument("id")
    return parser


def run(argv: list[str], store: Store, clock: Clock, out: TextIO, err: TextIO) -> int:
    args = _parser().parse_args(argv)
    if args.command == "create":
        try:
            record, token = store.create_static_token(args.name, args.scope)
        except ValueError as e:
            print(f"refused: {e}", file=err)
            return 1
        audit(clock, event="token_create", principal=f"mcp-token:{record.name}", outcome=args.scope)
        print(f"id {record.id}  name {record.name}  scope {record.scope}", file=err)
        print("The token is shown this once; only its hash is stored:", file=err)
        print(token, file=out)
        return 0
    if args.command == "list":
        print("static tokens", file=out)
        print(f"  {'id':<16}  {'name':<20}  {'scope':<5}  {'created':<20}  revoked", file=out)
        for t in store.static_tokens():
            print(
                f"  {t.id:<16}  {t.name:<20}  {t.scope:<5}  {_when(t.created_at):<20}"
                f"  {_when(t.revoked_at)}",
                file=out,
            )
        print("live OAuth grants", file=out)
        print(f"  {'id':<16}  {'user':<12}  {'scopes':<10}  {'expires':<20}  client", file=out)
        for g in store.grants():
            print(
                f"  {g['grant_id']:<16}  {g['subject']:<12}  {g['scopes']:<10}"
                f"  {_when(g['expires_at']):<20}  {_client_name(store, g['client_id'])}",
                file=out,
            )
        return 0
    if args.command == "revoke":
        if not store.revoke_static_token(args.id):
            print(f"no live static token with id {args.id}", file=err)
            return 1
        audit(clock, event="token_revoke", outcome=args.id)
        print(f"revoked static token {args.id}", file=out)
        return 0
    # revoke-grant
    count = store.revoke_grant(args.id)
    if count == 0:
        print(f"no live grant with id {args.id}", file=err)
        return 1
    audit(clock, event="grant_revoke", outcome=args.id)
    print(f"revoked grant {args.id} ({count} live tokens)", file=out)
    return 0


def main() -> int:
    settings = Settings()
    # the audit lines go to stderr with the rest of the run's notes
    logging.basicConfig(level=logging.INFO, stream=sys.stderr, format="%(message)s")
    clock = Clock()
    return run(sys.argv[1:], Store(settings.mcp_store, clock), clock, sys.stdout, sys.stderr)


if __name__ == "__main__":
    sys.exit(main())
