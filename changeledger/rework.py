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
import sys
from datetime import datetime, timedelta, timezone


# ── Ticket ID patterns ───────────────────────────────────────────────

TICKET_PATTERNS = [
    re.compile(r"[A-Z]{2,10}-\d+"),
    re.compile(r"#(\d+)"),
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

IGNORE_PATTERNS = re.compile(
    r"(^\.github/|^docs/|^\.vscode/|manifest\.json$|\.lock$|\.sum$|"
    r"\.md$|\.txt$|\.yml$|\.yaml$|\.toml$|\.cfg$|\.ini$)",
    re.IGNORECASE,
)

REVERT_PATTERN = re.compile(
    r'revert\s+"?(.+?)"?\s*$|^Revert\s+"(.+?)"',
    re.IGNORECASE | re.MULTILINE,
)

FIXES_TRAILER = re.compile(r"^Fixes:\s+([0-9a-f]{7,40})", re.MULTILINE)


def is_source_file(path: str) -> bool:
    basename = path.split("/")[-1] if "/" in path else path
    if basename in IGNORE_FILES:
        return False
    if IGNORE_PATTERNS.search(path):
        return False
    return True


def extract_ticket_ids(text: str) -> set:
    ids = set()
    for pattern in TICKET_PATTERNS:
        for match in pattern.finditer(text):
            ids.add(match.group(0).upper())
    return ids


def is_fix_message(text: str) -> bool:
    return bool(FIX_PATTERNS.search(text))


def extract_fixes_sha(text: str) -> str | None:
    m = FIXES_TRAILER.search(text)
    return m.group(1) if m else None


def is_revert_message(text: str) -> bool:
    return bool(REVERT_PATTERN.search(text))


# ── Git data fetching ────────────────────────────────────────────────

def get_merges_local(lookback_days: int) -> list[dict]:
    since = (datetime.now() - timedelta(days=lookback_days)).strftime("%Y-%m-%d")
    result = subprocess.run(
        [
            "git", "log", "--first-parent", f"--since={since}",
            "--format=%H|%aI|%s|%b%x00",
        ],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        print(f"git log failed: {result.stderr}", file=sys.stderr)
        sys.exit(1)

    commits = []
    for entry in result.stdout.split("\x00"):
        entry = entry.strip()
        if not entry:
            continue
        parts = entry.split("|", 3)
        if len(parts) < 3:
            continue
        sha = parts[0]
        date_str = parts[1]
        subject = parts[2]
        body = parts[3] if len(parts) > 3 else ""
        message = f"{subject}\n{body}".strip()

        files_result = subprocess.run(
            ["git", "diff-tree", "--no-commit-id", "--name-only", "-r", sha],
            capture_output=True, text=True,
        )
        files = set(files_result.stdout.strip().split("\n")) if files_result.stdout.strip() else set()

        commits.append({
            "sha": sha,
            "short_sha": sha[:10],
            "date": datetime.fromisoformat(date_str),
            "subject": subject,
            "message": message,
            "files": files,
            "ticket_ids": extract_ticket_ids(message),
            "is_fix": is_fix_message(message),
            "is_revert": is_revert_message(message),
            "fixes_sha": extract_fixes_sha(message),
        })

    return commits


def get_merges_github(repo: str, lookback_days: int) -> list[dict]:
    since = (datetime.now(timezone.utc) - timedelta(days=lookback_days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    result = subprocess.run(
        [
            "gh", "pr", "list", "--repo", repo,
            "--state", "merged", "--limit", "500",
            "--search", f"merged:>={since[:10]}",
            "--json", "number,title,mergedAt,files,mergeCommit",
        ],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        print(f"gh pr list failed: {result.stderr}", file=sys.stderr)
        sys.exit(1)

    prs = json.loads(result.stdout)
    commits = []

    for pr in prs:
        title = pr.get("title", "")
        sha = pr.get("mergeCommit", {}).get("oid", "")
        merged_at = pr.get("mergedAt", "")
        files = set(f.get("path", "") for f in pr.get("files", []))

        body = ""
        if sha:
            body_result = subprocess.run(
                ["gh", "api", f"repos/{repo}/commits/{sha}", "--jq", ".commit.message"],
                capture_output=True, text=True,
            )
            if body_result.returncode == 0:
                body = body_result.stdout.strip()

        message = f"{title}\n{body}".strip()

        commits.append({
            "sha": sha,
            "short_sha": sha[:10] if sha else f"PR#{pr['number']}",
            "pr_number": pr["number"],
            "date": datetime.fromisoformat(merged_at.replace("Z", "+00:00")) if merged_at else datetime.now(timezone.utc),
            "subject": title,
            "message": message,
            "files": files,
            "ticket_ids": extract_ticket_ids(message),
            "is_fix": is_fix_message(message),
            "is_revert": is_revert_message(message),
            "fixes_sha": extract_fixes_sha(message),
        })

    return commits


# ── Rework detection ─────────────────────────────────────────────────

def detect_rework(commits: list[dict], window_days: int) -> list[dict]:
    now = datetime.now(timezone.utc)
    for c in commits:
        if c["date"].tzinfo is None:
            c["date"] = c["date"].replace(tzinfo=timezone.utc)

    commits.sort(key=lambda c: c["date"])
    results = []

    for i, original in enumerate(commits):
        age_days = (now - original["date"]).days
        rework_signals = []

        for j in range(i + 1, len(commits)):
            candidate = commits[j]
            delta_days = (candidate["date"] - original["date"]).days

            if delta_days > window_days:
                break

            if candidate["is_revert"] and (
                original["short_sha"] in candidate["message"]
                or original["subject"] in candidate["message"]
            ):
                rework_signals.append(f"Reverted by {candidate['short_sha']}")
                continue

            if candidate["fixes_sha"] and original["sha"].startswith(candidate["fixes_sha"]):
                rework_signals.append(f"Fixes: trailer in {candidate['short_sha']}")
                continue

            if candidate["ticket_ids"] and original["ticket_ids"]:
                shared = candidate["ticket_ids"] & original["ticket_ids"]
                if shared and candidate["is_fix"]:
                    rework_signals.append(
                        f"Same ticket {', '.join(shared)} fixed by {candidate['short_sha']}"
                    )
                    continue

            if candidate["is_fix"] and candidate["files"] and original["files"]:
                orig_src = {f for f in original["files"] if is_source_file(f)}
                cand_src = {f for f in candidate["files"] if is_source_file(f)}
                overlap = orig_src & cand_src
                if len(cand_src) > 0 and len(overlap) / len(cand_src) > 0.5:
                    rework_signals.append(
                        f"Fix {candidate['short_sha']} touches same source files: {', '.join(list(overlap)[:3])}"
                    )
                    continue

        if age_days < window_days:
            status = "pending"
        elif rework_signals:
            status = "rework"
        else:
            status = "accepted"

        results.append({
            "sha": original["short_sha"],
            "date": original["date"].strftime("%Y-%m-%d"),
            "subject": original["subject"][:80],
            "status": status,
            "age_days": age_days,
            "signals": rework_signals,
            "ticket_ids": list(original["ticket_ids"]),
            "files_changed": len(original["files"]),
        })

    return results


def print_report(results: list[dict], window_days: int):
    accepted = [r for r in results if r["status"] == "accepted"]
    rework = [r for r in results if r["status"] == "rework"]
    pending = [r for r in results if r["status"] == "pending"]

    print()
    print("=" * 60)
    print(f" REWORK DETECTION REPORT ({window_days}-day window)")
    print("=" * 60)
    print()
    print(f"  Accepted:  {len(accepted)}")
    print(f"  Rework:    {len(rework)}")
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
