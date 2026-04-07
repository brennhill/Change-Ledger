"""Regression tests for vendoring `delivery-gap-signals` into changeledger."""

from __future__ import annotations

import argparse
import importlib
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock


class TestSignalsAreVendored(unittest.TestCase):
    """Signal helpers should not require the external package at import time."""

    def tearDown(self):
        sys.modules.pop("changeledger.signals", None)

    def test_signals_import_without_delivery_gap_signals(self):
        fake_external = types.ModuleType("delivery_gap_signals")
        sys.modules.pop("changeledger.signals", None)

        with mock.patch.dict(sys.modules, {"delivery_gap_signals": fake_external}):
            signals = importlib.import_module("changeledger.signals")

        self.assertEqual(signals.extract_revert_pr_numbers("Reverts #42"), {42})
        self.assertTrue(signals.is_fix_message("fix: patch the checkout flow"))


class TestVendoredFileSource(unittest.TestCase):
    """The local file adapter should parse cached change JSON."""

    def test_fetch_changes_reads_cached_json(self):
        from changeledger.sources.file import fetch_changes

        payload = [
            {
                "id": "42",
                "source": "file",
                "repo": "acme/widgets",
                "title": "feat: add checkout flow",
                "body": "Implements ENG-123\n\nFixes #77",
                "author": "brenn",
                "merged_at": "2026-01-15T10:30:00Z",
                "created_at": "2026-01-10T08:00:00Z",
                "files": ["src/checkout.py", "README.md"],
                "additions": 12,
                "deletions": 3,
                "merge_commit_sha": "abcdef0123456789",
                "pr_number": 42,
            }
        ]

        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as handle:
            json.dump(payload, handle)
            path = Path(handle.name)

        try:
            changes = fetch_changes(str(path))
        finally:
            path.unlink()

        self.assertEqual(len(changes), 1)
        change = changes[0]
        self.assertEqual(change.id, "42")
        self.assertEqual(change.repo, "acme/widgets")
        self.assertEqual(change.pr_number, 42)
        self.assertEqual(change.merge_commit_sha, "abcdef0123456789")
        self.assertEqual(change.ticket_ids, frozenset({"ENG-123", "#77"}))
        self.assertEqual(change.files, ["src/checkout.py", "README.md"])


class TestCliLoadsVendoredSources(unittest.TestCase):
    """CLI source loading should use local vendored adapters."""

    def test_load_changes_uses_vendored_file_adapter(self):
        from changeledger.cli import _load_changes

        args = argparse.Namespace(from_prs="prs.json", source=None, repo=None, lookback=90)
        with mock.patch("changeledger.sources.file.fetch_changes", return_value=["file-change"]) as fetch_changes:
            loaded = _load_changes(args)

        fetch_changes.assert_called_once_with("prs.json")
        self.assertEqual(loaded, ["file-change"])

    def test_load_changes_uses_vendored_graphql_adapter(self):
        from changeledger.cli import _load_changes

        args = argparse.Namespace(from_prs=None, source="graphql", repo="acme/widgets", lookback=30)
        with mock.patch(
            "changeledger.sources.github_graphql.fetch_changes",
            return_value=["graphql-change"],
        ) as fetch_changes:
            loaded = _load_changes(args)

        fetch_changes.assert_called_once_with("acme/widgets", 30)
        self.assertEqual(loaded, ["graphql-change"])


if __name__ == "__main__":
    unittest.main()
