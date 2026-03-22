#!/usr/bin/env python3
"""
changeledger — Cost per accepted change calculator.

Usage:
    changeledger cost                                  Interactive mode
    changeledger cost --json costs.json                From JSON input
    changeledger cost --json costs.json --html report  With HTML report
    changeledger rework                                Detect rework from local git
    changeledger rework --repo owner/repo              Detect rework from GitHub
    changeledger full --json costs.json                Rework + cost in one pass
"""

import argparse
import csv
import json
import sys
from pathlib import Path

from . import __version__
from .cost import (
    ChangeledgerError, calculate, interactive, print_results,
    load_rework_data, generate_warnings, detect_repo_info, summarize_rework,
)
from .rework import get_merges_local, get_merges_github, detect_rework, print_report as print_rework_report
from .report import generate_html


# ── Shared helpers ───────────────────────────────────────────────────

def _run_rework_scan(args) -> list[dict]:
    """Shared rework scan logic for cmd_rework and cmd_full."""
    print(f"Scanning {'GitHub ' + args.repo if args.repo else 'local repo'}...")
    print(f"Window: {args.window} days, lookback: {args.lookback} days")

    if args.repo:
        commits = get_merges_github(args.repo, args.lookback)
    else:
        commits = get_merges_local(args.lookback)

    if not commits:
        print("No commits found in the lookback period.")
        return []

    print(f"Found {len(commits)} commits to analyze.")

    results = detect_rework(commits, args.window)
    print_rework_report(results, args.window)
    return results


def _write_html_report(args, results: dict, rework_items: list | None = None):
    """Shared HTML report generation."""
    detected_name, detected_url = detect_repo_info()
    team = args.team if args.team is not None else detected_name
    repo_url = args.repo_url if args.repo_url is not None else detected_url
    warnings = generate_warnings(results, rework_items)
    html = generate_html(results, team, warnings, repo_url)
    Path(args.html).write_text(html, encoding="utf-8")
    print(f"  HTML report written to {args.html}")


# ── Commands ─────────────────────────────────────────────────────────

def cmd_cost(args):
    if args.json:
        data = json.loads(Path(args.json).read_text(encoding="utf-8"))
    elif args.interactive or sys.stdin.isatty():
        data = interactive()
    else:
        data = json.load(sys.stdin)

    rework_items = None
    if args.from_rework:
        data, rework_items = load_rework_data(args.from_rework, data)
        print(f"  Loaded rework data: {data['merged_prs']} classifiable, {data['reverted_prs']} reworked")

    results = calculate(data)
    print_results(results)

    if args.output:
        Path(args.output).write_text(json.dumps(results, indent=2), encoding="utf-8")
        print(f"  Results written to {args.output}")

    if args.html:
        _write_html_report(args, results, rework_items)


def cmd_rework(args):
    results = _run_rework_scan(args)
    if not results:
        return

    if args.json:
        Path(args.json).write_text(json.dumps(results, indent=2), encoding="utf-8")
        print(f"  Results written to {args.json}")

    if args.csv:
        with Path(args.csv).open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=[
                "sha", "full_sha", "date", "subject", "status", "age_days",
                "signals", "ticket_ids", "files_changed",
            ])
            writer.writeheader()
            for r in results:
                row = dict(r)
                row["signals"] = "; ".join(r["signals"])
                row["ticket_ids"] = ", ".join(r["ticket_ids"])
                writer.writerow(row)
        print(f"  Results written to {args.csv}")


def cmd_full(args):
    """Run rework detection + cost calculation in one pass."""
    rework_results = _run_rework_scan(args)
    if not rework_results:
        return

    # Load cost inputs
    if args.json:
        data = json.loads(Path(args.json).read_text(encoding="utf-8"))
    else:
        data = interactive()

    # Override with real rework data
    # Fix commits count toward rework — they represent follow-up cost
    accepted, rework, fix, pending = summarize_rework(rework_results)
    data["merged_prs"] = accepted + rework + fix
    data["reverted_prs"] = rework + fix

    from .cost import _print_pending_note
    _print_pending_note(pending)

    results = calculate(data)
    print_results(results)

    if args.html:
        _write_html_report(args, results, rework_results)


def main():
    parser = argparse.ArgumentParser(
        prog="changeledger",
        description="Cost per accepted change — the delivery metric no commercial tool computes.",
    )
    parser.add_argument("--version", action="version", version=f"changeledger {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # cost subcommand
    p_cost = subparsers.add_parser("cost", help="Calculate cost per accepted change")
    p_cost.add_argument("--json", help="Read cost inputs from JSON file")
    p_cost.add_argument("--interactive", action="store_true", help="Interactive prompt mode")
    p_cost.add_argument("--output", help="Write results to JSON file")
    p_cost.add_argument("--html", help="Generate branded HTML report")
    p_cost.add_argument("--team", default=None, help="Team name for report header (default: auto-detect)")
    p_cost.add_argument("--from-rework", help="Read rework data from JSON (overrides merged/reverted counts)")
    p_cost.add_argument("--repo-url", default=None, help="GitHub repo URL for commit links (default: auto-detect)")
    p_cost.set_defaults(func=cmd_cost)

    # rework subcommand
    p_rework = subparsers.add_parser("rework", help="Detect rework from git history")
    p_rework.add_argument("--repo", help="GitHub repo (owner/repo). Omit for local git.")
    p_rework.add_argument("--window", type=int, default=14, help="Observation window in days (default: 14)")
    p_rework.add_argument("--lookback", type=int, default=45, help="How far back to scan (default: 45 days)")
    p_rework.add_argument("--json", help="Write results to JSON file")
    p_rework.add_argument("--csv", help="Write results to CSV file")
    p_rework.set_defaults(func=cmd_rework)

    # full subcommand
    p_full = subparsers.add_parser("full", help="Rework detection + cost calculation in one pass")
    p_full.add_argument("--repo", help="GitHub repo (owner/repo). Omit for local git.")
    p_full.add_argument("--window", type=int, default=14, help="Observation window in days (default: 14)")
    p_full.add_argument("--lookback", type=int, default=45, help="How far back to scan (default: 45 days)")
    p_full.add_argument("--json", help="Read cost inputs from JSON file")
    p_full.add_argument("--html", help="Generate branded HTML report")
    p_full.add_argument("--team", default=None, help="Team name for report header")
    p_full.add_argument("--repo-url", default=None, help="GitHub repo URL for commit links")
    p_full.set_defaults(func=cmd_full)

    args = parser.parse_args()
    try:
        args.func(args)
    except ChangeledgerError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
