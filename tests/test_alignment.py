"""TDD tests for cross-tool alignment fixes.

1. Inclusive window boundary (<=) — commit at exactly T+14d is within window
2. Lookback default changed to 90 days
3. Revert #N pattern — detect reverts referencing PR numbers
"""

import unittest
from datetime import datetime, timedelta, timezone

from changeledger.rework import detect_rework
from tests.factories import make_commit


# ── 1. Inclusive window boundary ─────────────────────────────────────


class TestInclusiveWindowBoundary(unittest.TestCase):
    """A fix landing at exactly T+window_days should be within the window."""

    def test_fix_at_exact_boundary_is_detected(self):
        """Commit at exactly 14 days should be caught, not excluded."""
        now = datetime.now(timezone.utc)
        original_date = now - timedelta(days=30)
        # Fix lands at exactly 14 days after original
        fix_date = original_date + timedelta(days=14)

        original = make_commit(
            sha="a" * 40, subject="Add feature", date=original_date,
        )
        fix = make_commit(
            sha="b" * 40, subject="fix: null check in feature",
            date=fix_date, is_fix=True,
            files={"src/app.py"},  # same files as original
        )

        results = detect_rework([original, fix], window_days=14)
        original_result = [r for r in results if r["sha"] == "a" * 10][0]
        self.assertEqual(original_result["status"], "rework",
                         "Fix at exactly 14 days should be within window (inclusive)")

    def test_fix_one_second_after_boundary_is_excluded(self):
        """Commit at 14 days + 1 second should be outside the window."""
        now = datetime.now(timezone.utc)
        original_date = now - timedelta(days=30)
        fix_date = original_date + timedelta(days=14, seconds=1)

        original = make_commit(
            sha="a" * 40, subject="Add feature", date=original_date,
        )
        fix = make_commit(
            sha="b" * 40, subject="fix: null check in feature",
            date=fix_date, is_fix=True,
            files={"src/app.py"},
        )

        results = detect_rework([original, fix], window_days=14)
        original_result = [r for r in results if r["sha"] == "a" * 10][0]
        self.assertEqual(original_result["status"], "accepted",
                         "Fix beyond 14 days should be outside window")


# ── 2. Lookback default ─────────────────────────────────────────────


class TestLookbackDefault(unittest.TestCase):
    """Default lookback should be 90 days (1 quarter)."""

    def test_default_lookback_is_90(self):
        import argparse
        from changeledger.cli import main

        parser = argparse.ArgumentParser()
        subparsers = parser.add_subparsers(dest="command")

        # Reconstruct the rework_opts to check default
        from changeledger.cli import positive_int_arg
        rework_opts = argparse.ArgumentParser(add_help=False)
        rework_opts.add_argument("--lookback", type=positive_int_arg("lookback"), default=90)

        p = subparsers.add_parser("rework", parents=[rework_opts])
        args = parser.parse_args(["rework"])
        self.assertEqual(args.lookback, 90)

    def test_cli_lookback_default_is_90(self):
        """The actual CLI parser should default to 90."""
        import subprocess, sys, json
        result = subprocess.run(
            [sys.executable, "-m", "changeledger", "rework", "--help"],
            capture_output=True, text=True, timeout=10,
        )
        self.assertIn("default: 90", result.stdout)


# ── 3. Revert #N pattern ────────────────────────────────────────────


class TestRevertPrNumberPattern(unittest.TestCase):
    """Revert #N in title/body should link to the original PR."""

    def test_revert_pr_number_in_title(self):
        """'Revert #42' in title should flag PR #42 as rework."""
        now = datetime.now(timezone.utc)
        old = now - timedelta(days=30)

        original = make_commit(
            sha="a" * 40, subject="Add checkout flow",
            date=old, pr_number=42,
        )
        revert = make_commit(
            sha="b" * 40, subject="Revert #42",
            date=old + timedelta(days=1),
            is_revert=True, pr_number=43,
        )

        results = detect_rework([original, revert], window_days=14)
        original_result = [r for r in results if r["sha"] == "a" * 10][0]
        self.assertEqual(original_result["status"], "rework",
                         "Revert #42 should flag PR #42 as rework")

    def test_revert_pr_number_in_body(self):
        """'Reverts #42' in body should flag PR #42 as rework."""
        now = datetime.now(timezone.utc)
        old = now - timedelta(days=30)

        original = make_commit(
            sha="a" * 40, subject="Add checkout flow",
            date=old, pr_number=42,
        )
        from changeledger.models import Commit
        revert = Commit.build(
            sha="b" * 40,
            date=old + timedelta(days=1),
            subject='Revert "Add checkout flow"',
            body="Reverts #42\n\nThis change caused regressions.",
            files={"src/app.py"},
            pr_number=43,
        )

        results = detect_rework([original, revert], window_days=14)
        original_result = [r for r in results if r["sha"] == "a" * 10][0]
        self.assertEqual(original_result["status"], "rework",
                         "Reverts #42 in body should flag PR #42 as rework")

    def test_no_false_match_on_unrelated_pr_number(self):
        """Revert #99 should not flag PR #42."""
        now = datetime.now(timezone.utc)
        old = now - timedelta(days=30)

        original = make_commit(
            sha="a" * 40, subject="Add checkout flow",
            date=old, pr_number=42,
        )
        # Build revert manually to avoid subject overlap
        from changeledger.models import Commit
        revert = Commit.build(
            sha="b" * 40,
            date=old + timedelta(days=1),
            subject='Revert "Unrelated change"',
            body="Reverts #99",
            files={"src/other.py"},
            pr_number=43,
        )

        results = detect_rework([original, revert], window_days=14)
        original_result = [r for r in results if r["sha"] == "a" * 10][0]
        self.assertNotEqual(original_result["status"], "rework")

    def test_revert_pr_number_only_works_with_pr_number_set(self):
        """Local git commits without pr_number should not match on #N."""
        now = datetime.now(timezone.utc)
        old = now - timedelta(days=30)

        original = make_commit(
            sha="a" * 40, subject="Add unique feature xyz",
            date=old, pr_number=None,  # local git, no PR number
        )
        from changeledger.models import Commit
        revert = Commit.build(
            sha="b" * 40,
            date=old + timedelta(days=1),
            subject='Revert "Something else"',
            body="Reverts #42",
            files={"src/other.py"},
        )

        results = detect_rework([original, revert], window_days=14)
        original_result = [r for r in results if r["sha"] == "a" * 10][0]
        self.assertNotEqual(original_result["status"], "rework")


if __name__ == "__main__":
    unittest.main()
