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

import json
import re
import subprocess
from datetime import datetime, timedelta, timezone

from .cost import ChangeledgerError


# ── Ticket ID patterns ───────────────────────────────────────────────

TICKET_PATTERNS = [
    re.compile(r"[A-Z]{2,10}-\d+"),          # JIRA/Linear: PROJ-123
    re.compile(r"#(\d+)"),                     # GitHub/GitLab: #123
    re.compile(r"(?:fixes|closes|resolves)\s+#(\d+)", re.IGNORECASE),
]

FIX_PATTERNS = re.compile(
    r"^(fix|hotfix|bugfix|patch|revert)[\s(:!/]",
    re.IGNORECASE | re.MULTILINE,
)

IGNORE_FILES = {
    "README.md", "CHANGELOG.md", "CHANGES.md", "HISTORY.md",
    "package.json", "package-lock.json", "yarn.lock", "pnpm-lock.yaml",
    "Cargo.lock", "go.sum", "Gemfile.lock", "poetry.lock",
    "requirements.txt", "Pipfile.lock",
    ".gitignore", ".eslintrc.js", ".eslintrc.json", ".prettierrc",
    "tsconfig.json", "jest.config.js", "jest.config.ts",
    "Makefile", "Dockerfile", "docker-compose.yml",
}

# Only filter files under known non-source directories, not by extension globally.
# This avoids breaking IaC repos where .yml/.toml are primary source files.
IGNORE_DIR_PATTERNS = re.compile(
    r"(^\.github/|^docs/|^\.vscode/|\.lock$|\.sum$)",
    re.IGNORECASE,
)

REVERT_PATTERN = re.compile(
    r'revert\s+"?(.+?)"?\s*$|^Revert\s+"(.+?)"|This reverts commit ([0-9a-f]{7,40})',
    re.IGNORECASE | re.MULTILINE,
)

FIXES_TRAILER = re.compile(r"^Fixes:\s+([0-9a-f]{7,40})", re.MULTILINE)

REPO_PATTERN = re.compile(r"^[a-zA-Z0-9._-]+/[a-zA-Z0-9._-]+$")


def is_source_file(path: str) -> bool:
    basename = path.split("/")[-1] if "/" in path else path
    if basename in IGNORE_FILES:
        return False
    if IGNORE_DIR_PATTERNS.search(path):
        return False
    return True


def extract_ticket_ids(text: str) -> set:
    """Extract normalized ticket IDs from a commit message."""
    ids = set()
    for pattern in TICKET_PATTERNS:
        for match in pattern.finditer(text):
            # Use capture group if present, else full match
            captured = match.group(1) if pattern.groups and match.group(1) else match.group(0)
            # Normalize: prefix bare numbers with # for consistency
            normalized = captured.upper()
            if normalized.isdigit():
                normalized = f"#{normalized}"
            ids.add(normalized)
    return ids


def is_fix_message(text: str) -> bool:
    return bool(FIX_PATTERNS.search(text))


def extract_fixes_sha(text: str) -> str | None:
    m = FIXES_TRAILER.search(text)
    return m.group(1) if m else None


def is_revert_message(text: str) -> bool:
    return bool(REVERT_PATTERN.search(text))


def validate_repo(repo: str) -> None:
    """Validate --repo format to prevent API path injection."""
    if not REPO_PATTERN.match(repo):
        raise ChangeledgerError(
            f"Invalid repo format: '{repo}'. Expected 'owner/repo'."
        )


# ── Git data fetching ────────────────────────────────────────────────

def get_merges_local(lookback_days: int) -> list[dict]:
    """Get merge commits from local git repo.

    Uses a single git log call with --name-only to avoid N+1 subprocess overhead.
    """
    since = (datetime.now(timezone.utc) - timedelta(days=lookback_days)).strftime("%Y-%m-%d")
    result = subprocess.run(
        [
            "git", "log", "--first-parent", f"--since={since}",
            "--format=%x00%H|%aI|%s|%b", "--name-only",
        ],
        capture_output=True, text=True, check=False,
    )
    if result.returncode != 0:
        raise ChangeledgerError(f"git log failed: {result.stderr.strip()}")

    commits = []
    # Split on null byte to separate commits. Each chunk has the format line
    # followed by file names (from --name-only).
    for chunk in result.stdout.split("\x00"):
        chunk = chunk.strip()
        if not chunk:
            continue

        lines = chunk.split("\n")
        header = lines[0]
        parts = header.split("|", 3)
        if len(parts) < 3:
            continue

        sha = parts[0]
        date_str = parts[1]
        subject = parts[2]
        body = parts[3] if len(parts) > 3 else ""
        message = f"{subject}\n{body}".strip()

        # Remaining lines are file names from --name-only
        files = {line.strip() for line in lines[1:] if line.strip()}

        commits.append({
            "sha": sha,
            "short_sha": sha[:10],
            "date": datetime.fromisoformat(date_str),
            "subject": subject,
            "message": message,
            "files": files,
            "src_files": {f for f in files if is_source_file(f)},
            "ticket_ids": extract_ticket_ids(message),
            "is_fix": is_fix_message(message),
            "is_revert": is_revert_message(message),
            "fixes_sha": extract_fixes_sha(message),
        })

    return commits


def get_merges_github(repo: str, lookback_days: int) -> list[dict]:
    """Get merged PRs from GitHub API."""
    validate_repo(repo)

    since = (datetime.now(timezone.utc) - timedelta(days=lookback_days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    result = subprocess.run(
        [
            "gh", "pr", "list", "--repo", repo,
            "--state", "merged", "--limit", "500",
            "--search", f"merged:>={since[:10]}",
            "--json", "number,title,mergedAt,files,mergeCommit,body",
        ],
        capture_output=True, text=True, check=False,
    )
    if result.returncode != 0:
        raise ChangeledgerError(f"gh pr list failed: {result.stderr.strip()}")

    prs = json.loads(result.stdout)
    commits = []

    for pr in prs:
        title = pr.get("title", "")
        sha = pr.get("mergeCommit", {}).get("oid", "")
        merged_at = pr.get("mergedAt", "")
        files = set(f.get("path", "") for f in pr.get("files", []))
        body = pr.get("body", "") or ""

        if not merged_at:
            # Skip PRs with missing merge date rather than guessing "now"
            continue

        message = f"{title}\n{body}".strip()

        commits.append({
            "sha": sha,
            "short_sha": sha[:10] if sha else f"PR#{pr['number']}",
            "pr_number": pr["number"],
            "date": datetime.fromisoformat(merged_at.replace("Z", "+00:00")),
            "subject": title,
            "message": message,
            "files": files,
            "src_files": {f for f in files if is_source_file(f)},
            "ticket_ids": extract_ticket_ids(message),
            "is_fix": is_fix_message(message),
            "is_revert": is_revert_message(message),
            "fixes_sha": extract_fixes_sha(message),
        })

    return commits


# ── Rework detection ─────────────────────────────────────────────────

def detect_rework(commits: list[dict], window_days: int) -> list[dict]:
    now = datetime.now(timezone.utc)
    window = timedelta(days=window_days)

    for c in commits:
        if c["date"].tzinfo is None:
            c["date"] = c["date"].replace(tzinfo=timezone.utc)

    commits.sort(key=lambda c: c["date"])

    # Track commits consumed as fixes/reverts so they aren't double-counted
    consumed_as_fix: set[int] = set()

    results = []

    for i, original in enumerate(commits):
        age = now - original["date"]
        rework_signals = []

        for j in range(i + 1, len(commits)):
            candidate = commits[j]

            if (candidate["date"] - original["date"]) >= window:
                break

            # Signal 1: Explicit revert
            if candidate["is_revert"] and (
                original["short_sha"] in candidate["message"]
                or original["sha"] in candidate["message"]
                or original["subject"] in candidate["message"]
            ):
                rework_signals.append(f"Reverted by {candidate['short_sha']}")
                consumed_as_fix.add(j)
                continue

            # Signal 2: Fixes: trailer pointing to this commit
            if candidate["fixes_sha"] and original["sha"].startswith(candidate["fixes_sha"]):
                rework_signals.append(f"Fixes: trailer in {candidate['short_sha']}")
                consumed_as_fix.add(j)
                continue

            # Signal 3: Same ticket ID AND is a fix
            if candidate["ticket_ids"] and original["ticket_ids"]:
                shared = candidate["ticket_ids"] & original["ticket_ids"]
                if shared and candidate["is_fix"]:
                    rework_signals.append(
                        f"Same ticket {', '.join(shared)} fixed by {candidate['short_sha']}"
                    )
                    consumed_as_fix.add(j)
                    continue

            # Signal 4: Same source files AND is a fix (pre-computed src_files)
            if candidate["is_fix"] and candidate.get("src_files") and original.get("src_files"):
                overlap = original["src_files"] & candidate["src_files"]
                if len(candidate["src_files"]) > 0 and len(overlap) / len(candidate["src_files"]) > 0.5:
                    rework_signals.append(
                        f"Fix {candidate['short_sha']} touches same source files: {', '.join(list(overlap)[:3])}"
                    )
                    consumed_as_fix.add(j)
                    continue

        # Classify
        if age < window:
            status = "pending"
        elif i in consumed_as_fix:
            status = "fix"  # This commit was a fix for something else
        elif rework_signals:
            status = "rework"
        else:
            status = "accepted"

        results.append({
            "sha": original["short_sha"],
            "full_sha": original["sha"],
            "date": original["date"].strftime("%Y-%m-%d"),
            "subject": original["subject"][:80],
            "status": status,
            "age_days": age.days,
            "signals": rework_signals,
            "ticket_ids": list(original["ticket_ids"]),
            "files_changed": len(original["files"]),
        })

    return results


def print_report(results: list[dict], window_days: int):
    accepted = [r for r in results if r["status"] == "accepted"]
    rework = [r for r in results if r["status"] == "rework"]
    fixes = [r for r in results if r["status"] == "fix"]
    pending = [r for r in results if r["status"] == "pending"]

    print()
    print("=" * 60)
    print(f" REWORK DETECTION REPORT ({window_days}-day window)")
    print("=" * 60)
    print()
    print(f"  Accepted:  {len(accepted)}")
    print(f"  Rework:    {len(rework)}")
    print(f"  Fixes:     {len(fixes)} (fix commits, not counted as accepted)")
    print(f"  Pending:   {len(pending)} (< {window_days} days old)")
    print()

    total_classifiable = len(accepted) + len(rework)
    if total_classifiable > 0:
        rate = len(rework) / total_classifiable * 100
        print(f"  Rework rate: {rate:.1f}%")
    else:
        print("  Rework rate: N/A (no changes old enough to classify)")
    print()

    if rework:
        print("  REWORKED CHANGES:")
        print("  " + "-" * 56)
        for r in rework:
            print(f"  {r['sha']}  {r['date']}  {r['subject'][:50]}")
            for signal in r["signals"]:
                print(f"    -> {signal}")
        print()
