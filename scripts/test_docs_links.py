"""docs_links.py: broken links, missing anchors and unknown commands in agent-facing docs."""

import tempfile
import unittest
from pathlib import Path

import docs_links as links

MAKEFILE = """.PHONY: build
test: ## run
\tpytest
a b: c
\ttrue
VAR := 1
"""


class DocsLinksTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="bss-docs-links-")
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        (self.root / "scripts").mkdir()
        (self.root / "scripts/real.py").write_text("")
        (self.root / "docs/archive").mkdir(parents=True)
        (self.root / "Makefile").write_text(MAKEFILE)

    def write(self, name, text):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    def problems(self):
        return links.check(self.root)

    def test_valid_links_and_anchors_pass(self):
        self.write("docs/a.md", "# Title\n\n## Two words, `code`\n\n## Again\n\n## Again\n")
        self.write(
            "CLAUDE.md",
            "[a](docs/a.md) [t](docs/a.md#title) [w](docs/a.md#two-words-code)\n"
            "[d](docs/a.md#again-1) [self](#intro) [web](https://example.com/x)\n\n## Intro\n",
        )
        self.assertEqual(self.problems(), [])

    def test_broken_link_and_missing_anchor_are_reported_with_line_numbers(self):
        self.write("docs/a.md", "# Title\n")
        self.write(
            "CLAUDE.md", "ok\n[gone](docs/missing.md)\n[bad](docs/a.md#nope)\n[x](#nowhere)\n"
        )
        found = self.problems()
        self.assertEqual(len(found), 3)
        self.assertIn("CLAUDE.md:2: broken link docs/missing.md", found)
        self.assertIn("CLAUDE.md:3: no heading for anchor docs/a.md#nope", found)
        self.assertIn("CLAUDE.md:4: no heading for anchor #nowhere", found)

    def test_code_fences_hide_links_and_headings(self):
        self.write("docs/a.md", "# Real\n\n```\n# Fake heading\n[x](nothing.md)\n```\n")
        self.write("CLAUDE.md", "[ok](docs/a.md#real) [no](docs/a.md#fake-heading)\n")
        self.assertEqual(
            self.problems(), ["CLAUDE.md:1: no heading for anchor docs/a.md#fake-heading"]
        )

    def test_agent_docs_name_existing_scripts_and_make_targets(self):
        self.write(
            "CLAUDE.md",
            "`make test` `make build` `make a` `make b` `scripts/real.py` `scripts/real.py`\n"
            "`make nothing` and `scripts/gone.py` and `make -f x`\n",
        )
        self.assertEqual(
            self.problems(),
            [
                "CLAUDE.md:2: make nothing is not a Makefile target",
                "CLAUDE.md:2: scripts/gone.py does not exist",
            ],
        )

    def test_other_docs_and_archive_may_mention_removed_commands(self):
        self.write("docs/other.md", "`make removed` `scripts/removed.py`\n")
        self.write("docs/archive/old.md", "[x](gone.md) `make removed`\n")
        self.assertEqual(self.problems(), [])

    def test_the_repository_itself_is_consistent(self):
        self.assertEqual(links.check(links.ROOT), [])


if __name__ == "__main__":
    unittest.main()
