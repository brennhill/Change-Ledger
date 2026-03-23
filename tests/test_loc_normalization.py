"""Tests for LOC normalization feature.

Written before implementation (TDD) — all tests should fail initially,
then pass after the feature is implemented.
"""

import math
import unittest
from datetime import datetime, timedelta, timezone

from changeledger.cost import apply_rework_to_cost_data
from changeledger.models import rework_summary
from changeledger.rework import detect_rework

from tests.factories import make_commit


class TestNormalizedUnitsComputation(unittest.TestCase):
    """Test the normalized_units = max(1, ceil(lines_changed / 500)) formula."""

    def test_normalized_units_small_pr(self):
        """200 LOC PR = 1 unit."""
        units = max(1, math.ceil(200 / 500))
        self.assertEqual(units, 1)

        # Also verify it appears in rework results
        now = datetime.now(timezone.utc)
        old = now - timedelta(days=30)
        from changeledger.models import Commit
        c = Commit.build(
            sha="a" * 40, date=old, subject="small change",
            body="", files={"src/app.py"}, lines_changed=200,
        )
        results = detect_rework([c], window_days=14)
        self.assertEqual(results[0]["normalized_units"], 1)
        self.assertEqual(results[0]["lines_changed"], 200)

    def test_normalized_units_large_pr(self):
        """800 LOC PR = 2 units."""
        units = max(1, math.ceil(800 / 500))
        self.assertEqual(units, 2)

        now = datetime.now(timezone.utc)
        old = now - timedelta(days=30)
        from changeledger.models import Commit
        c = Commit.build(
            sha="b" * 40, date=old, subject="large change",
            body="", files={"src/app.py"}, lines_changed=800,
        )
        results = detect_rework([c], window_days=14)
        self.assertEqual(results[0]["normalized_units"], 2)

    def test_normalized_units_zero_loc(self):
        """0 LOC PR = 1 unit (floor at 1)."""
        units = max(1, math.ceil(0 / 500))
        self.assertEqual(units, 1)

        now = datetime.now(timezone.utc)
        old = now - timedelta(days=30)
        from changeledger.models import Commit
        c = Commit.build(
            sha="c" * 40, date=old, subject="empty change",
            body="", files={"src/app.py"}, lines_changed=0,
        )
        results = detect_rework([c], window_days=14)
        self.assertEqual(results[0]["normalized_units"], 1)

    def test_normalized_units_exact_boundary(self):
        """500 LOC = 1 unit, 501 LOC = 2 units."""
        self.assertEqual(max(1, math.ceil(500 / 500)), 1)
        self.assertEqual(max(1, math.ceil(501 / 500)), 2)

        now = datetime.now(timezone.utc)
        old = now - timedelta(days=30)
        from changeledger.models import Commit

        c500 = Commit.build(
            sha="d" * 40, date=old, subject="boundary 500",
            body="", files={"src/app.py"}, lines_changed=500,
        )

        c501 = Commit.build(
            sha="e" * 40, date=old - timedelta(seconds=1), subject="boundary 501",
            body="", files={"src/app.py"}, lines_changed=501,
        )

        results = detect_rework([c500, c501], window_days=14)
        by_sha = {r["sha"]: r for r in results}
        self.assertEqual(by_sha["d" * 10]["normalized_units"], 1)
        self.assertEqual(by_sha["e" * 10]["normalized_units"], 2)

    def test_normalized_units_1500_loc(self):
        """1500 LOC PR = 3 units."""
        now = datetime.now(timezone.utc)
        old = now - timedelta(days=30)
        from changeledger.models import Commit
        c = Commit.build(
            sha="f" * 40, date=old, subject="huge change",
            body="", files={"src/app.py"}, lines_changed=1500,
        )
        results = detect_rework([c], window_days=14)
        self.assertEqual(results[0]["normalized_units"], 3)


class TestReworkResultIncludesLinesChanged(unittest.TestCase):
    """Rework results must include lines_changed and normalized_units fields."""

    def test_rework_result_includes_lines_changed(self):
        now = datetime.now(timezone.utc)
        old = now - timedelta(days=30)
        from changeledger.models import Commit
        c = Commit.build(
            sha="a" * 40, date=old, subject="change with LOC",
            body="", files={"src/app.py"}, lines_changed=350,
        )
        results = detect_rework([c], window_days=14)

        self.assertIn("lines_changed", results[0])
        self.assertIn("normalized_units", results[0])
        self.assertEqual(results[0]["lines_changed"], 350)
        self.assertEqual(results[0]["normalized_units"], 1)


class TestSummarizeReworkWithNormalization(unittest.TestCase):
    """summarize_rework with normalize=True sums normalized_units."""

    def test_rework_summary_with_normalization(self):
        rework_results = [
            {"status": "accepted", "lines_changed": 200, "normalized_units": 1},
            {"status": "accepted", "lines_changed": 800, "normalized_units": 2},
            {"status": "rework", "lines_changed": 1500, "normalized_units": 3},
            {"status": "fix", "lines_changed": 100, "normalized_units": 1},
            {"status": "pending", "lines_changed": 300, "normalized_units": 1},
        ]

        # Without normalization: counts are 2, 1, 1, 1
        s = rework_summary(rework_results)
        self.assertEqual(s["accepted"], 2)
        self.assertEqual(s["rework"], 1)
        self.assertEqual(s["fix"], 1)
        self.assertEqual(s["pending"], 1)

        # With normalization: sums are 3 (1+2), 3, 1, 1
        s_n = rework_summary(rework_results, normalize=True)
        self.assertEqual(s_n["accepted"], 3)  # 1 + 2
        self.assertEqual(s_n["rework"], 3)    # one rework PR with 1500 LOC = 3 units
        self.assertEqual(s_n["fix"], 1)       # 100 LOC = 1 unit
        self.assertEqual(s_n["pending"], 1)   # 300 LOC = 1 unit


class TestNoNormalizeFlag(unittest.TestCase):
    """--no-normalize preserves raw PR counts."""

    def test_no_normalize_flag_apply_rework(self):
        """apply_rework_to_cost_data with normalize=False uses raw counts."""
        data = {
            "model_cost": 1000,
            "infra_cost": 0,
            "prompting_hours": 0,
            "review_hours": 0,
            "rework_hours": 0,
            "burdened_rate": 1,
            "merged_prs": 0,
            "reverted_prs": 0,
        }
        rework_results = [
            {"status": "accepted", "lines_changed": 200, "normalized_units": 1,
             "sha": "a", "subject": "a", "signals": []},
            {"status": "accepted", "lines_changed": 800, "normalized_units": 2,
             "sha": "b", "subject": "b", "signals": []},
            {"status": "rework", "lines_changed": 1500, "normalized_units": 3,
             "sha": "c", "subject": "c", "signals": ["Reverted"]},
            {"status": "fix", "lines_changed": 100, "normalized_units": 1,
             "sha": "d", "subject": "d", "signals": []},
        ]

        # normalize=False: raw counts
        data_raw, _pending_raw = apply_rework_to_cost_data(
            data, rework_results, normalize=False
        )
        # 2 accepted + 1 rework + 1 fix = 4 merged, 1+1=2 reverted
        self.assertEqual(data_raw["merged_prs"], 4)
        self.assertEqual(data_raw["reverted_prs"], 2)

        # normalize=True (default): use normalized_units
        data_norm, _pending_norm = apply_rework_to_cost_data(
            data, rework_results, normalize=True
        )
        # accepted: 1+2=3, rework: 3, fix: 1 => merged=3+3+1=7, reverted=3+1=4
        self.assertEqual(data_norm["merged_prs"], 7)
        self.assertEqual(data_norm["reverted_prs"], 4)


class TestBackwardCompatibility(unittest.TestCase):
    """Rework results without lines_changed should still work."""

    def test_missing_lines_changed_defaults_to_raw_counts(self):
        """When normalized_units is absent, rework_summary with normalize=True
        treats each PR as 1 unit."""
        rework_results = [
            {"status": "accepted"},
            {"status": "accepted"},
            {"status": "rework"},
        ]
        s = rework_summary(rework_results, normalize=True)
        self.assertEqual(s["accepted"], 2)
        self.assertEqual(s["rework"], 1)

    def test_apply_rework_missing_normalized_units(self):
        """apply_rework_to_cost_data handles missing normalized_units gracefully."""
        data = {
            "model_cost": 1,
            "infra_cost": 0,
            "prompting_hours": 0,
            "review_hours": 0,
            "rework_hours": 0,
            "burdened_rate": 1,
            "merged_prs": 0,
            "reverted_prs": 0,
        }
        rework_results = [
            {"status": "accepted", "sha": "a", "subject": "a", "signals": []},
            {"status": "rework", "sha": "b", "subject": "b", "signals": ["Reverted"]},
        ]
        # Should not raise even with normalize=True
        updated, _pending = apply_rework_to_cost_data(data, rework_results, normalize=True)
        self.assertEqual(updated["merged_prs"], 2)
        self.assertEqual(updated["reverted_prs"], 1)


class TestCommitLinesChanged(unittest.TestCase):
    """Commit dataclass should have lines_changed field."""

    def test_commit_has_lines_changed(self):
        from changeledger.models import Commit
        c = Commit.build(
            sha="a" * 40,
            date=datetime.now(timezone.utc),
            subject="test",
            body="",
            files={"src/app.py"},
            lines_changed=350,
        )
        self.assertEqual(c.lines_changed, 350)

    def test_commit_lines_changed_defaults_to_zero(self):
        from changeledger.models import Commit
        c = Commit.build(
            sha="a" * 40,
            date=datetime.now(timezone.utc),
            subject="test",
            body="",
            files={"src/app.py"},
        )
        self.assertEqual(c.lines_changed, 0)


if __name__ == "__main__":
    unittest.main()
