"""
Terminal output and interactive I/O for changeledger.

Separates all user-facing I/O from pure computation and CLI dispatch.
"""

import re
import subprocess

from .cost import CURRENCY_SYMBOLS


def interactive() -> dict:
    """Prompt for input interactively."""
    print("Cost per Accepted Change Calculator")
    print("=" * 44)
    print()

    def ask(prompt, default=0):
        while True:
            val = input(f"  {prompt} [{default}]: ").strip()
            if not val:
                return float(default)
            try:
                return float(val)
            except ValueError:
                print("    Not a number. Try again.")

    def ask_int(prompt, default=0):
        while True:
            val = ask(prompt, default)
            if val != int(val):
                print(f"    Must be a whole number (got {val}). Try again.")
                continue
            return int(val)

    currency = input("  Currency symbol or ISO code [$]: ").strip() or "$"

    sym = CURRENCY_SYMBOLS.get(currency.upper(), currency)

    return {
        "currency": currency,
        "model_cost": ask(f"AI model/API spend this period ({sym})", 4200),
        "infra_cost": ask(f"Infrastructure cost ({sym})", 1800),
        "prompting_hours": ask("Human engineering hours (discussion, specs, prompting)", 30),
        "review_hours": ask("Hours spent reviewing AI output", 40),
        "rework_hours": ask("Hours spent on rework/fixes", 20),
        "burdened_rate": ask(f"Fully burdened hourly rate ({sym})", 120),
        "merged_prs": ask_int("Merged PRs this period", 88),
        "reverted_prs": ask_int("Reverted/hotfixed PRs within 14 days", 12),
    }


def print_results(r: dict):
    """Print cost breakdown to stdout."""
    c = r.get("currency", "$")
    print()
    print("=" * 50)
    print(" COST PER ACCEPTED CHANGE BREAKDOWN")
    print("=" * 50)
    print()
    print(f"  Model/API cost:      {c}{r['model_cost']:>10,.0f}  ({r['breakdown']['model_pct']}%)")
    print(f"  Infrastructure:      {c}{r['infra_cost']:>10,.0f}  ({r['breakdown']['infra_pct']}%)")
    print(f"  Human engineering:   {c}{r['prompting_cost']:>10,.0f}  ({r['breakdown']['prompting_pct']}%)")
    print(f"  Human review:        {c}{r['review_cost']:>10,.0f}  ({r['breakdown']['review_pct']}%)")
    print(f"  Rework:              {c}{r['rework_cost']:>10,.0f}  ({r['breakdown']['rework_pct']}%)")
    print(f"  {'─' * 40}")
    print(f"  Total cost:          {c}{r['total_cost']:>10,.0f}")
    print()
    print(f"  Merged PRs:          {r['merged_prs']:>10}")
    print(f"  Reverted/fixed:      {r['reverted_prs']:>10}")
    print(f"  Accepted changes:    {r['accepted_changes']:>10}")
    print()
    print("  ┌──────────────────────────────────────────────┐")
    print(f"  │  Cost per accepted change: {c}{r['cost_per_accepted_change']:>10,.2f}  │")
    print("  └──────────────────────────────────────────────┘")
    print()

    visible = r["breakdown"]["model_pct"] + r["breakdown"]["infra_pct"]
    hidden = r["breakdown"]["prompting_pct"] + r["breakdown"]["review_pct"] + r["breakdown"]["rework_pct"]
    print(f"  Visible cost (model + infra): {visible:.0f}%")
    print(f"  Hidden cost (people):         {hidden:.0f}%")
    print()


def print_pending_note(pending: int) -> None:
    """Print a note about excluded pending changes."""
    if pending > 0:
        print(
            f"  Note: {pending} pending change(s) excluded from denominator "
            f"(< observation window). Their cost is still in the numerator.",
            flush=True,
        )


def print_rework_report(results: list[dict], window_days: int):
    """Print rework detection report to stdout."""
    from .models import rework_summary

    summary = rework_summary(results)
    rework_items = [r for r in results if r["status"] == "rework"]

    print()
    print("=" * 60)
    print(f" REWORK DETECTION REPORT ({window_days}-day window)")
    print("=" * 60)
    print()
    print(f"  Accepted:  {summary['accepted']}")
    print(f"  Rework:    {summary['rework']}")
    print(f"  Fixes:     {summary['fix']} (fix commits, not counted as accepted)")
    print(f"  Pending:   {summary['pending']} (< {window_days} days old)")
    print()

    if summary["rework_rate"] is not None:
        print(f"  Rework rate: {summary['rework_rate']:.1f}%")
    else:
        print("  Rework rate: N/A (no changes old enough to classify)")
    print()

    if rework_items:
        print("  REWORKED CHANGES:")
        print("  " + "-" * 56)
        for r in rework_items:
            print(f"  {r['sha']}  {r['date']}  {r['subject'][:50]}")
            for signal in r["signals"]:
                print(f"    -> {signal}")
        print()


def detect_repo_info() -> tuple[str, str]:
    """Try to detect repo name and URL from git remote.

    Supports GitHub, GitLab, Bitbucket, and other hosts.
    """
    try:
        result = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            capture_output=True, text=True, check=False, timeout=10,
        )
        if result.returncode == 0:
            raw = result.stdout.strip()
            # Strip embedded credentials (e.g. https://token@github.com/...)
            raw = re.sub(r"://[^@]+@", "://", raw)
            # SSH: git@host:owner/repo.git
            ssh_match = re.match(r"git@([^:]+):([^/]+/[^/]+?)(?:\.git)?$", raw)
            if ssh_match:
                host = ssh_match.group(1)
                name = ssh_match.group(2)
                return name, f"https://{host}/{name}"
            # HTTPS: https://host/owner/repo.git
            https_match = re.match(r"https?://([^/]+)/([^/]+/[^/]+?)(?:\.git)?$", raw)
            if https_match:
                host = https_match.group(1)
                name = https_match.group(2)
                return name, f"https://{host}/{name}"
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return "", ""
