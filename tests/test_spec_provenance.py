"""Every vendored spec file is byte-identical to upstream at its pinned commit.

spec/README.md records each file's upstream git blob SHA. The dev image has no git, so the
SHA is recomputed here: sha1(b"blob <len>\\0" + content). A spec file without a recorded
SHA, or a recorded SHA without a file, is a failure.
"""

import hashlib
import re
from pathlib import Path

SPEC_DIR = Path(__file__).resolve().parent.parent / "spec"
NOT_SPECS = {"README.md", "SHA256SUMS"}

# | `<file>` | `<40 hex>` |  - only the "Upstream blob SHAs" table has this shape
BLOB_ROW = re.compile(r"^\| `([^`]+)` \| `([0-9a-f]{40})` \|$", re.MULTILINE)


def _git_blob_sha(data: bytes) -> str:
    # sha1 is git's object id, not a security primitive here
    return hashlib.sha1(b"blob %d\0" % len(data) + data, usedforsecurity=False).hexdigest()


def test_git_blob_sha_matches_git():
    # `git hash-object` of an empty file and of "hello\n"
    assert _git_blob_sha(b"") == "e69de29bb2d1d6434b8b29ae775ad8c2e48c5391"
    assert _git_blob_sha(b"hello\n") == "ce013625030ba8dba906f756967f9e9ca394464a"


def test_spec_files_match_upstream_blob_shas():
    readme = SPEC_DIR / "README.md"
    assert readme.is_file(), f"{readme} missing (is ./spec mounted?)"
    recorded = dict(BLOB_ROW.findall(readme.read_text()))
    on_disk = {p.name for p in SPEC_DIR.iterdir() if p.is_file()} - NOT_SPECS

    assert recorded, "no blob SHAs found in spec/README.md"
    assert set(recorded) == on_disk, "spec files and recorded blob SHAs differ"
    for name, expected in recorded.items():
        actual = _git_blob_sha((SPEC_DIR / name).read_bytes())
        assert actual == expected, f"{name} differs from the upstream blob in spec/README.md"
