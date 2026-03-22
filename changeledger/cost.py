"""
Cost per accepted change calculator.

The formula:
    cost per accepted change = (model + infra + human engineering + review + rework) / accepted changes

Feed it your numbers and it produces the cost breakdown, with an optional
branded HTML report including SVG pie charts and rework warnings.
"""

import json
import math
import re
import subprocess
from numbers import Real
from typing import Any


# ── Thresholds (from research) ───────────────────────────────────────

REWORK_RATE_THRESHOLD = 15  # % — flag above this
LARGE_PR_FILES = 20  # files — proxy for 400+ lines when line count unavailable

# ── Required input fields ────────────────────────────────────────────

REQUIRED_FIELDS = [
    "model_cost", "infra_cost", "prompting_hours", "review_hours",
    "rework_hours", "burdened_rate", "merged_prs", "reverted_prs",
]

NON_NEGATIVE_FIELDS = [
    "model_cost", "infra_cost", "prompting_hours", "review_hours",
    "rework_hours", "burdened_rate", "merged_prs", "reverted_prs",
]

REWORK_REQUIRED_FIELDS = ["sha", "subject", "status", "signals"]
VALID_REWORK_STATUSES = {"accepted", "rework", "fix", "pending"}


class ChangeledgerError(Exception):
    """Raised when inputs are invalid."""


CURRENCY_SYMBOLS = {
    "USD": "$", "EUR": "€", "GBP": "£", "CHF": "CHF ",
    "JPY": "¥", "CNY": "¥", "INR": "₹", "BRL": "R$",
    "KRW": "₩", "SEK": "kr ", "NOK": "kr ", "DKK": "kr ",
    "PLN": "zł ", "CZK": "Kč ", "ILS": "₪", "TRY": "₺",
    "AUD": "A$", "CAD": "C$", "SGD": "S$", "HKD": "HK$",
}


def resolve_currency(data: dict) -> str:
    """Resolve currency symbol from input data.

    Accepts either a symbol directly ("€") or an ISO code ("EUR").
    Defaults to "$" if not specified. Short custom tokens such as
    "R$", "A$", or "kr" are preserved for display.
    """
    raw_value = data.get("currency")
    raw = str(raw_value).strip() if raw_value is not None else "USD"
    if not raw:
        raw = "USD"

    sym = CURRENCY_SYMBOLS.get(raw.upper())
    if sym is not None:
        return sym

    # Preserve short user-supplied display tokens while rejecting obvious
    # markup/control characters. HTML output is escaped separately.
    if (
        len(raw) <= 4
        and raw.isprintable()
        and not any(ch.isspace() or ch in "<>&\"'" for ch in raw)
    ):
        return raw if not raw.isalpha() else raw + " "
    return "$"


def validate_inputs(data: dict) -> None:
    """Validate required fields and value constraints.

    Raises ChangeledgerError with actionable message listing all problems.
    """
    errors = []

    missing = [f for f in REQUIRED_FIELDS if f not in data]
    if missing:
        errors.append(f"Missing required fields: {', '.join(missing)}")

    for field in NON_NEGATIVE_FIELDS:
        if field not in data:
            continue

        value = data[field]
        if isinstance(value, bool) or not isinstance(value, Real):
            errors.append(f"'{field}' must be a number (got {type(value).__name__})")
            continue

        if not math.isfinite(value):
            errors.append(f"'{field}' must be a finite number (got {value})")
            continue

        if value < 0:
            errors.append(f"'{field}' must be non-negative (got {value})")

    if errors:
        raise ChangeledgerError(
            "Invalid input:\n  " + "\n  ".join(errors)
            + "\n\nSee costs-example.json for the expected format."
        )


def calculate(data: dict) -> dict:
    """Calculate cost per accepted change from input data.

    Raises ChangeledgerError if inputs are invalid or accepted changes <= 0.
    """
    validate_inputs(data)

    prompting_cost = data["prompting_hours"] * data["burdened_rate"]
    review_cost = data["review_hours"] * data["burdened_rate"]
    rework_cost = data["rework_hours"] * data["burdened_rate"]

    total_cost = (
        data["model_cost"]
        + data["infra_cost"]
        + prompting_cost
        + review_cost
        + rework_cost
    )

    accepted = data["merged_prs"] - data["reverted_prs"]
    if accepted <= 0:
        raise ChangeledgerError(
            f"Accepted changes must be > 0 (merged={data['merged_prs']}, "
            f"reverted={data['reverted_prs']}). Check your inputs."
        )

    cost_per_change = total_cost / accepted

    # Guard division by zero when total_cost is 0
    if total_cost > 0:
        breakdown = {
            "model_pct": round(data["model_cost"] / total_cost * 100, 1),
            "infra_pct": round(data["infra_cost"] / total_cost * 100, 1),
            "prompting_pct": round(prompting_cost / total_cost * 100, 1),
            "review_pct": round(review_cost / total_cost * 100, 1),
            "rework_pct": round(rework_cost / total_cost * 100, 1),
        }
    else:
        breakdown = {
            "model_pct": 0.0,
            "infra_pct": 0.0,
            "prompting_pct": 0.0,
            "review_pct": 0.0,
            "rework_pct": 0.0,
        }

    return {
        "currency": resolve_currency(data),
        "model_cost": data["model_cost"],
        "infra_cost": data["infra_cost"],
        "prompting_cost": prompting_cost,
        "review_cost": review_cost,
        "rework_cost": rework_cost,
        "total_cost": total_cost,
        "merged_prs": data["merged_prs"],
        "reverted_prs": data["reverted_prs"],
        "accepted_changes": accepted,
        "cost_per_accepted_change": round(cost_per_change, 2),
        "breakdown": breakdown,
    }


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
        "merged_prs": ask("Merged PRs this period", 88),
        "reverted_prs": ask("Reverted/hotfixed PRs within 14 days", 12),
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


def summarize_rework(rework_results: list[dict]) -> tuple[int, int, int, int]:
    """Count accepted, rework, fix, and pending from rework results.

    Fix commits are counted as rework for cost purposes — the original
    change required a follow-up, so both the original and the fix
    consumed engineering time that should be attributed to rework.
    """
    accepted = sum(1 for r in rework_results if r["status"] == "accepted")
    rework = sum(1 for r in rework_results if r["status"] == "rework")
    fix = sum(1 for r in rework_results if r["status"] == "fix")
    pending = sum(1 for r in rework_results if r["status"] == "pending")
    return accepted, rework, fix, pending


def validate_rework_results(rework_results: Any) -> None:
    """Validate rework JSON loaded from `changeledger rework --json`."""
    if not isinstance(rework_results, list):
        raise ChangeledgerError(
            "Invalid rework data:\n  Expected a JSON array of rework result objects."
        )

    errors = []
    for idx, item in enumerate(rework_results, start=1):
        if not isinstance(item, dict):
            errors.append(f"Item {idx} must be an object (got {type(item).__name__})")
            continue

        missing = [field for field in REWORK_REQUIRED_FIELDS if field not in item]
        if missing:
            errors.append(f"Item {idx} missing required fields: {', '.join(missing)}")
            continue

        if item["status"] not in VALID_REWORK_STATUSES:
            errors.append(
                f"Item {idx} has invalid status '{item['status']}' "
                f"(expected one of: {', '.join(sorted(VALID_REWORK_STATUSES))})"
            )

        if not isinstance(item["sha"], str) or not item["sha"]:
            errors.append(f"Item {idx} has invalid sha (expected non-empty string)")

        if not isinstance(item["subject"], str):
            errors.append(f"Item {idx} has invalid subject (expected string)")

        if not isinstance(item["signals"], list):
            errors.append(f"Item {idx} has invalid signals (expected array)")

    if errors:
        raise ChangeledgerError(
            "Invalid rework data:\n  " + "\n  ".join(errors)
            + "\n\nUse the JSON emitted by `changeledger rework --json`."
        )


def load_rework_data(rework_json_path: str, data: dict) -> tuple[dict, list]:
    """Override merged_prs and reverted_prs from rework detector output."""
    from pathlib import Path
    rework_results = json.loads(Path(rework_json_path).read_text(encoding="utf-8"))
    validate_rework_results(rework_results)

    accepted, rework, fix, pending = summarize_rework(rework_results)
    # Fix commits count toward rework — they represent follow-up cost
    total = accepted + rework + fix

    _print_pending_note(pending)

    data["merged_prs"] = total
    data["reverted_prs"] = rework + fix
    return data, rework_results


def _print_pending_note(pending: int) -> None:
    """Print a note about excluded pending changes."""
    if pending > 0:
        print(
            f"  Note: {pending} pending change(s) excluded from denominator "
            f"(< observation window). Their cost is still in the numerator.",
            flush=True,
        )


def generate_warnings(r: dict, rework_items: list | None = None) -> list[dict[str, Any]]:
    """Generate warning cards based on thresholds from research."""
    warnings: list[dict[str, Any]] = []

    total = r["merged_prs"]
    if total > 0:
        rework_rate = r["reverted_prs"] / total * 100
        if rework_rate > REWORK_RATE_THRESHOLD:
            warnings.append({
                "level": "high",
                "title": f"Rework rate: {rework_rate:.0f}%",
                "detail": f"Above {REWORK_RATE_THRESHOLD}% baseline. Check spec quality and gate coverage.",
            })

    if rework_items:
        oversized = [
            item for item in rework_items
            if item.get("files_changed", 0) > LARGE_PR_FILES
        ]
        reworked = [item for item in rework_items if item["status"] in ("rework", "fix")]

        if oversized:
            warnings.append({
                "level": "medium",
                "title": f"{len(oversized)} PRs touch {LARGE_PR_FILES}+ files",
                "detail": "Review effectiveness drops sharply above 400 lines (SmartBear/Cisco). Consider enforcing a PR size limit in CI.",
                "structured_items": oversized[:10],
            })

        if reworked:
            warnings.append({
                "level": "high",
                "title": f"{len(reworked)} changes required rework or follow-up fixes",
                "detail": "These changes were reverted, patched, or required fix commits within 14 days.",
                "structured_items": reworked[:10],
            })

    return warnings


def detect_repo_info() -> tuple[str, str]:
    """Try to detect repo name and GitHub URL from git remote."""
    try:
        result = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            capture_output=True, text=True, check=False,
        )
        if result.returncode == 0:
            raw = result.stdout.strip()
            for pattern in [r"[:/]([^/]+/[^/]+?)(?:\.git)?$", r"([^/]+/[^/]+?)(?:\.git)?$"]:
                m = re.search(pattern, raw)
                if m:
                    name = m.group(1)
                    url = f"https://github.com/{name}"
                    return name, url
    except FileNotFoundError:
        pass
    return "", ""
