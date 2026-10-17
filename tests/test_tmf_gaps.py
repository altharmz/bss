"""The TMF gap list (tests/tmf_gaps.py) equals tests/tmf-gaps.txt, line for line.

A closed gap must leave the file, and a new one cannot appear unnoticed. Rule: what the
vendored specs define is implemented (docs/decisions/tmf-api.md, "Following TMF").
"""

from pathlib import Path

from tests import tmf_gaps

GAPS_FILE = Path(__file__).resolve().parent / "tmf-gaps.txt"


def recorded() -> list[str]:
    lines = GAPS_FILE.read_text().splitlines()
    return [line for line in lines if line and not line.startswith("#")]


def test_the_gap_list_is_the_recorded_one(api):
    found = tmf_gaps.gaps(api.client)
    expected = recorded()
    closed = sorted(set(expected) - set(found))
    opened = sorted(set(found) - set(expected))
    assert not closed and not opened, (
        "tests/tmf-gaps.txt is out of date.\n"
        + "".join(f"closed (remove the line): {line}\n" for line in closed)
        + "".join(f"open (not recorded): {line}\n" for line in opened)
    )


def test_the_probe_finds_a_refused_attribute(api):
    # negative control: an attribute nothing defines is reported, at the top and nested
    lines = tmf_gaps.probe_writes(api.client)
    assert not any(line.endswith(f" {tmf_gaps.CONTROL}") for line in lines)
    assert any(" refused " in line for line in lines)


def test_the_recorded_list_is_sorted_and_unique():
    assert recorded() == sorted(set(recorded()))
