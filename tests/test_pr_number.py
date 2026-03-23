"""TDD tests for PR number in rework results and local git extraction.

1. pr_number flows through detect_rework into result dicts
2. Local git extracts PR number from merge commit subjects
"""

import unittest
from datetime import datetime, timedelta, timezone

from changeledger.rework import detect_rework
from changeledger.signals import extract_pr_number_from_subject
from tests.factories import make_commit


class TestPrNumberInReworkResults(unittest.TestCase):
    """detect_rework must include pr_number in result dicts."""

    def test_pr_number_present_in_results(self):
        now = datetime.now(timezone.utc)
        old = now - timedelta(days=30)
        c = make_commit(sha="a" * 40, date=old, pr_number=42)
        results = detect_rework([c], window_days=14)
        self.assertEqual(results[0]["pr_number"], 42)

    def test_pr_number_none_for_local_git(self):
        now = datetime.now(timezone.utc)
        old = now - timedelta(days=30)
        c = make_commit(sha="a" * 40, date=old, pr_number=None)
        results = detect_rework([c], window_days=14)
        self.assertIsNone(results[0]["pr_number"])


class TestExtractPrNumberFromSubject(unittest.TestCase):
    """Extract PR numbers from local git merge commit subjects."""

    def test_merge_pull_request(self):
        """Standard GitHub merge commit."""
        self.assertEqual(
            extract_pr_number_from_subject("Merge pull request #42 from owner/branch"),
            42,
        )

    def test_squash_merge_suffix(self):
        """GitHub squash merge appends (#N)."""
        self.assertEqual(
            extract_pr_number_from_subject("feat: add checkout flow (#123)"),
            123,
        )

    def test_no_pr_number(self):
        """Regular commit without PR reference."""
        self.assertIsNone(
            extract_pr_number_from_subject("fix: null pointer in checkout"),
        )

    def test_hash_in_middle_not_matched(self):
        """Bare #N in the middle of a subject is not a PR number."""
        # "fixes #42" is a ticket reference, not a PR merge indicator
        self.assertIsNone(
            extract_pr_number_from_subject("fixes #42 in checkout"),
        )

    def test_merge_pr_with_large_number(self):
        self.assertEqual(
            extract_pr_number_from_subject("Merge pull request #9999 from org/feat"),
            9999,
        )

    def test_squash_merge_with_spaces(self):
        """Parenthesized PR number at end with trailing whitespace."""
        self.assertEqual(
            extract_pr_number_from_subject("chore: update deps (#77) "),
            77,
        )


class TestLocalGitPrNumberExtraction(unittest.TestCase):
    """get_merges_local should extract pr_number from subjects."""

    def test_commit_build_with_extracted_pr_number(self):
        """Commit.build receives pr_number extracted from subject."""
        from changeledger.models import Commit
        # Simulate what get_merges_local would do
        subject = "Merge pull request #42 from owner/branch"
        pr_num = extract_pr_number_from_subject(subject)
        c = Commit.build(
            sha="a" * 40,
            date=datetime.now(timezone.utc),
            subject=subject,
            body="",
            files={"src/app.py"},
            pr_number=pr_num,
        )
        self.assertEqual(c.pr_number, 42)


if __name__ == "__main__":
    unittest.main()
