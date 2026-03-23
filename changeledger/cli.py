#!/usr/bin/env python3
"""
changeledger — Cost per accepted change calculator.

Thin CLI dispatcher — delegates to domain modules for all logic.
"""
#coherence:intent import_bloat "CLI orchestrator legitimately coordinates cost, rework, report, and output modules"
#coherence:intent god_module "50-function CLI app — command handlers are the application entry points, not dead code"

import argparse
import csv
import json
import sys
from pathlib import Path

from . import __version__
from .cost import (
    apply_rework_to_cost_data,
    calculate,
    load_rework_data,
)
from .errors import ChangeledgerError
from .output import detect_repo_info, interactive, print_pending_note, print_results
from .report import write_html_report
from .rework import run_scan

# ── Argument helpers ─────────────────────────────────────────────────

def positive_int_arg(name: str):
    """argparse type for positive integer options."""
    def parser(value: str) -> int:
        try:
            parsed = int(value)
        except ValueError as err:
            raise argparse.ArgumentTypeError(
                f"{name} must be a positive integer (got '{value}')"
            ) from err
        if parsed <= 0:
            raise argparse.ArgumentTypeError(f"{name} must be > 0")
        return parsed

    return parser


# ── Shared input loading ──────────────────────────────────────────────

def load_cost_inputs(args) -> dict:
    """Load cost inputs from --json, stdin, or interactive prompts."""
    if getattr(args, "json", None):
        return json.loads(Path(args.json).read_text(encoding="utf-8"))
    elif getattr(args, "interactive", False) or sys.stdin.isatty():
        return interactive()
    else:
        return json.load(sys.stdin)


# ── Shared output helpers ─────────────────────────────────────────────

def _maybe_write_html(args, results: dict, rework_items: list | None = None):
    """Write HTML report if --html was specified."""
    if not getattr(args, "html", None):
        return
    detected_name, detected_url = detect_repo_info()
    write_html_report(
        args.html, results, rework_items,
        team=args.team if args.team is not None else detected_name,
        repo_url=args.repo_url if args.repo_url is not None else detected_url,
    )


# ── Commands ─────────────────────────────────────────────────────────

def cmd_cost(args):
    data = load_cost_inputs(args)

    normalize = not args.no_normalize
    rework_items = None
    if args.from_rework:
        data, rework_items, pending = load_rework_data(args.from_rework, data, normalize=normalize)
        print_pending_note(pending)
        print(f"  Loaded rework data: {data['merged_prs']} classifiable, {data['reverted_prs']} reworked")

    results = calculate(data)
    print_results(results)

    if args.output:
        Path(args.output).write_text(json.dumps(results, indent=2), encoding="utf-8")
        print(f"  Results written to {args.output}")

    _maybe_write_html(args, results, rework_items)


def _load_changes(args):
    """Load MergedChange objects from --from-prs if provided."""
    from_prs = getattr(args, "from_prs", None)
    if from_prs:
        from delivery_gap_signals.sources.file import fetch_changes
        return fetch_changes(from_prs)
    return None


def cmd_rework(args):
    changes = _load_changes(args)
    results = run_scan(args.repo, args.lookback, args.window, changes=changes)
    if not results:
        return

    if args.json:
        Path(args.json).write_text(json.dumps(results, indent=2), encoding="utf-8")
        print(f"  Results written to {args.json}")

    if args.csv:
        with Path(args.csv).open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=[
                "sha", "full_sha", "pr_number", "date", "subject", "status",
                "age_days", "signals", "ticket_ids", "files_changed",
                "lines_changed", "normalized_units",
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
    changes = _load_changes(args)
    rework_results = run_scan(args.repo, args.lookback, args.window, changes=changes)
    if not rework_results:
        print("No commits found; skipping cost calculation.")
        return

    data = load_cost_inputs(args)

    normalize = not args.no_normalize
    data, pending = apply_rework_to_cost_data(data, rework_results, normalize=normalize)
    print_pending_note(pending)

    results = calculate(data)
    print_results(results)

    _maybe_write_html(args, results, rework_results)


def main():
    parser = argparse.ArgumentParser(
        prog="changeledger",
        description="Cost per accepted change — the delivery metric no commercial tool computes.",
    )
    parser.add_argument("--version", action="version", version=f"changeledger {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # Shared option groups (argparse parents)
    rework_opts = argparse.ArgumentParser(add_help=False)
    rework_source = rework_opts.add_mutually_exclusive_group()
    rework_source.add_argument("--repo", help="GitHub repo (owner/repo). Omit for local git.")
    rework_source.add_argument("--from-prs", help="Read changes from JSON file (MergedChange format)")
    rework_opts.add_argument("--window", type=positive_int_arg("window"), default=14, help="Observation window in days (default: 14)")
    rework_opts.add_argument("--lookback", type=positive_int_arg("lookback"), default=90, help="How far back to scan (default: 90 days)")

    report_opts = argparse.ArgumentParser(add_help=False)
    report_opts.add_argument("--html", help="Generate branded HTML report")
    report_opts.add_argument("--team", default=None, help="Team name for report header (default: auto-detect)")
    report_opts.add_argument("--repo-url", default=None, help="GitHub repo URL for commit links (default: auto-detect)")
    report_opts.add_argument("--no-normalize", action="store_true", help="Disable LOC normalization (use raw PR counts)")

    # cost subcommand
    p_cost = subparsers.add_parser("cost", parents=[report_opts], help="Calculate cost per accepted change")
    p_cost.add_argument("--json", help="Read cost inputs from JSON file")
    p_cost.add_argument("--interactive", action="store_true", help="Interactive prompt mode")
    p_cost.add_argument("--output", help="Write results to JSON file")
    p_cost.add_argument("--from-rework", help="Read rework data from JSON (overrides merged/reverted counts)")
    p_cost.set_defaults(func=cmd_cost)

    # rework subcommand
    p_rework = subparsers.add_parser("rework", parents=[rework_opts], help="Detect rework from git history")
    p_rework.add_argument("--json", help="Write results to JSON file")
    p_rework.add_argument("--csv", help="Write results to CSV file")
    p_rework.set_defaults(func=cmd_rework)

    # full subcommand — inherits both rework and report options
    p_full = subparsers.add_parser("full", parents=[rework_opts, report_opts], help="Rework detection + cost calculation in one pass")
    p_full.add_argument("--json", help="Read cost inputs from JSON file")
    p_full.set_defaults(func=cmd_full)

    args = parser.parse_args()
    try:
        args.func(args)
    except ChangeledgerError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
