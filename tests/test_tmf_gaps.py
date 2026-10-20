"""The TMF gap list (tests/tmf_gaps.py) equals tests/tmf-gaps.txt, line for line.

A closed gap must leave the file, and a new one cannot appear unnoticed. Rule: what the
vendored specs define is implemented (docs/decisions/tmf-api.md, "Following TMF").
The file's `# accepted deviations` section holds the gaps kept on purpose, each with its
reason; the probe still finds them, so they are locked the same way.
"""

from pathlib import Path

from tests import apidata, tmf_gaps

GAPS_FILE = Path(__file__).resolve().parent / "tmf-gaps.txt"
DEVIATIONS = "# accepted deviations"


def sections() -> tuple[list[str], list[str]]:
    """(open gaps, accepted deviations), comments and blank lines left out."""
    gaps: list[str] = []
    deviations: list[str] = []
    target = gaps
    for line in GAPS_FILE.read_text().splitlines():
        if line.startswith(DEVIATIONS):
            target = deviations
        elif line and not line.startswith("#"):
            target.append(line)
    return gaps, deviations


def recorded() -> list[str]:
    gaps, deviations = sections()
    return gaps + deviations


def test_the_gap_list_is_the_recorded_one(api):
    found = tmf_gaps.gaps(api)
    expected = recorded()
    closed = sorted(set(expected) - set(found))
    opened = sorted(set(found) - set(expected))
    assert not closed and not opened, (
        "tests/tmf-gaps.txt is out of date.\n"
        + "".join(f"closed (remove the line): {line}\n" for line in closed)
        + "".join(f"open (not recorded): {line}\n" for line in opened)
    )


def with_control(monkeypatch) -> None:
    """Make every spec schema define the control attribute, which nothing implements."""
    properties = tmf_gaps.properties

    def defining_control(api, schema, seen=frozenset()):
        found = properties(api, schema, seen)
        return {**found, tmf_gaps.CONTROL: {"type": "string"}} if found else found

    monkeypatch.setattr(tmf_gaps, "properties", defining_control)


def test_the_probe_finds_a_refused_attribute(api, monkeypatch):
    # negative control: an attribute the specs defined but the app refuses is reported,
    # at the top and nested. One operation is enough: the probe is the same for every
    # one, and the full probe never reports the control (it is not in tmf-gaps.txt, so
    # test_the_gap_list_is_the_recorded_one would fail).
    only = frozenset({"TMF676 POST /payment"})
    lines = tmf_gaps.probe_writes(api.client, only=only)
    assert not any(line.endswith(tmf_gaps.CONTROL) for line in lines)
    with_control(monkeypatch)
    lines = tmf_gaps.probe_writes(api.client, only=only)
    assert "TMF676 POST /payment refused zzProbe" in lines
    assert "TMF676 POST /payment refused paymentMethod.zzProbe" in lines


def test_the_probe_finds_a_missing_nested_attribute(api, monkeypatch):
    # negative control: a nested attribute the specs defined and no response carries
    apidata.account(api)
    only = frozenset({"billingAccount"})
    # as the write probe finds it: POST compares accountBalance, it does not store it
    server_set = {"TMF666 POST /billingAccount accountBalance"}
    line = "TMF666 GET /billingAccount/{id} missing accountBalance.zzProbe"
    assert line not in tmf_gaps.probe_nested_reads(api.client, [], server_set, only)
    with_control(monkeypatch)
    assert line in tmf_gaps.probe_nested_reads(api.client, [], server_set, only)


def test_the_recorded_list_is_sorted_and_unique():
    for section in sections():
        assert section == sorted(set(section))
    gaps, deviations = sections()
    assert not set(gaps) & set(deviations)
