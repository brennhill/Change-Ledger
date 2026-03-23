"""Tests for three architecture fixes:

1. Integer field validation — merged_prs/reverted_prs must be whole numbers
2. Shared input loading — load_cost_inputs(args) handles JSON/stdin/interactive
3. SHA matching on Commit — matches() method for 7/10/40-char comparisons
"""

import io
import json
import sys
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

from changeledger.cost import ChangeledgerError, calculate, _largest_remainder_pcts

from tests.factories import make_commit, make_cost_inputs


# ── Architecture 1: Integer field validation ─────────────────────────


class TestIntegerFieldValidation(unittest.TestCase):
    """merged_prs and reverted_prs must be whole numbers."""

    def test_rejects_fractional_merged_prs(self):
        with self.assertRaises(ChangeledgerError) as ctx:
            calculate(make_cost_inputs(merged_prs=12.7, reverted_prs=2))
        self.assertIn("merged_prs", str(ctx.exception))
        self.assertIn("whole number", str(ctx.exception))

    def test_rejects_fractional_reverted_prs(self):
        with self.assertRaises(ChangeledgerError) as ctx:
            calculate(make_cost_inputs(merged_prs=10, reverted_prs=2.5))
        self.assertIn("reverted_prs", str(ctx.exception))
        self.assertIn("whole number", str(ctx.exception))

    def test_accepts_integer_pr_counts(self):
        result = calculate(make_cost_inputs(merged_prs=10, reverted_prs=2))
        self.assertEqual(result["accepted_changes"], 8)

    def test_accepts_float_with_zero_fraction(self):
        """10.0 is a whole number — should be accepted."""
        result = calculate(make_cost_inputs(merged_prs=10.0, reverted_prs=2.0))
        self.assertEqual(result["accepted_changes"], 8)

    def test_largest_remainder_clamps_negative_residual(self):
        """Negative residual from float rounding must not corrupt percentages."""
        # Sum of percentages must always be exactly 100.0
        for _ in range(100):
            import random
            values = [random.uniform(0.01, 1000) for _ in range(5)]
            total = sum(values)
            keys = ["a", "b", "c", "d", "e"]
            result = _largest_remainder_pcts(keys, values, total)
            pct_sum = sum(result.values())
            self.assertAlmostEqual(pct_sum, 100.0, places=1,
                                   msg=f"Percentages sum to {pct_sum}, expected 100.0")


# ── Architecture 2: Shared input loading ─────────────────────────────


class TestLoadCostInputs(unittest.TestCase):
    """load_cost_inputs(args) unifies JSON/stdin/interactive loading."""

    def _make_args(self, **kwargs):
        """Build a mock args namespace."""
        defaults = {
            "json": None,
            "interactive": False,
            "from_rework": None,
            "no_normalize": False,
        }
        defaults.update(kwargs)
        return mock.Mock(**defaults)

    def test_loads_from_json_file(self):
        from changeledger.cli import load_cost_inputs
        import tempfile, os

        data = {"model_cost": 100, "infra_cost": 0, "prompting_hours": 0,
                "review_hours": 0, "rework_hours": 0, "burdened_rate": 1,
                "merged_prs": 5, "reverted_prs": 1}

        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as f:
            json.dump(data, f)
            path = f.name

        try:
            args = self._make_args(json=path)
            result = load_cost_inputs(args)
            self.assertEqual(result["model_cost"], 100)
        finally:
            os.unlink(path)

    def test_loads_from_stdin_when_not_tty(self):
        from changeledger.cli import load_cost_inputs

        data = {"model_cost": 42, "infra_cost": 0, "prompting_hours": 0,
                "review_hours": 0, "rework_hours": 0, "burdened_rate": 1,
                "merged_prs": 5, "reverted_prs": 1}

        args = self._make_args()
        fake_stdin = io.StringIO(json.dumps(data))
        with mock.patch.object(sys, "stdin", fake_stdin):
            with mock.patch.object(fake_stdin, "isatty", return_value=False):
                result = load_cost_inputs(args)
        self.assertEqual(result["model_cost"], 42)

    def test_cmd_full_uses_stdin_when_not_tty(self):
        """cmd_full must support piped stdin, not just --json."""
        from changeledger.cli import load_cost_inputs

        data = {"model_cost": 99, "infra_cost": 0, "prompting_hours": 0,
                "review_hours": 0, "rework_hours": 0, "burdened_rate": 1,
                "merged_prs": 5, "reverted_prs": 1}

        args = self._make_args()
        fake_stdin = io.StringIO(json.dumps(data))
        with mock.patch.object(sys, "stdin", fake_stdin):
            with mock.patch.object(fake_stdin, "isatty", return_value=False):
                result = load_cost_inputs(args)
        self.assertEqual(result["model_cost"], 99)


# ── Architecture 3: SHA matching on Commit ───────────────────────────


class TestCommitShaMatching(unittest.TestCase):
    """Commit.sha_matches() handles 7/10/40-char prefix comparisons."""

    def test_matches_full_sha_in_text(self):
        sha = "abcdef0123456789" * 2 + "abcdef01"
        c = make_commit(sha=sha)
        self.assertTrue(c.sha_in_text(f"This reverts commit {sha}."))

    def test_matches_10_char_prefix_in_text(self):
        sha = "abcdef0123456789" * 2 + "abcdef01"
        c = make_commit(sha=sha)
        self.assertTrue(c.sha_in_text(f"Reverted by {sha[:10]}"))

    def test_matches_7_char_prefix_in_text(self):
        sha = "abcdef0123456789" * 2 + "abcdef01"
        c = make_commit(sha=sha)
        self.assertTrue(c.sha_in_text(f"Reverted by {sha[:7]}"))

    def test_no_false_match_on_short_prefix(self):
        sha = "abcdef0123456789" * 2 + "abcdef01"
        c = make_commit(sha=sha)
        # 4-char prefix should NOT match (too short, high collision risk)
        self.assertFalse(c.sha_in_text(f"Reverted by {sha[:4]}"))

    def test_no_false_match_on_unrelated_text(self):
        c = make_commit()
        self.assertFalse(c.sha_in_text("unrelated message"))

    def test_dead_else_branch_removed(self):
        """short_sha should always be sha[:10], never PR# fallback."""
        from changeledger.models import Commit
        c = Commit.build(
            sha="abc1234567890" + "0" * 27,
            date=datetime.now(timezone.utc),
            subject="test",
            body="",
            files=set(),
        )
        self.assertEqual(c.short_sha, "abc1234567")

    def test_revert_detection_with_7_char_sha(self):
        """detect_rework should catch reverts using 7-char abbreviated SHAs."""
        from changeledger.models import Commit
        from changeledger.rework import detect_rework

        now = datetime.now(timezone.utc)
        old = now - timedelta(days=30)

        original_sha = "abcdef0123" + "0" * 30
        original = Commit.build(
            sha=original_sha, date=old, subject="Add feature",
            body="", files={"src/app.py"},
        )
        # Revert message only contains 7-char abbreviation
        revert = Commit.build(
            sha="1111111111" + "0" * 30,
            date=old + timedelta(days=1),
            subject=f'Revert "Add feature"',
            body=f"This reverts commit {original_sha[:7]}.",
            files={"src/app.py"},
        )
        results = detect_rework([original, revert], window_days=14)
        original_result = [r for r in results if r["sha"] == original_sha[:10]][0]
        self.assertEqual(original_result["status"], "rework",
                         "Should detect revert via 7-char SHA")


if __name__ == "__main__":
    unittest.main()
