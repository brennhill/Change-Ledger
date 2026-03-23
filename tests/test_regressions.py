import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from changeledger.cost import ChangeledgerError, calculate, load_rework_data
from changeledger.report import generate_html
from changeledger.rework import get_merges_github, get_merges_local, print_report

from tests.factories import make_cost_inputs

REPO_ROOT = Path(__file__).resolve().parents[1]


class CostRegressionTests(unittest.TestCase):
    def test_calculate_preserves_direct_currency_symbols(self):
        for symbol in ("€", "£", "R$"):
            with self.subTest(symbol=symbol):
                result = calculate(make_cost_inputs(currency=symbol))
                self.assertEqual(result["currency"], symbol)

    def test_calculate_rejects_non_numeric_fields_with_changeledger_error(self):
        with self.assertRaises(ChangeledgerError) as ctx:
            calculate(make_cost_inputs(model_cost="1"))

        self.assertIn("model_cost", str(ctx.exception))
        self.assertIn("must be a number", str(ctx.exception))


class ReworkRegressionTests(unittest.TestCase):
    def test_get_merges_local_preserves_subject_body_and_files(self):
        with tempfile.TemporaryDirectory() as repo_dir:
            def run(*args):
                return subprocess.run(
                    args,
                    cwd=repo_dir,
                    check=True,
                    capture_output=True,
                    text=True,
                )

            run("git", "init")
            run("git", "config", "user.email", "test@example.com")
            run("git", "config", "user.name", "Test User")

            with open(os.path.join(repo_dir, "f.txt"), "w", encoding="utf-8") as f:
                f.write("x")

            run("git", "add", "f.txt")
            run("git", "commit", "-m", "feature | parsing", "-m", "body line 1\nbody line 2")

            real_run = subprocess.run
            with mock.patch("changeledger.rework.subprocess.run") as mock_run:
                def run_in_repo(*args, **kwargs):
                    kwargs["cwd"] = repo_dir
                    return real_run(*args, **kwargs)
                mock_run.side_effect = run_in_repo
                commits = get_merges_local(lookback_days=30)

        self.assertEqual(commits[0].subject, "feature | parsing")
        self.assertEqual(commits[0].message, "feature | parsing\nbody line 1\nbody line 2")
        self.assertEqual(commits[0].files, frozenset({"f.txt"}))

    def test_load_rework_data_rejects_invalid_items_with_changeledger_error(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as f:
            json.dump([{}], f)
            path = f.name

        try:
            with self.assertRaises(ChangeledgerError) as ctx:
                load_rework_data(path, make_cost_inputs())
        finally:
            os.unlink(path)

        self.assertIn("rework", str(ctx.exception).lower())
        self.assertIn("status", str(ctx.exception))

    def test_get_merges_github_rejects_truncated_500_pr_result_sets(self):
        pr = {
            "number": 1,
            "title": "Example PR",
            "mergedAt": "2026-03-01T00:00:00Z",
            "files": [{"path": "app.py"}],
            "mergeCommit": {"oid": "a" * 40},
            "body": "",
        }
        completed = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=json.dumps([pr] * 500),
            stderr="",
        )

        with mock.patch("changeledger.rework.subprocess.run", return_value=completed), \
                self.assertRaises(ChangeledgerError) as ctx:
            get_merges_github("owner/repo", lookback_days=30)

        self.assertIn("500", str(ctx.exception))
        self.assertIn("lookback", str(ctx.exception).lower())

    def test_print_report_includes_fix_commits_in_rework_rate(self):
        results = [
            {
                "sha": "acceptedsha",
                "date": "2026-03-01",
                "subject": "Accepted change",
                "status": "accepted",
                "signals": [],
            },
            {
                "sha": "reworksha",
                "date": "2026-03-02",
                "subject": "Reworked change",
                "status": "rework",
                "signals": ["Reverted by deadbeef00"],
            },
            {
                "sha": "fixcommit1",
                "date": "2026-03-03",
                "subject": "Follow-up fix",
                "status": "fix",
                "signals": [],
            },
        ]

        buf = io.StringIO()
        with redirect_stdout(buf):
            print_report(results, 14)

        self.assertIn("Rework rate: 66.7%", buf.getvalue())

    def test_generate_html_labels_reverted_metric_as_fixed_too(self):
        html = generate_html(
            {
                "currency": "$",
                "model_cost": 1,
                "infra_cost": 0,
                "prompting_cost": 0,
                "review_cost": 0,
                "rework_cost": 0,
                "total_cost": 1,
                "merged_prs": 3,
                "reverted_prs": 2,
                "accepted_changes": 1,
                "cost_per_accepted_change": 1.0,
                "breakdown": {
                    "model_pct": 100.0,
                    "infra_pct": 0.0,
                    "prompting_pct": 0.0,
                    "review_pct": 0.0,
                    "rework_pct": 0.0,
                },
            }
        )

        self.assertIn("Reverted / fixed", html)

    def test_cli_rejects_negative_window(self):
        result = subprocess.run(
            [sys.executable, "-m", "changeledger.cli", "rework", "--window", "-1"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("window", result.stderr.lower())

    def test_cli_rejects_negative_lookback(self):
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "changeledger.cli",
                "full",
                "--lookback",
                "-1",
                "--json",
                "costs-example.json",
            ],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("lookback", result.stderr.lower())


if __name__ == "__main__":
    unittest.main()
