"""GitHub GraphQL source adapter vendored from `delivery-gap-signals`."""

from __future__ import annotations

import json
import logging
import re
import subprocess
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ..source_models import CIStatus, Commit, MergedChange, Review

_log = logging.getLogger(__name__)

_EXHAUSTED = object()

_REPO_PATTERN = re.compile(r"^[a-zA-Z0-9._-]+/[a-zA-Z0-9._-]+$")

_BOT_REVIEWERS = {
    "copilot", "github-copilot", "copilot-pull-request-reviewer",
    "copilot-swe-agent", "coderabbitai", "codium-ai",
    "sourcery-ai", "ellipsis-dev", "greptile-bot", "pantheon-ai",
    "promptfoo-scanner", "cubic-dev-ai", "devin-ai-integration",
}
_BOT_PREFIXES = ("copilot-", "coderabbit-", "sourcery-", "pantheon-", "devin-")

_MIN_PAGE_SIZE = 5
_GATEWAY_SIGNALS = ("502", "504", "stream error", "CANCEL", "timed out")
_RATE_LIMIT_SIGNALS = ("rate limit", "API rate limit", "403", "429", "secondary rate", "abuse detection")

_QUERY = """
query($owner: String!, $repo: String!, $after: String, $pageSize: Int!) {
  repository(owner: $owner, name: $repo) {
    pullRequests(
      states: MERGED,
      orderBy: {field: UPDATED_AT, direction: DESC},
      first: $pageSize,
      after: $after
    ) {
      pageInfo {
        hasNextPage
        endCursor
      }
      nodes {
        number
        title
        body
        mergedAt
        createdAt
        lastEditedAt
        totalCommentsCount
        additions
        deletions
        author { login }
        mergeCommit { oid }
        files(first: 100) {
          nodes { path }
        }
        reviews(first: 50) {
          nodes {
            author { login }
            state
            submittedAt
            body
          }
        }
        commits(first: 100) {
          totalCount
          nodes {
            commit {
              message
              author {
                date
              }
              statusCheckRollup {
                contexts(first: 50) {
                  nodes {
                    ... on CheckRun {
                      conclusion
                    }
                    ... on StatusContext {
                      state
                    }
                  }
                }
              }
            }
            oid
          }
        }
      }
    }
  }
}
"""

_TOKEN_PATTERN = re.compile(r"gh[pousr]_[A-Za-z0-9]{10,}|x-access-token:[^@]+@")


def _sanitize_stderr(stderr: str, max_len: int = 200) -> str:
    text = _TOKEN_PATTERN.sub("[REDACTED]", stderr.strip())
    if len(text) > max_len:
        return text[:max_len] + "..."
    return text


def _validate_repo(repo: str) -> None:
    if not _REPO_PATTERN.match(repo):
        raise ValueError(f"Invalid repo format: '{repo}'. Expected 'owner/repo'.")
    if any(part in (".", "..") for part in repo.split("/")):
        raise ValueError(f"Invalid repo format: '{repo}'.")


def _is_bot_reviewer(login: str) -> bool:
    low = login.lower()
    return login.endswith("[bot]") or low in _BOT_REVIEWERS or low.startswith(_BOT_PREFIXES)


def _parse_ci_status(pr_node: dict) -> CIStatus | None:
    commits = pr_node.get("commits", {}).get("nodes", [])
    if not commits:
        return CIStatus.NO_CHECKS

    rollup = (commits[-1].get("commit", {}).get("statusCheckRollup") or {})
    contexts = rollup.get("contexts", {}).get("nodes", [])
    if not contexts:
        return CIStatus.NO_CHECKS

    for ctx in contexts:
        conclusion = (ctx.get("conclusion") or "").lower()
        state = (ctx.get("state") or "").lower()
        if conclusion in ("failure", "timed_out", "cancelled") or state == "failure":
            return CIStatus.FAILED

    return CIStatus.PASSED


def _parse_reviews(pr_node: dict) -> list[Review]:
    reviews = []
    for review in pr_node.get("reviews", {}).get("nodes", []) or []:
        login = (review.get("author") or {}).get("login", "")
        state_raw = (review.get("state") or "").lower()
        state = {
            "approved": "approved",
            "changes_requested": "changes_requested",
            "commented": "commented",
            "dismissed": "commented",
        }.get(state_raw, "commented")

        submitted = review.get("submittedAt") or ""
        if not submitted:
            continue

        reviews.append(Review(
            reviewer=login,
            state=state,
            submitted_at=datetime.fromisoformat(submitted.replace("Z", "+00:00")),
            is_bot=_is_bot_reviewer(login),
            body=(review.get("body") or "")[:2000],
        ))
    return reviews


def _parse_commits(pr_node: dict) -> tuple[list[Commit], int]:
    """Parse commits from the GraphQL response."""
    commits_data = pr_node.get("commits", {})
    total_count = commits_data.get("totalCount", 0)
    commits = []
    for node in commits_data.get("nodes", []) or []:
        commit = node.get("commit", {})
        message = commit.get("message", "")
        author_info = commit.get("author", {}) or {}
        authored_at = None
        date_str = author_info.get("date", "")
        if date_str:
            try:
                authored_at = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
            except (ValueError, TypeError):
                pass
        commits.append(Commit(
            message=message,
            sha=node.get("oid", ""),
            authored_at=authored_at,
        ))
    return commits, total_count


def _is_gateway_error(err: str) -> bool:
    """True if the error looks like a GitHub response-size or gateway timeout."""
    return any(signal in err for signal in _GATEWAY_SIGNALS)


def _run_graphql(query: str, variables: dict, timeout: int = 60) -> dict:
    """Execute a GraphQL query via `gh api graphql`."""
    args = ["gh", "api", "graphql", "-f", f"query={query}"]
    for key, value in variables.items():
        if value is None:
            continue
        if isinstance(value, int):
            args.extend(["-F", f"{key}={value}"])
        else:
            args.extend(["-f", f"{key}={value}"])

    try:
        result = subprocess.run(
            args, capture_output=True, text=True, check=False, timeout=timeout,
        )
    except subprocess.TimeoutExpired as err:
        raise RuntimeError("gh api graphql timed out") from err

    if result.returncode != 0:
        err_text = result.stderr.strip()
        if any(signal in err_text.lower() for signal in _RATE_LIMIT_SIGNALS):
            for attempt in range(1, 100):
                wait = 900
                print(
                    f"\n  *** GitHub rate limit hit (attempt {attempt}). Waiting {wait // 60} minutes... ***",
                    flush=True,
                )
                time.sleep(wait)
                try:
                    result = subprocess.run(
                        args, capture_output=True, text=True, check=False, timeout=timeout,
                    )
                except subprocess.TimeoutExpired as err:
                    raise RuntimeError("gh api graphql timed out (after rate limit wait)") from err
                if result.returncode == 0:
                    data = json.loads(result.stdout)
                    if "errors" not in data:
                        return data
                retry_err = result.stderr.strip()
                if not any(signal in retry_err.lower() for signal in _RATE_LIMIT_SIGNALS):
                    break
        raise RuntimeError(f"GraphQL query failed: {_sanitize_stderr(result.stderr)}")

    data = json.loads(result.stdout)
    if "errors" in data:
        msgs = "; ".join(err.get("message", "") for err in data["errors"])
        raise RuntimeError(f"GraphQL errors: {msgs}")

    return data


def _parse_pr_node(
    pr: dict,
    repo: str,
    lookback_days: int,
    since: datetime | None = None,
    until: datetime | None = None,
) -> MergedChange | None:
    """Convert a GraphQL PR node into a merged change."""
    merged_at = pr.get("mergedAt")
    if not merged_at:
        return None

    merged_dt = datetime.fromisoformat(merged_at.replace("Z", "+00:00"))

    if not since and not until:
        cutoff = datetime.now(timezone.utc) - timedelta(days=lookback_days)
        if merged_dt < cutoff:
            return None

    sha = (pr.get("mergeCommit") or {}).get("oid", "")
    author = (pr.get("author") or {}).get("login", "")
    created_at = None
    if pr.get("createdAt"):
        created_at = datetime.fromisoformat(pr["createdAt"].replace("Z", "+00:00"))

    files = [
        entry.get("path", "")
        for entry in (pr.get("files", {}).get("nodes", []) or [])
        if entry.get("path")
    ]

    commits, commit_count = _parse_commits(pr)

    last_edited_at = None
    if pr.get("lastEditedAt"):
        try:
            last_edited_at = datetime.fromisoformat(pr["lastEditedAt"].replace("Z", "+00:00"))
        except (ValueError, TypeError):
            pass

    return MergedChange.build(
        id=str(pr["number"]),
        source="github_graphql",
        repo=repo,
        title=pr.get("title", ""),
        body=pr.get("body", "") or "",
        author=author,
        merged_at=merged_dt,
        created_at=created_at,
        files=files,
        additions=pr.get("additions", 0) or 0,
        deletions=pr.get("deletions", 0) or 0,
        reviews=_parse_reviews(pr),
        ci_status=_parse_ci_status(pr),
        merge_commit_sha=sha or None,
        pr_number=pr["number"],
        commits=commits,
        commit_count=commit_count,
        last_edited_at=last_edited_at,
        total_comments_count=pr.get("totalCommentsCount", 0) or 0,
    )


def _save_incremental(path: str, changes: list[MergedChange]) -> None:
    """Save fetched PRs after each page using an atomic write-then-rename."""
    output_path = Path(path)
    existing: dict[int | None, dict] = {}
    if output_path.exists():
        try:
            for pr in json.loads(output_path.read_text(encoding="utf-8")):
                existing[pr.get("pr_number")] = pr
        except (json.JSONDecodeError, TypeError) as exc:
            _log.warning("Corrupt incremental file %s, starting fresh: %s", path, exc)

    for change in changes:
        payload = change.to_dict()
        existing[payload.get("pr_number")] = payload

    tmp = output_path.with_suffix(".tmp")
    tmp.write_text(json.dumps(list(existing.values()), indent=2, default=str), encoding="utf-8")
    tmp.rename(output_path)


def fetch_changes(
    repo: str,
    lookback_days: int = 90,
    *,
    limit: int = 0,
    page_size: int = 15,
    incremental_path: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
) -> list[MergedChange]:
    """Fetch merged PRs in the requested time window via GraphQL."""
    _validate_repo(repo)

    owner, name = repo.split("/", 1)

    initial_cursor = None
    if since and until:
        result = _skip_to_window_fast(owner, name, until)
        if result is _EXHAUSTED:
            print("    No PRs found in window, skipping collection", flush=True)
            return []
        initial_cursor = result

    all_changes: list[MergedChange] = []
    cursor: str | None = initial_cursor
    current_page_size = max(_MIN_PAGE_SIZE, page_size)
    consecutive_successes = 0

    while True:
        variables = {
            "owner": owner,
            "repo": name,
            "after": cursor,
            "pageSize": current_page_size,
        }

        print(f"    [{len(all_changes):4d} PRs] page size={current_page_size}...", end="", flush=True)

        try:
            data = _run_graphql(_QUERY, variables)
        except RuntimeError as exc:
            print(" FAILED", flush=True)
            if _is_gateway_error(str(exc)) and current_page_size > _MIN_PAGE_SIZE:
                current_page_size = max(_MIN_PAGE_SIZE, current_page_size // 2)
                consecutive_successes = 0
                print(f"    backing down to size={current_page_size}", flush=True)
                continue
            raise

        prs_data = data.get("data", {}).get("repository", {}).get("pullRequests", {})
        nodes = prs_data.get("nodes", [])
        page_info = prs_data.get("pageInfo", {})
        print(f" +{len(nodes)} = {len(all_changes) + len(nodes)} total", flush=True)

        added_this_page = 0
        any_before_since = False
        for pr in nodes:
            merged_at = pr.get("mergedAt")
            if merged_at and since:
                merged_dt = datetime.fromisoformat(merged_at.replace("Z", "+00:00"))
                if merged_dt < since:
                    any_before_since = True

            change = _parse_pr_node(pr, repo, lookback_days, since=since, until=until)
            if change is not None:
                all_changes.append(change)
                added_this_page += 1
                if 0 < limit <= len(all_changes):
                    if incremental_path:
                        _save_incremental(incremental_path, all_changes[:limit])
                    return all_changes[:limit]

        if incremental_path and all_changes:
            _save_incremental(incremental_path, all_changes)

        if any_before_since:
            break

        if nodes and added_this_page == 0 and not since:
            break

        if not page_info.get("hasNextPage"):
            break

        cursor = page_info.get("endCursor")
        if not cursor:
            break

        consecutive_successes += 1
        if consecutive_successes >= 3 and current_page_size < page_size:
            current_page_size = min(page_size, current_page_size * 2)
            consecutive_successes = 0

    return all_changes


_SKIP_QUERY = """
query($owner: String!, $repo: String!, $after: String, $pageSize: Int!) {
  repository(owner: $owner, name: $repo) {
    pullRequests(
      states: MERGED,
      orderBy: {field: UPDATED_AT, direction: DESC},
      first: $pageSize,
      after: $after
    ) {
      pageInfo { hasNextPage endCursor }
      nodes { mergedAt }
    }
  }
}
"""


def _skip_to_window_fast(owner: str, name: str, until: datetime) -> str | None | object:
    """Jump ahead in large pages to find PRs near `until`."""
    cursor: str | None = None
    prev_cursor: str | None = None
    skip_size = 100
    total_skipped = 0
    target_str = until.strftime("%Y-%m-%d")

    print(f"    Skipping to {target_str}...", end="", flush=True)

    while True:
        variables = {
            "owner": owner,
            "repo": name,
            "after": cursor,
            "pageSize": skip_size,
        }

        try:
            data = _run_graphql(_SKIP_QUERY, variables)
        except RuntimeError as exc:
            if _is_gateway_error(str(exc)) and skip_size > 25:
                skip_size = skip_size // 2
                continue
            print(f" failed ({str(exc)[:40]}), starting from beginning", flush=True)
            return None

        prs_data = data.get("data", {}).get("repository", {}).get("pullRequests", {})
        nodes = prs_data.get("nodes", [])
        page_info = prs_data.get("pageInfo", {})

        if not nodes:
            print(f" exhausted all PRs ({total_skipped} skipped), no PRs in window", flush=True)
            return _EXHAUSTED

        min_merged = None
        for pr in nodes:
            merged_at = pr.get("mergedAt")
            if merged_at:
                merged_dt = datetime.fromisoformat(merged_at.replace("Z", "+00:00"))
                if min_merged is None or merged_dt < min_merged:
                    min_merged = merged_dt

        total_skipped += len(nodes)

        if min_merged and min_merged <= until:
            print(
                f" found target at ~{total_skipped} PRs (oldest on page: {min_merged.strftime('%Y-%m-%d')})",
                flush=True,
            )
            return prev_cursor

        prev_cursor = cursor
        if not page_info.get("hasNextPage"):
            print(f" reached end of repo ({total_skipped} PRs), no PRs in window", flush=True)
            return _EXHAUSTED

        cursor = page_info.get("endCursor")
        if not cursor:
            return prev_cursor

        if total_skipped % 500 < skip_size:
            min_str = min_merged.strftime("%Y-%m-%d") if min_merged else "?"
            print(f" {total_skipped} skipped (at {min_str})...", end="", flush=True)
