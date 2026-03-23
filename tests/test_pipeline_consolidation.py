"""TDD tests for pipeline consolidation and remaining fixes.

Architecture: Unify rework_summary as single pipeline (replaces summarize_rework)
P1 #1: _largest_remainder_pcts must not produce negative percentages
P2 #4: Empty file paths from GitHub API must be filtered
"""

import io
import re
import unittest
from collections import Counter
from contextlib import redirect_stdout

from changeledger.cost import _largest_remainder_pcts

from tests.factories import make_cost_inputs


# ── Architecture: Single rework summary pipeline ─────────────────────


class TestUnifiedReworkSummary(unittest.TestCase):
    """rework_summary must be the ONLY summary function, with normalize support."""

    def test_rework_summary_supports_normalize_false(self):
        """rework_summary(results, normalize=False) counts raw items."""
        from changeledger.models import rework_summary

        results = [
            {"status": "accepted", "normalized_units": 2},
            {"status": "accepted", "normalized_units": 3},
            {"status": "rework", "normalized_units": 5},
            {"status": "fix", "normalized_units": 1},
            {"status": "pending", "normalized_units": 1},
        ]
        s = rework_summary(results, normalize=False)
        self.assertEqual(s["accepted"], 2)
        self.assertEqual(s["rework"], 1)
        self.assertEqual(s["fix"], 1)
        self.assertEqual(s["pending"], 1)
        self.assertEqual(s["total_classifiable"], 4)
        self.assertAlmostEqual(s["rework_rate"], 50.0)

    def test_rework_summary_supports_normalize_true(self):
        """rework_summary(results, normalize=True) sums normalized_units."""
        from changeledger.models import rework_summary

        results = [
            {"status": "accepted", "normalized_units": 1},
            {"status": "accepted", "normalized_units": 2},
            {"status": "rework", "normalized_units": 3},
            {"status": "fix", "normalized_units": 1},
            {"status": "pending", "normalized_units": 1},
        ]
        s = rework_summary(results, normalize=True)
        # accepted: 1+2=3, rework: 3, fix: 1, pending: 1
        self.assertEqual(s["accepted"], 3)
        self.assertEqual(s["rework"], 3)
        self.assertEqual(s["fix"], 1)
        self.assertEqual(s["pending"], 1)
        self.assertEqual(s["total_classifiable"], 7)
        # rate: (3+1)/7 * 100 = 57.1%
        self.assertAlmostEqual(s["rework_rate"], (4 / 7) * 100, places=1)

    def test_rework_summary_normalize_missing_units_defaults_to_1(self):
        """Backward compat: missing normalized_units treated as 1."""
        from changeledger.models import rework_summary

        results = [
            {"status": "accepted"},
            {"status": "rework"},
        ]
        s = rework_summary(results, normalize=True)
        self.assertEqual(s["accepted"], 1)
        self.assertEqual(s["rework"], 1)

    def test_summarize_rework_removed_from_cost(self):
        """summarize_rework should no longer exist in cost.py."""
        import changeledger.cost as cost_module
        self.assertFalse(hasattr(cost_module, "summarize_rework"),
                         "summarize_rework should be removed from cost.py")

    def test_apply_rework_uses_rework_summary(self):
        """apply_rework_to_cost_data must use rework_summary internally."""
        from changeledger.cost import apply_rework_to_cost_data

        data = make_cost_inputs(merged_prs=0, reverted_prs=0)
        rework_results = [
            {"status": "accepted", "normalized_units": 1},
            {"status": "accepted", "normalized_units": 2},
            {"status": "rework", "normalized_units": 3},
            {"status": "fix", "normalized_units": 1},
        ]
        # normalize=True: accepted=3, rework=3, fix=1
        updated, pending = apply_rework_to_cost_data(data, rework_results, normalize=True)
        self.assertEqual(updated["merged_prs"], 7)  # 3+3+1
        self.assertEqual(updated["reverted_prs"], 4)  # 3+1

        # normalize=False: accepted=2, rework=1, fix=1
        updated2, _ = apply_rework_to_cost_data(data, rework_results, normalize=False)
        self.assertEqual(updated2["merged_prs"], 4)  # 2+1+1
        self.assertEqual(updated2["reverted_prs"], 2)  # 1+1

    def test_print_report_uses_rework_summary(self):
        """print_report must use rework_summary, not its own Counter."""
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

        # Extract rate from terminal
        m = re.search(r"Rework rate: ([\d.]+)%", buf.getvalue())
        self.assertIsNotNone(m)
        self.assertAlmostEqual(float(m.group(1)), summary["rework_rate"], places=1)


# ── P1 #1: No negative percentages ──────────────────────────────────


class TestNoNegativePercentages(unittest.TestCase):
    """_largest_remainder_pcts must never produce negative values."""

    def test_negative_residual_skips_zero_buckets(self):
        """When a bucket is already 0, subtracting should skip it."""
        # Force a scenario: one tiny value among large ones
        # The tiny value's floored tenths will be 0
        result = _largest_remainder_pcts(
            ["big1", "big2", "big3", "big4", "tiny"],
            [250.0, 250.0, 250.0, 249.99, 0.01],
            1000.0,
        )
        for k, v in result.items():
            self.assertGreaterEqual(v, 0.0, f"{k} = {v} is negative")
        self.assertAlmostEqual(sum(result.values()), 100.0, places=1)

    def test_fuzz_no_negative_percentages(self):
        """500-iteration fuzz: no individual percentage should be negative."""
        import random
        random.seed(42)
        for trial in range(500):
            n = random.randint(2, 8)
            values = [random.uniform(0.0001, 10000) for _ in range(n)]
            total = sum(values)
            keys = [f"k{i}" for i in range(n)]
            result = _largest_remainder_pcts(keys, values, total)
            for k, v in result.items():
                self.assertGreaterEqual(v, 0.0,
                    f"Trial {trial}: {k}={v} negative, values={values}")
            self.assertAlmostEqual(sum(result.values()), 100.0, places=1,
                msg=f"Trial {trial}: sum={sum(result.values())}")


# ── P2 #4: Empty file paths filtered ────────────────────────────────


class TestEmptyFilePathsFiltered(unittest.TestCase):
    """GitHub API file entries with missing 'path' must not pollute file sets."""

    def test_empty_path_excluded_from_commit_files(self):
        """Commit.build should not include empty strings in files."""
        from changeledger.models import Commit
        from datetime import datetime, timezone

        c = Commit.build(
            sha="a" * 40,
            date=datetime.now(timezone.utc),
            subject="test",
            body="",
            files={"src/app.py", "", ""},  # empty strings from API
        )
        self.assertNotIn("", c.files)
        self.assertNotIn("", c.src_files)
        self.assertEqual(len(c.files), 1)  # only src/app.py

    def test_github_merges_filter_empty_paths(self):
        """get_merges_github must not pass empty paths to Commit.build."""
        import json
        import subprocess
        from unittest import mock
        from changeledger.rework import get_merges_github

        pr = {
            "number": 1,
            "title": "Test PR",
            "mergedAt": "2026-03-01T00:00:00Z",
            "files": [{"path": "app.py"}, {"additions": 1}],  # second has no "path"
            "mergeCommit": {"oid": "a" * 40},
            "body": "",
            "additions": 10,
            "deletions": 5,
        }
        completed = subprocess.CompletedProcess(
            args=[], returncode=0, stdout=json.dumps([pr]), stderr="",
        )
        with mock.patch("changeledger.rework.subprocess.run", return_value=completed):
            commits = get_merges_github("owner/repo", lookback_days=30)

        self.assertEqual(len(commits), 1)
        self.assertNotIn("", commits[0].files)


if __name__ == "__main__":
    unittest.main()
