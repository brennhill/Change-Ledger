"""
Rework detector — identifies accepted vs reworked changes from git history.

Scans the default branch merge history and classifies each change as
accepted, rework, or pending based on a configurable observation window.

A change is "rework" if a subsequent merge within the window matches:
  1. Explicit git revert of the original commit
  2. Touches the same source files AND has fix/hotfix/bugfix/patch in the message
  3. References the same ticket ID (JIRA-123, #123, ENG-123, etc.)
  4. Contains a Fixes: trailer pointing to the original SHA
"""

from __future__ import annotations

import contextlib
import json
import math
import re
import subprocess
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

from .errors import ChangeledgerError

if TYPE_CHECKING:
    from .models import Commit

REPO_PATTERN = re.compile(r"^[a-zA-Z0-9._-]+/[a-zA-Z0-9._-]+$")


_TOKEN_PATTERN = re.compile(r"gh[pousr]_[A-Za-z0-9]{10,}|x-access-token:[^@]+@")


def _sanitize_stderr(stderr: str, max_len: int = 200) -> str:
    """Truncate and redact subprocess stderr to avoid leaking auth tokens."""
    text = _TOKEN_PATTERN.sub("[REDACTED]", stderr.strip())
    if len(text) > max_len:
        return text[:max_len] + "..."
    return text


def validate_repo(repo: str) -> None:
    """Validate --repo format to prevent API path injection."""
    if not REPO_PATTERN.match(repo):
        raise ChangeledgerError(
            f"Invalid repo format: '{repo}'. Expected 'owner/repo'."
        )
    parts = repo.split("/")
    if any(p in (".", "..") for p in parts):
        raise ChangeledgerError(
            f"Invalid repo format: '{repo}'. Owner and repo must not be '.' or '..'."
        )


# ── Git data fetching ────────────────────────────────────────────────

def get_merges_local(lookback_days: int) -> list[Commit]:
    """Get merge commits from local git repo.

    Uses a single git log call with --name-only to avoid N+1 subprocess overhead.
    """
    from .models import Commit

    since = (datetime.now(timezone.utc) - timedelta(days=lookback_days)).strftime("%Y-%m-%d")
    try:
        result = subprocess.run(
            [
                "git", "log", "--first-parent", f"--since={since}",
                "--format=%x1e%H%x1f%aI%x1f%s%x1f%b%x1f", "--numstat", "-z",
            ],
            capture_output=True, text=True, check=False, timeout=60,
        )
    except subprocess.TimeoutExpired as err:
        raise ChangeledgerError("git log timed out after 60 seconds") from err
    if result.returncode != 0:
        raise ChangeledgerError(f"git log failed: {_sanitize_stderr(result.stderr)}")

    commits: list[Commit] = []
    # Record separator (\x1e) separates commits. Unit separator (\x1f)
    # separates commit metadata from the free-form subject/body text.
    for chunk in result.stdout.split("\x1e"):
        chunk = chunk.strip("\x00\n")
        if not chunk:
            continue

        parts = chunk.split("\x1f", 4)
        if len(parts) < 5:
            continue

        sha, date_str, subject, body, stats_blob = parts

        # --numstat -z gives: additions\tdeletions\tfilename\0 per file
        files: set[str] = set()
        total_additions = 0
        total_deletions = 0
        for entry in stats_blob.lstrip("\x00\n").split("\x00"):
            entry = entry.strip()
            if not entry:
                continue
            # numstat lines: "adds\tdels\tpath"
            stat_parts = entry.split("\t", 2)
            if len(stat_parts) == 3:
                adds_str, dels_str, path = stat_parts
                if path:
                    files.add(path)
                # Binary files show "-" for adds/dels
                with contextlib.suppress(ValueError):
                    total_additions += int(adds_str)
                with contextlib.suppress(ValueError):
                    total_deletions += int(dels_str)
            elif entry:
                # Fallback: bare filename (shouldn't happen with --numstat)
                files.add(entry)

        from .signals import extract_pr_number_from_subject
        commits.append(Commit.build(
            sha=sha,
            date=datetime.fromisoformat(date_str),
            subject=subject,
            body=body,
            files=files,
            lines_changed=total_additions + total_deletions,
            pr_number=extract_pr_number_from_subject(subject),
        ))

    return commits


def get_merges_github(repo: str, lookback_days: int) -> list[Commit]:
    """Get merged PRs from GitHub API."""
    from .models import Commit

    validate_repo(repo)

    since = (datetime.now(timezone.utc) - timedelta(days=lookback_days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        result = subprocess.run(
            [
                "gh", "pr", "list", "--repo", repo,
                "--state", "merged", "--limit", "500",
                "--search", f"merged:>={since[:10]}",
                "--json", "number,title,mergedAt,files,mergeCommit,body,additions,deletions",
            ],
            capture_output=True, text=True, check=False, timeout=60,
        )
    except subprocess.TimeoutExpired as err:
        raise ChangeledgerError("gh pr list timed out after 60 seconds") from err
    if result.returncode != 0:
        raise ChangeledgerError(f"gh pr list failed: {_sanitize_stderr(result.stderr)}")

    prs = json.loads(result.stdout)
    if len(prs) == 500:
        raise ChangeledgerError(
            "gh pr list reached the 500 PR limit for this lookback window. "
            "Narrow the lookback or add pagination before trusting this result."
        )
    commits: list[Commit] = []

    for pr in prs:
        title = pr.get("title", "")
        sha = (pr.get("mergeCommit") or {}).get("oid", "")
        merged_at = pr.get("mergedAt", "")
        files = set(f.get("path", "") for f in pr.get("files", []))
        body = pr.get("body", "") or ""
        additions = pr.get("additions", 0) or 0
        deletions = pr.get("deletions", 0) or 0

        if not merged_at:
            continue

        if not sha:
            # Use PR number as fallback identifier for PRs without merge commits
            sha = f"PR#{pr['number']}"

        commits.append(Commit.build(
            sha=sha,
            date=datetime.fromisoformat(merged_at.replace("Z", "+00:00")),
            subject=title,
            body=body,
            files=files,
            lines_changed=additions + deletions,
            pr_number=pr["number"],
        ))

    return commits


# ── Rework detection ─────────────────────────────────────────────────

def detect_rework(commits: list[Commit], window_days: int) -> list[dict]:  # -> list[ReworkItem]

    now = datetime.now(timezone.utc)
    window = timedelta(days=window_days)

    sorted_commits = sorted(commits, key=lambda c: c.date)

    # Track commits consumed as fixes/reverts so they aren't double-counted
    consumed_as_fix: set[int] = set()

    results = []

    for i, original in enumerate(sorted_commits):
        age = now - original.date
        rework_signals = []

        for j in range(i + 1, len(sorted_commits)):
            if j in consumed_as_fix:
                continue
            candidate = sorted_commits[j]

            if (candidate.date - original.date) > window:
                break

            # Signal 1a: Revert referencing PR number (GitHub mode)
            if candidate.is_revert and original.pr_number is not None:
                from .signals import extract_revert_pr_numbers
                revert_prs = extract_revert_pr_numbers(candidate.message)
                if original.pr_number in revert_prs:
                    rework_signals.append(f"Reverted by {candidate.short_sha}")
                    consumed_as_fix.add(j)
                    continue

            # Signal 1b: Explicit revert (SHA or subject match)
            if candidate.is_revert and (
                original.sha_in_text(candidate.message)
                or original.subject in candidate.message
            ):
                rework_signals.append(f"Reverted by {candidate.short_sha}")
                consumed_as_fix.add(j)
                continue

            # Signal 2: Fixes: trailer pointing to this commit
            if candidate.fixes_sha and original.sha.startswith(candidate.fixes_sha):
                rework_signals.append(f"Fixes: trailer in {candidate.short_sha}")
                consumed_as_fix.add(j)
                continue

            # Signal 3: Same ticket ID AND is a fix
            if candidate.ticket_ids and original.ticket_ids:
                shared = candidate.ticket_ids & original.ticket_ids
                if shared and candidate.is_fix:
                    rework_signals.append(
                        f"Same ticket {', '.join(sorted(shared))} fixed by {candidate.short_sha}"
                    )
                    consumed_as_fix.add(j)
                    continue

            # Signal 4: Same source files AND is a fix
            if candidate.is_fix and candidate.src_files and original.src_files:
                overlap = original.src_files & candidate.src_files
                if len(candidate.src_files) > 0 and len(overlap) / len(candidate.src_files) > 0.5:
                    rework_signals.append(
                        f"Fix {candidate.short_sha} touches same source files: {', '.join(list(overlap)[:3])}"
                    )
                    consumed_as_fix.add(j)
                    continue

        # Classify — rework takes priority over fix status.
        # A commit that fixes one thing but gets reverted itself is rework.
        if age <= window:
            status = "pending"
        elif rework_signals:
            status = "rework"
        elif i in consumed_as_fix:
            status = "fix"
        else:
            status = "accepted"

        loc = getattr(original, "lines_changed", 0)
        results.append({
            "sha": original.short_sha,
            "full_sha": original.sha,
            "pr_number": original.pr_number,
            "date": original.date.strftime("%Y-%m-%d"),
            "subject": original.subject[:80],
            "status": status,
            "age_days": age.days,
            "signals": rework_signals,
            "ticket_ids": sorted(original.ticket_ids),
            "files_changed": len(original.files),
            "lines_changed": loc,
            "normalized_units": max(1, math.ceil(loc / 500)) if loc > 0 else 1,
        })

    return results


def scan(repo: str | None, lookback: int, window: int) -> list[dict]:  # -> list[ReworkItem]
    """Fetch commits and run rework detection. Pure data, no I/O."""
    if repo:
        commits = get_merges_github(repo, lookback)
    else:
        commits = get_merges_local(lookback)

    if not commits:
        return []

    return detect_rework(commits, window)


def run_scan(repo: str | None, lookback: int, window: int) -> list[dict]:
    """CLI wrapper: scan + print progress and report."""
    print(f"Scanning {'GitHub ' + repo if repo else 'local repo'}...")
    print(f"Window: {window} days, lookback: {lookback} days")

    results = scan(repo, lookback, window)

    if not results:
        print("No commits found in the lookback period.")
        return []

    print(f"Analyzed {len(results)} commits.")
    print_report(results, window)
    return results


def print_report(results: list[dict], window_days: int):
    """Print rework detection report. Delegates to output.print_rework_report."""
    from .output import print_rework_report
    print_rework_report(results, window_days)
