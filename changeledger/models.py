"""Data models for changeledger."""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TypedDict

from .signals import (
    extract_fixes_sha,
    extract_ticket_ids,
    is_fix_message,
    is_revert_message,
    is_source_file,
)


@dataclass(frozen=True, slots=True)
class Commit:
    """A single merge commit or PR, with pre-computed rework signals."""

    sha: str
    short_sha: str
    date: datetime
    subject: str
    message: str
    files: frozenset[str]
    src_files: frozenset[str]
    ticket_ids: frozenset[str]
    is_fix: bool
    is_revert: bool
    fixes_sha: str | None
    lines_changed: int = 0
    pr_number: int | None = None

    @classmethod
    def build(
        cls,
        sha: str,
        date: datetime,
        subject: str,
        body: str,
        files: set[str],
        *,
        lines_changed: int = 0,
        pr_number: int | None = None,
    ) -> Commit:
        """Validated constructor shared by local and GitHub sources."""
        if not sha:
            raise ValueError("Commit SHA must be non-empty")

        message = f"{subject}\n{body}".strip()

        if date.tzinfo is None:
            date = date.replace(tzinfo=timezone.utc)

        return cls(
            sha=sha,
            short_sha=sha[:10],
            date=date,
            subject=subject,
            message=message,
            files=frozenset(f for f in files if f),
            src_files=frozenset(f for f in files if f and is_source_file(f)),
            ticket_ids=frozenset(extract_ticket_ids(message)),
            is_fix=is_fix_message(message),
            is_revert=is_revert_message(message),
            fixes_sha=extract_fixes_sha(message),
            lines_changed=lines_changed,
            pr_number=pr_number,
        )

    def sha_in_text(self, text: str) -> bool:
        """Check if this commit's SHA appears in text (7, 10, or 40 char prefix).

        Uses word-boundary matching for short prefixes to avoid false
        positives on hex substrings inside UUIDs or other hashes.
        Synthetic PR# identifiers never match.
        """
        if not self.sha[:7].isascii() or not all(c in "0123456789abcdef" for c in self.sha[:7]):
            return False  # synthetic SHA like PR#42 — skip text matching
        if self.sha in text or self.short_sha in text:
            return True
        return bool(re.search(r'(?<![0-9a-fA-F])' + re.escape(self.sha[:7]) + r'(?![0-9a-fA-F])', text))


class ReworkSummary(TypedDict):
    """Canonical rework summary — returned by rework_summary()."""

    accepted: int
    rework: int
    fix: int
    pending: int
    total_classifiable: int
    rework_rate: float | None


class ReworkItem(TypedDict, total=False):
    """A single rework detection result from detect_rework()."""

    sha: str
    full_sha: str
    date: str
    subject: str
    status: str
    age_days: int
    signals: list[str]
    ticket_ids: list[str]
    files_changed: int
    lines_changed: int
    normalized_units: int


class CostResult(TypedDict):
    """Output of calculate()."""

    currency: str
    model_cost: float
    infra_cost: float
    prompting_cost: float
    review_cost: float
    rework_cost: float
    total_cost: float
    merged_prs: int
    reverted_prs: int
    accepted_changes: int
    cost_per_accepted_change: float
    breakdown: dict[str, float]


# ── Summary computation ──────────────────────────────────────────────

def rework_summary(results: list[dict], *, normalize: bool = False) -> ReworkSummary:
    """Canonical rework summary — single source of truth for rework rate.

    When *normalize* is True, each item contributes its ``normalized_units``
    instead of counting as 1. Missing values default to 1 for backward
    compatibility with older rework JSON files.
    """
    if normalize:
        totals: dict[str, int] = {"accepted": 0, "rework": 0, "fix": 0, "pending": 0}
        for r in results:
            status = r["status"]
            if status in totals:
                totals[status] += r.get("normalized_units", 1)
        accepted, rework, fix, pending = totals["accepted"], totals["rework"], totals["fix"], totals["pending"]
    else:
        counts = Counter(r["status"] for r in results)
        accepted = counts["accepted"]
        rework = counts["rework"]
        fix = counts["fix"]
        pending = counts["pending"]

    total = accepted + rework + fix
    rate = (rework + fix) / total * 100 if total > 0 else None
    return ReworkSummary(
        accepted=accepted,
        rework=rework,
        fix=fix,
        pending=pending,
        total_classifiable=total,
        rework_rate=rate,
    )
