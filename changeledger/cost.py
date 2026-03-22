"""
Cost per accepted change calculator.

The formula:
    cost per accepted change = (model + infra + human engineering + review + rework) / accepted changes

Feed it your numbers and it produces the cost breakdown, with an optional
branded HTML report including SVG pie charts and rework warnings.
"""

import json
import re
import subprocess
from typing import Any


# ── Thresholds (from research) ───────────────────────────────────────

REWORK_RATE_THRESHOLD = 15  # % — flag above this
LARGE_PR_FILES = 20  # files — proxy for 400+ lines when line count unavailable


class ChangeledgerError(Exception):
    """Raised when inputs are invalid."""


def calculate(data: dict) -> dict:
    """Calculate cost per accepted change from input data.

    Raises ChangeledgerError if accepted changes <= 0.
    """
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
        val = input(f"  {prompt} [{default}]: ").strip()
        return float(val) if val else default

    return {
        "model_cost": ask("AI model/API spend this period ($)", 4200),
        "infra_cost": ask("Infrastructure cost ($)", 1800),
        "prompting_hours": ask("Human engineering hours (discussion, specs, prompting)", 30),
        "review_hours": ask("Hours spent reviewing AI output", 40),
        "rework_hours": ask("Hours spent on rework/fixes", 20),
        "burdened_rate": ask("Fully burdened hourly rate ($)", 120),
        "merged_prs": ask("Merged PRs this period", 88),
        "reverted_prs": ask("Reverted/hotfixed PRs within 14 days", 12),
    }


def print_results(r: dict):
    """Print cost breakdown to stdout."""
    print()
    print("=" * 50)
    print(" COST PER ACCEPTED CHANGE BREAKDOWN")
    print("=" * 50)
    print()
    print(f"  Model/API cost:      ${r['model_cost']:>10,.0f}  ({r['breakdown']['model_pct']}%)")
    print(f"  Infrastructure:      ${r['infra_cost']:>10,.0f}  ({r['breakdown']['infra_pct']}%)")
    print(f"  Human engineering:   ${r['prompting_cost']:>10,.0f}  ({r['breakdown']['prompting_pct']}%)")
    print(f"  Human review:        ${r['review_cost']:>10,.0f}  ({r['breakdown']['review_pct']}%)")
    print(f"  Rework:              ${r['rework_cost']:>10,.0f}  ({r['breakdown']['rework_pct']}%)")
    print(f"  {'─' * 40}")
    print(f"  Total cost:          ${r['total_cost']:>10,.0f}")
    print()
    print(f"  Merged PRs:          {r['merged_prs']:>10}")
    print(f"  Reverted/fixed:      {r['reverted_prs']:>10}")
    print(f"  Accepted changes:    {r['accepted_changes']:>10}")
    print()
    print("  ┌─────────────────────────────────────────┐")
    print(f"  │  Cost per accepted change: ${r['cost_per_accepted_change']:>10,.2f}  │")
    print("  └─────────────────────────────────────────┘")
    print()

    visible = r["breakdown"]["model_pct"] + r["breakdown"]["infra_pct"]
    hidden = r["breakdown"]["prompting_pct"] + r["breakdown"]["review_pct"] + r["breakdown"]["rework_pct"]
    print(f"  Visible cost (model + infra): {visible:.0f}%")
    print(f"  Hidden cost (people):         {hidden:.0f}%")
    print()


def summarize_rework(rework_results: list[dict]) -> tuple[int, int, int]:
    """Count accepted, rework, and pending from rework results."""
    accepted = sum(1 for r in rework_results if r["status"] == "accepted")
    rework = sum(1 for r in rework_results if r["status"] == "rework")
    pending = sum(1 for r in rework_results if r["status"] == "pending")
    return accepted, rework, pending


def load_rework_data(rework_json_path: str, data: dict) -> tuple[dict, list]:
    """Override merged_prs and reverted_prs from rework detector output."""
    from pathlib import Path
    rework_results = json.loads(Path(rework_json_path).read_text(encoding="utf-8"))

    accepted, rework, pending = summarize_rework(rework_results)
    total = accepted + rework

    if pending > 0:
        print(
            f"  Note: {pending} pending change(s) excluded from denominator "
            f"(< observation window). Their cost is still in the numerator.",
            flush=True,
        )

    data["merged_prs"] = total
    data["reverted_prs"] = rework
    return data, rework_results


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
        reworked = [item for item in rework_items if item["status"] == "rework"]

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
                "title": f"{len(reworked)} changes required rework",
                "detail": "These changes were reverted or patched within 14 days.",
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
