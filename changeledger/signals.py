"""
Pure signal detection functions for rework analysis.

These are stateless, testable functions that classify commit messages
and file paths. No I/O, no subprocess calls.
"""

import re

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

REVERT_PATTERN = re.compile(
    r'revert\s+"?(.+?)"?\s*$|^Revert\s+"(.+?)"|This reverts commit ([0-9a-f]{7,40})',
    re.IGNORECASE | re.MULTILINE,
)

FIXES_TRAILER = re.compile(r"^Fixes:\s+([0-9a-f]{7,40})", re.MULTILINE)

REVERT_PR_PATTERN = re.compile(
    r"(?:revert(?:s|ed|ing)?)\s+#(\d+)",
    re.IGNORECASE,
)

# ── File classification ──────────────────────────────────────────────

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


# ── Pure functions ───────────────────────────────────────────────────

def is_source_file(path: str) -> bool:
    basename = path.split("/")[-1] if "/" in path else path
    if basename in IGNORE_FILES:
        return False
    return not IGNORE_DIR_PATTERNS.search(path)


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


def extract_revert_pr_numbers(text: str) -> set[int]:
    """Extract PR numbers from revert messages (e.g., 'Revert #42', 'Reverts #42')."""
    return {int(m.group(1)) for m in REVERT_PR_PATTERN.finditer(text)}


# ── PR number extraction from git subjects ───────────────────────────

_MERGE_PR_PATTERN = re.compile(r"^Merge pull request #(\d+)\b")
_SQUASH_PR_PATTERN = re.compile(r"\(#(\d+)\)\s*$")


def extract_pr_number_from_subject(subject: str) -> int | None:
    """Extract PR number from a local git merge commit subject.

    Handles two common GitHub merge strategies:
    - Merge commit: "Merge pull request #42 from owner/branch"
    - Squash merge: "feat: add thing (#42)"
    """
    m = _MERGE_PR_PATTERN.search(subject)
    if m:
        return int(m.group(1))
    m = _SQUASH_PR_PATTERN.search(subject)
    if m:
        return int(m.group(1))
    return None
