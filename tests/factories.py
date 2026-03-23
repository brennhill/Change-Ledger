"""Shared test factories for changeledger tests."""

from datetime import datetime, timedelta, timezone


def make_cost_inputs(**overrides) -> dict:
    """Build a minimal valid cost input dict with optional overrides."""
    data = {
        "model_cost": 1,
        "infra_cost": 0,
        "prompting_hours": 0,
        "review_hours": 0,
        "rework_hours": 0,
        "burdened_rate": 1,
        "merged_prs": 1,
        "reverted_prs": 0,
    }
    data.update(overrides)
    return data


def make_commit(
    sha: str = "a" * 40,
    subject: str = "test commit",
    date: datetime | None = None,
    files: set[str] | None = None,
    *,
    body: str = "",
    lines_changed: int = 0,
    is_fix: bool = False,
    is_revert: bool = False,
    fixes_sha: str | None = None,
    pr_number: int | None = None,
):
    """Build a Commit via Commit.build with sensible defaults."""
    from changeledger.models import Commit

    if date is None:
        date = datetime.now(timezone.utc) - timedelta(days=30)
    if files is None:
        files = {"src/app.py"}

    if fixes_sha:
        body = f"Fixes: {fixes_sha}"
    if is_fix:
        subject = f"fix: {subject}"
    if is_revert:
        subject = f'Revert "{subject}"'

    return Commit.build(
        sha=sha,
        date=date,
        subject=subject,
        body=body,
        files=files,
        lines_changed=lines_changed,
        pr_number=pr_number,
    )
