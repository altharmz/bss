"""scripts/smoke-retry.sh: which failed smoke requests are tried again (#257). Stdlib only.

Each case sources the helper in bash and hands it a fake request command that answers
from a list - one line per attempt, `<exit code> <write-out>` - so no network is used.
"""

import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HELPER = ROOT / "scripts/smoke-retry.sh"

# the fake request: the next answer from $ANSWERS, the number of calls in $CALLS
FAKE = r"""
fake() {
  local n
  n=$(( $(cat "$CALLS") + 1 )); echo "$n" > "$CALLS"
  read -r code out < <(sed -n "${n}p" "$ANSWERS")
  printf '%s' "$out"
  return "$code"
}
"""


class SmokeRetry(unittest.TestCase):
    def run_retry(self, method, answers):
        tmp = Path(tempfile.mkdtemp(prefix="bss-smoke-retry-"))
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        (tmp / "answers").write_text("".join(f"{a}\n" for a in answers))
        (tmp / "calls").write_text("0")
        script = f'source "{HELPER}"\n{FAKE}\nsmoke_retry {method} /path fake\n'
        result = subprocess.run(  # noqa: S603 - bash with a test-owned script
            [shutil.which("bash"), "-c", script],
            env={
                "PATH": "/usr/bin:/bin",
                "ANSWERS": str(tmp / "answers"),
                "CALLS": str(tmp / "calls"),
                "SMOKE_RETRY_DELAY": "0",
            },
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.strip(), int((tmp / "calls").read_text()), result.stderr

    def test_an_answer_is_returned_at_once(self):
        self.assertEqual(self.run_retry("GET", ["0 200 0.120"]), ("200", 1, ""))

    def test_an_http_error_status_is_never_retried(self):
        self.assertEqual(self.run_retry("GET", ["0 503 0.120", "0 200 0.1"])[:2], ("503", 1))

    def test_a_get_without_an_answer_is_retried_until_it_answers(self):
        status, calls, log = self.run_retry("GET", ["28 000 0.250", "7 000 0.000000", "0 200 0.1"])
        self.assertEqual((status, calls), ("200", 3))
        self.assertEqual(len(re.findall(r"retry GET /path: no answer", log)), 2)
        self.assertIn("(curl exit 28), attempt 2 of 3", log)

    def test_a_get_gives_up_after_three_attempts(self):
        answers = ["28 000 0.000000"] * 4
        self.assertEqual(self.run_retry("GET", answers)[:2], ("000", 3))

    def test_a_post_that_was_never_sent_is_retried(self):
        status, calls, _ = self.run_retry("POST", ["28 000 0.000000", "0 401 0.2"])
        self.assertEqual((status, calls), ("401", 2))

    def test_a_post_that_may_have_been_sent_is_not_repeated(self):
        status, calls, log = self.run_retry("POST", ["28 000 0.310", "0 200 0.2"])
        self.assertEqual((status, calls, log), ("000", 1, ""))

    def test_both_shell_smokes_send_every_request_through_it(self):
        for name in ("web-smoke.sh", "mcp-smoke.sh"):
            with self.subTest(script=name):
                text = (ROOT / "scripts" / name).read_text()
                self.assertIn('source "$(dirname "$0")/smoke-retry.sh"', text)
                self.assertIn("%{http_code} %{time_pretransfer}", text)
                self.assertEqual(text.count("smoke_retry "), 1)
                # every curl in the script is the one smoke_retry runs
                curls = [line for line in text.splitlines() if re.search(r"\bcurl\b", line)]
                calls = [line for line in curls if not line.lstrip().startswith("#")]
                self.assertTrue(all("smoke_retry" in c or "get_once()" in c for c in calls), calls)


if __name__ == "__main__":
    unittest.main()
