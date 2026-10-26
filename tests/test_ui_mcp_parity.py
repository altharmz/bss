"""UI vs MCP parity (docs/decisions/harness.md): every MCP operation has a call in the
admin UI or a reasoned line in tests/ui-mcp-allowlist.txt. The scan is
scripts/ui_mcp_parity.py over frontend/src (mounted read-only by compose.override.yaml)."""

import sys
from pathlib import Path

from bss_mcp.operations import AUTH_OPERATIONS, EXCLUDED
from tests.test_mcp_tools import api_operations

sys.path.insert(0, str(Path("scripts")))
import ui_mcp_parity as parity  # noqa: E402

ALLOWLIST = Path("tests/ui-mcp-allowlist.txt")
SRC = Path("frontend/src")


def allowlist() -> dict[str, str]:
    entries = {}
    for line in ALLOWLIST.read_text().splitlines():
        if line.strip() and not line.startswith("#"):
            name, _, reason = line.partition(":")
            entries[name.strip()] = reason.strip()
    return entries


def mcp_operations() -> dict[str, tuple[str, str]]:
    skipped = EXCLUDED | AUTH_OPERATIONS
    return {n: key for n, key in api_operations().items() if key not in skipped}


def test_every_mcp_operation_has_a_ui_call_or_an_allowlist_entry():
    calls, unresolved = parity.extract(SRC)
    assert not unresolved, f"UI calls whose path the scan cannot resolve: {unresolved}"
    missing = set(parity.uncalled(mcp_operations(), calls)) - set(allowlist())
    assert not missing, f"MCP operations with no UI call and no allowlist entry: {sorted(missing)}"


def test_the_allowlist_has_no_stale_or_unreasoned_entries():
    calls, _ = parity.extract(SRC)
    operations = mcp_operations()
    uncalled = set(parity.uncalled(operations, calls))
    entries = allowlist()
    assert set(entries) <= set(operations), sorted(set(entries) - set(operations))
    called = sorted(set(entries) - uncalled)
    assert not called, f"called by the UI now, drop: {called}"
    assert all(entries.values()), "every allowlist line needs a reason"


# --- the extractor on small samples ---


def scan(tmp_path: Path, **files: str):
    for name, text in files.items():
        (tmp_path / name).write_text(text)
    return parity.extract(tmp_path)


def test_extractor_resolves_constants_templates_maps_and_methods(tmp_path):
    calls, unresolved = scan(
        tmp_path,
        **{
            "a.ts": """
export const BASE = '/tmf-api/x/v1';
const MAP: Record<K, string> = { a: `${BASE}/one`, b: `${BASE}/two` };
export async function f(id: string, kind: K) {
  await getJson(`${BASE}/thing/${id}?limit=1`);
  await postJson(BASE + '/thing', {});
  await patchJson(`${MAP[kind]}/${id}`, {});
  await request('DELETE', `${BASE}/thing/${id}`);
  return getAll(ok ? BASE : `${BASE}/other`);
}
"""
        },
    )
    assert unresolved == []
    assert calls == {
        ("GET", "/tmf-api/x/v1/thing/{}"),
        ("POST", "/tmf-api/x/v1/thing"),
        ("PATCH", "/tmf-api/x/v1/one/{}"),
        ("PATCH", "/tmf-api/x/v1/two/{}"),
        ("DELETE", "/tmf-api/x/v1/thing/{}"),
        ("GET", "/tmf-api/x/v1"),
        ("GET", "/tmf-api/x/v1/other"),
    }


def test_extractor_reports_an_unresolvable_call_and_honours_a_marker(tmp_path):
    calls, unresolved = scan(tmp_path, **{"a.ts": "await postJson(request.path, body);\n"})
    assert unresolved == ["a.ts: postJson(request.path)"] and calls == set()
    calls, unresolved = scan(
        tmp_path,
        **{"a.ts": "// parity: POST /tmf-api/x/v1/y\nawait postJson(request.path, body);\n"},
    )
    assert unresolved == [] and calls == {("POST", "/tmf-api/x/v1/y")}


def test_extractor_ignores_tests_and_generated_code(tmp_path):
    (tmp_path / "generated").mkdir()
    (tmp_path / "generated" / "g.ts").write_text("getJson('/tmf-api/g')")
    calls, _ = scan(tmp_path, **{"a.test.ts": "getJson('/tmf-api/t')"})
    assert calls == set()


def test_matching_treats_a_placeholder_as_any_one_segment():
    assert parity.matches("/tmf-api/x/{id}/retry", "/tmf-api/x/{}/retry")
    assert parity.matches("/tmf-api/x/{id}", "/tmf-api/x/hub")
    assert not parity.matches("/tmf-api/x/{id}", "/tmf-api/x/{}/retry")


def test_uncalled_lists_an_operation_without_a_ui_call():
    operations = {"get_a": ("GET", "/p/a/{id}"), "delete_a": ("DELETE", "/p/a/{id}")}
    assert parity.uncalled(operations, {("GET", "/p/a/{}")}) == ["delete_a"]
