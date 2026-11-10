"""compose.prod.yaml keeps bss off vps-infra's `shared` network (stdlib only).

The file is read as text: services are the two-space-indented keys under `services:`.
"""

import re
import unittest
from pathlib import Path

TEXT = (Path(__file__).resolve().parent.parent / "compose.prod.yaml").read_text()
CODE = "\n".join(line for line in TEXT.splitlines() if not line.lstrip().startswith("#"))


def block(name: str) -> str:
    m = re.search(rf"^  {name}:\n((?:(?:    .*)?\n)+)", CODE, re.M)
    assert m, f"service {name} not found"
    return m.group(1)


def networks_of(service: str) -> dict[str, list[str]]:
    """network -> aliases for one service"""
    body = block(service)
    m = re.search(r"^    networks:(.*)\n((?:      .*\n)*)", body + "\n", re.M)
    assert m, f"{service} has no networks"
    inline = m.group(1).strip()
    if inline.startswith("["):
        return {n.strip(): [] for n in inline.strip("[]").split(",") if n.strip()}
    result: dict[str, list[str]] = {}
    current = None
    for line in m.group(2).splitlines():
        top = re.match(r"^      (\S+):(.*)$", line)
        if top:
            current = top.group(1)
            result[current] = []
            alias = re.search(r"aliases:\s*\[(.*)\]", top.group(2))
            if alias:
                result[current] = [a.strip() for a in alias.group(1).split(",")]
        elif current:
            alias = re.search(r"aliases:\s*\[(.*)\]", line)
            if alias:
                result[current] = [a.strip() for a in alias.group(1).split(",")]
    return result


class ComposeProd(unittest.TestCase):
    def test_no_service_on_shared(self):
        for svc in ("app", "worker", "mcp"):
            self.assertNotIn("shared", networks_of(svc), svc)

    def test_top_level_networks_are_exactly_the_two_external(self):
        m = re.search(r"^networks:\n((?:  .*\n|\n)*)", CODE + "\n", re.M)
        self.assertTrue(m)
        names = re.findall(r"^  (\S+):\s*$", m.group(1), re.M)
        self.assertEqual(sorted(names), ["bss-db", "bss-edge"])
        for n in names:
            sect = re.search(rf"^  {n}:\n((?:    .*\n)+)", m.group(1), re.M).group(1)
            self.assertIn("external: true", sect)
            self.assertIn(f"name: {n}", sect)

    def test_app(self):
        nets = networks_of("app")
        self.assertEqual(sorted(nets), ["bss-db", "bss-edge"])
        self.assertEqual(nets["bss-edge"], ["bss"])

    def test_mcp(self):
        self.assertEqual(networks_of("mcp"), {"bss-edge": ["bss-mcp"]})

    def test_limits_and_processes_default_to_the_current_values(self):
        """#288: limits and process counts come from the environment, with today's values as
        defaults, so the current VPS keeps exactly what it runs."""
        text = TEXT
        for expected in (
            "cpus: ${APP_CPUS:-0.50}",
            "mem_limit: ${APP_MEM:-384m}",
            "memswap_limit: ${APP_MEM:-384m}",
            "cpus: ${WORKER_CPUS:-0.25}",
            "mem_limit: ${WORKER_MEM:-256m}",
            "memswap_limit: ${WORKER_MEM:-256m}",
            "cpus: ${MCP_CPUS:-0.25}",
            "mem_limit: ${MCP_MEM:-192m}",
            "memswap_limit: ${MCP_MEM:-192m}",
            "WEB_CONCURRENCY: ${WEB_CONCURRENCY:-1}",
            "DB_POOL_SIZE: ${DB_POOL_SIZE:-10}",
        ):
            self.assertIn(expected, text)

    def test_worker(self):
        self.assertEqual(networks_of("worker"), {"bss-db": []})


if __name__ == "__main__":
    unittest.main()
