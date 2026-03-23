"""TDD tests for P1 fixes from third audit round.

#3: Rework rate single source of truth (rework_summary)
#1: _largest_remainder_pcts symmetric residual handling
#2: sha_in_text word-boundary check
#4: detect_repo_info strips credentials from URLs
"""

import io
import re
import unittest
from collections import Counter
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from unittest import mock

from changeledger.cost import _largest_remainder_pcts
from changeledger.report import generate_warnings

from tests.factories import make_commit


# ── #3: Rework rate single source of truth ───────────────────────────


class TestReworkRateSingleSource(unittest.TestCase):
    """Terminal and HTML report must use the same rework rate."""

    def test_rework_summary_returns_canonical_rate(self):
        """rework_summary computes the canonical rate excluding pending."""
        from changeledger.models import rework_summary

        results = [
            {"status": "accepted"},
            {"status": "accepted"},
            {"status": "rework", "signals": ["Reverted"]},
            {"status": "fix", "signals": []},
            {"status": "pending"},
        ]
        summary = rework_summary(results)
        # Canonical: (rework + fix) / (accepted + rework + fix) = 2/4 = 50%
        self.assertAlmostEqual(summary["rework_rate"], 50.0)
        self.assertEqual(summary["accepted"], 2)
        self.assertEqual(summary["rework"], 1)
        self.assertEqual(summary["fix"], 1)
        self.assertEqual(summary["pending"], 1)
        self.assertEqual(summary["total_classifiable"], 4)

    def test_rework_summary_no_classifiable(self):
        """When all are pending, rate should be None."""
        from changeledger.models import rework_summary

        results = [{"status": "pending"}, {"status": "pending"}]
        summary = rework_summary(results)
        self.assertIsNone(summary["rework_rate"])

    def test_generate_warnings_uses_rework_summary(self):
        """generate_warnings should use rework_summary rate, not merged_prs denominator."""
        # 3 classifiable (2 accepted, 1 rework) + 7 pending = 10 merged
        # Old buggy rate: 1/10 = 10% (below threshold, no warning)
        # Correct rate: 1/3 = 33% (above 15% threshold, should warn)
        cost_result = {
            "merged_prs": 10,
            "reverted_prs": 1,
        }
        rework_items = [
            {"status": "accepted", "sha": "a", "subject": "a", "signals": [], "files_changed": 1},
            {"status": "accepted", "sha": "b", "subject": "b", "signals": [], "files_changed": 1},
            {"status": "rework", "sha": "c", "subject": "c", "signals": ["Reverted"], "files_changed": 1},
            *[{"status": "pending", "sha": f"p{i}", "subject": "p", "signals": [], "files_changed": 1} for i in range(7)],
        ]
        warnings = generate_warnings(cost_result, rework_items)
        rate_warnings = [w for w in warnings if "Rework rate" in w["title"]]
        self.assertEqual(len(rate_warnings), 1, "Should flag 33% rework rate")
        self.assertIn("33", rate_warnings[0]["title"])

    def test_print_report_and_warnings_agree(self):
        """Terminal and HTML rework rates must match."""
        from changeledger.models import rework_summary
        from changeledger.rework import print_report

        results = [
            {"status": "accepted", "sha": "a", "date": "2026-01-01", "subject": "a", "signals": []},
            {"status": "rework", "sha": "b", "date": "2026-01-02", "subject": "b", "signals": ["Rev"]},
            {"status": "fix", "sha": "c", "date": "2026-01-03", "subject": "c", "signals": []},
            {"status": "pending", "sha": "d", "date": "2026-01-04", "subject": "d", "signals": []},
        ]
        summary = rework_summary(results)

        buf = io.StringIO()
        with redirect_stdout(buf):
            print_report(results, 14)

        # Extract rate from terminal output
        terminal_output = buf.getvalue()
        m = re.search(r"Rework rate: ([\d.]+)%", terminal_output)
        self.assertIsNotNone(m)
        terminal_rate = float(m.group(1))
        self.assertAlmostEqual(terminal_rate, summary["rework_rate"], places=1)


# ── #1: _largest_remainder_pcts symmetric residual ───────────────────


class TestLargestRemainderSymmetric(unittest.TestCase):
    """Percentages must sum to exactly 100.0 even with adversarial floats."""

    def test_percentages_always_sum_to_100(self):
        """Fuzz test: random values must always produce exactly 100.0%."""
        import random
        random.seed(42)
        for _ in range(500):
            values = [random.uniform(0.001, 10000) for _ in range(5)]
            total = sum(values)
            keys = ["a", "b", "c", "d", "e"]
            result = _largest_remainder_pcts(keys, values, total)
            pct_sum = sum(result.values())
            self.assertAlmostEqual(pct_sum, 100.0, places=1,
                                   msg=f"Got {pct_sum} for values={values}")

    def test_no_negative_percentages(self):
        """No individual percentage should ever be negative."""
        import random
        random.seed(99)
        for _ in range(500):
            n = random.randint(2, 8)
            values = [random.uniform(0.001, 10000) for _ in range(n)]
            total = sum(values)
            keys = [f"k{i}" for i in range(n)]
            result = _largest_remainder_pcts(keys, values, total)
            for k, v in result.items():
                self.assertGreaterEqual(v, 0.0, f"{k} is negative: {v}")

    def test_equal_values_sum_to_100(self):
        """Five equal values should produce 5 x 20.0% = 100.0%."""
        result = _largest_remainder_pcts(
            ["a", "b", "c", "d", "e"],
            [200.0, 200.0, 200.0, 200.0, 200.0],
            1000.0,
        )
        self.assertAlmostEqual(sum(result.values()), 100.0)


# ── #2: sha_in_text word boundary ────────────────────────────────────


class TestShaInTextWordBoundary(unittest.TestCase):
    """sha_in_text must not false-match hex substrings inside longer tokens."""

    def test_no_false_match_inside_uuid(self):
        """7-char prefix inside a UUID should NOT match."""
        c = make_commit(sha="deadbee" + "0" * 33)
        # "deadbee" appears inside this UUID but not as a standalone token
        self.assertFalse(c.sha_in_text("Updated UUID 1deadbee2-abcd-1234-0000-000000000000"))

    def test_no_false_match_inside_longer_hex(self):
        """7-char prefix embedded in a longer hex string should NOT match."""
        c = make_commit(sha="abcdef0" + "1" * 33)
        self.assertFalse(c.sha_in_text("Hash is 99abcdef0123456789"))

    def test_matches_standalone_7_char(self):
        """7-char prefix as standalone word SHOULD match."""
        c = make_commit(sha="abcdef0" + "1" * 33)
        self.assertTrue(c.sha_in_text("Reverted abcdef0 due to bug"))

    def test_matches_full_sha(self):
        """Full 40-char SHA should always match."""
        sha = "abcdef0" + "1" * 33
        c = make_commit(sha=sha)
        self.assertTrue(c.sha_in_text(f"This reverts commit {sha}."))

    def test_matches_10_char(self):
        """10-char short_sha should match."""
        sha = "abcdef0123" + "4" * 30
        c = make_commit(sha=sha)
        self.assertTrue(c.sha_in_text(f"Reverted by abcdef0123"))

    def test_synthetic_pr_sha_never_matches(self):
        """PR#N synthetic SHAs should not participate in SHA text matching."""
        c = make_commit(sha="PR#42000000000000000000000000000000000000")
        # Should not match a message that says "PR#42" — that's a coincidence
        self.assertFalse(c.sha_in_text("See PR#42 for context"))


# ── #4: detect_repo_info strips credentials ──────────────────────────


class TestDetectRepoInfoStripsCredentials(unittest.TestCase):
    """Credential-embedded git remote URLs must be sanitized."""

    def test_strips_token_from_https_url(self):
        from changeledger.output import detect_repo_info

        fake_result = mock.Mock()
        fake_result.returncode = 0
        fake_result.stdout = "https://ghp_SECRET123@github.com/owner/repo.git\n"

        with mock.patch("changeledger.output.subprocess.run", return_value=fake_result):
            name, url = detect_repo_info()

        self.assertEqual(name, "owner/repo")
        self.assertNotIn("ghp_SECRET123", url)
        self.assertNotIn("@", url)
        self.assertEqual(url, "https://github.com/owner/repo")

    def test_strips_x_access_token(self):
        from changeledger.output import detect_repo_info

        fake_result = mock.Mock()
        fake_result.returncode = 0
        fake_result.stdout = "https://x-access-token:ghs_ABCDEF@github.com/org/repo.git\n"

        with mock.patch("changeledger.output.subprocess.run", return_value=fake_result):
            name, url = detect_repo_info()

        self.assertNotIn("ghs_ABCDEF", url)
        self.assertNotIn("x-access-token", url)
        self.assertEqual(url, "https://github.com/org/repo")

    def test_clean_url_unchanged(self):
        from changeledger.output import detect_repo_info

        fake_result = mock.Mock()
        fake_result.returncode = 0
        fake_result.stdout = "https://github.com/owner/repo.git\n"

        with mock.patch("changeledger.output.subprocess.run", return_value=fake_result):
            name, url = detect_repo_info()

        self.assertEqual(name, "owner/repo")
        self.assertEqual(url, "https://github.com/owner/repo")

    def test_ssh_url_unaffected(self):
        from changeledger.output import detect_repo_info

        fake_result = mock.Mock()
        fake_result.returncode = 0
        fake_result.stdout = "git@github.com:owner/repo.git\n"

        with mock.patch("changeledger.output.subprocess.run", return_value=fake_result):
            name, url = detect_repo_info()

        self.assertEqual(name, "owner/repo")
        self.assertEqual(url, "https://github.com/owner/repo")


if __name__ == "__main__":
    unittest.main()
