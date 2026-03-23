"""
Cost per accepted change calculator — pure computation, no I/O.

The formula:
    cost per accepted change = (model + infra + human engineering + review + rework) / accepted changes
"""

import json
import math
from numbers import Real
from pathlib import Path
from typing import Any, NamedTuple

from .errors import ChangeledgerError

# Re-export for backward compatibility with external consumers
__all__ = [
    "ChangeledgerError",
    "apply_rework_to_cost_data",
    "calculate",
    "resolve_currency",
    "validate_inputs",
    "validate_rework_results",
]

# ── Constants ────────────────────────────────────────────────────────

REQUIRED_FIELDS = [
    "model_cost", "infra_cost", "prompting_hours", "review_hours",
    "rework_hours", "burdened_rate", "merged_prs", "reverted_prs",
]

# All required fields also happen to be non-negative.
# If a future field can be negative, add it only to REQUIRED_FIELDS.
NON_NEGATIVE_FIELDS = list(REQUIRED_FIELDS)

# These fields must be whole numbers (int or float with zero fraction).
INTEGER_FIELDS = {"merged_prs", "reverted_prs"}

REWORK_REQUIRED_FIELDS = ["sha", "subject", "status", "signals"]
VALID_REWORK_STATUSES = {"accepted", "rework", "fix", "pending"}

CURRENCY_SYMBOLS = {
    "USD": "$", "EUR": "€", "GBP": "£", "CHF": "CHF ",
    "JPY": "¥", "CNY": "¥", "INR": "₹", "BRL": "R$",
    "KRW": "₩", "SEK": "kr ", "NOK": "kr ", "DKK": "kr ",
    "PLN": "zł ", "CZK": "Kč ", "ILS": "₪", "TRY": "₺",
    "AUD": "A$", "CAD": "C$", "SGD": "S$", "HKD": "HK$",
}


# ── Validation ───────────────────────────────────────────────────────

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

        if field in INTEGER_FIELDS and value != int(value):
            errors.append(f"'{field}' must be a whole number (got {value})")

    if errors:
        raise ChangeledgerError(
            "Invalid input:\n  " + "\n  ".join(errors)
            + "\n\nSee costs-example.json for the expected format."
        )


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


# ── Pure computation ─────────────────────────────────────────────────

def _largest_remainder_pcts(
    keys: list[str], values: list[float], total: float,
) -> dict[str, float]:
    """Round percentages so they always sum to exactly 100.0%.

    Uses the largest-remainder method: floor each value to one decimal,
    then distribute the residual tenths to the entries with the largest
    fractional remainders.
    """
    raw = [v / total * 1000 for v in values]  # work in tenths of a percent
    floored = [math.floor(r) for r in raw]
    remainders = [r - f for r, f in zip(raw, floored, strict=True)]
    residual = 1000 - sum(floored)

    if residual > 0:
        # Distribute extra tenths to entries with the largest remainders
        indices = sorted(range(len(keys)), key=lambda i: -remainders[i])
        for i in indices[:residual]:
            floored[i] += 1
    elif residual < 0:
        # Remove excess tenths from entries with the smallest remainders,
        # but never subtract below 0.
        indices = sorted(range(len(keys)), key=lambda i: remainders[i])
        remaining = -residual
        for i in indices:
            if remaining <= 0:
                break
            if floored[i] > 0:
                floored[i] -= 1
                remaining -= 1

    return {k: f / 10 for k, f in zip(keys, floored, strict=True)}


def calculate(data: dict) -> dict:  # -> CostResult
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
        breakdown = _largest_remainder_pcts(
            ["model_pct", "infra_pct", "prompting_pct", "review_pct", "rework_pct"],
            [data["model_cost"], data["infra_cost"], prompting_cost, review_cost, rework_cost],
            total_cost,
        )
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


class ReworkApplyResult(NamedTuple):
    """Result of apply_rework_to_cost_data."""
    data: dict
    pending: int


class ReworkLoadResult(NamedTuple):
    """Result of load_rework_data."""
    data: dict
    rework_results: list
    pending: int


def apply_rework_to_cost_data(
    data: dict, rework_results: list[dict], normalize: bool = True,
) -> ReworkApplyResult:
    """Apply rework summary to cost data.

    Fix commits count toward rework — they represent follow-up cost.

    When *normalize* is True (default), the merged/reverted counts use
    LOC-normalized units instead of raw PR counts.
    """
    from .models import rework_summary

    data = {**data}
    s = rework_summary(rework_results, normalize=normalize)
    data["merged_prs"] = s["accepted"] + s["rework"] + s["fix"]
    data["reverted_prs"] = s["rework"] + s["fix"]
    return ReworkApplyResult(data, s["pending"])


def load_rework_data(
    rework_json_path: str, data: dict, normalize: bool = True,
) -> ReworkLoadResult:
    """Load and validate rework JSON, apply to cost data."""
    rework_results = json.loads(Path(rework_json_path).read_text(encoding="utf-8"))
    validate_rework_results(rework_results)

    result = apply_rework_to_cost_data(data, rework_results, normalize=normalize)
    return ReworkLoadResult(result.data, rework_results, result.pending)
