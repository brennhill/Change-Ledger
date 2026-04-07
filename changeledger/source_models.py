"""Local copy of the source-adapter data models used by changeledger."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

from .signals import extract_ticket_ids


class CIStatus(str, Enum):
    """Aggregate CI result for a change."""

    PASSED = "passed"
    FAILED = "failed"
    NO_CHECKS = "no_checks"


@dataclass(frozen=True)
class Review:
    """A single review on a change."""

    reviewer: str
    state: str
    submitted_at: datetime
    is_bot: bool = False
    body: str = ""


@dataclass(frozen=True)
class Commit:
    """A single commit contained in a merged change."""

    message: str
    sha: str = ""
    authored_at: datetime | None = None


@dataclass(frozen=True)
class MergedChange:
    """Universal merged-change unit returned by local source adapters."""

    id: str
    source: str
    repo: str
    title: str
    body: str
    author: str
    merged_at: datetime
    created_at: datetime | None = None
    files: list[str] = field(default_factory=list)
    additions: int = 0
    deletions: int = 0
    ticket_ids: frozenset[str] = field(default_factory=frozenset)
    reviews: list[Review] | None = None
    ci_status: CIStatus | None = None
    commits: list[Commit] = field(default_factory=list)
    commit_count: int = 0
    last_edited_at: datetime | None = None
    total_comments_count: int = 0
    merge_commit_sha: str | None = None
    pr_number: int | None = None

    @classmethod
    def build(
        cls,
        *,
        id: str,
        source: str,
        repo: str,
        title: str,
        body: str,
        author: str,
        merged_at: datetime,
        created_at: datetime | None = None,
        files: list[str] | None = None,
        additions: int = 0,
        deletions: int = 0,
        reviews: list[Review] | None = None,
        ci_status: CIStatus | None = None,
        merge_commit_sha: str | None = None,
        pr_number: int | None = None,
        commits: list[Commit] | None = None,
        commit_count: int = 0,
        last_edited_at: datetime | None = None,
        total_comments_count: int = 0,
    ) -> MergedChange:
        """Validated constructor that auto-extracts ticket IDs from title + body."""
        text = f"{title}\n{body}".strip()
        return cls(
            id=id,
            source=source,
            repo=repo,
            title=title,
            body=body,
            author=author,
            merged_at=merged_at,
            created_at=created_at,
            files=files or [],
            additions=additions,
            deletions=deletions,
            ticket_ids=frozenset(extract_ticket_ids(text)),
            reviews=reviews,
            ci_status=ci_status,
            merge_commit_sha=merge_commit_sha,
            pr_number=pr_number,
            commits=commits or [],
            commit_count=commit_count,
            last_edited_at=last_edited_at,
            total_comments_count=total_comments_count,
        )

    def to_dict(self) -> dict:
        """Serialize to a JSON-compatible dict."""
        return {
            "id": self.id,
            "source": self.source,
            "repo": self.repo,
            "title": self.title,
            "body": self.body,
            "author": self.author,
            "merged_at": self.merged_at.isoformat(),
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "files": self.files,
            "additions": self.additions,
            "deletions": self.deletions,
            "ticket_ids": sorted(self.ticket_ids),
            "merge_commit_sha": self.merge_commit_sha,
            "pr_number": self.pr_number,
            "ci_status": self.ci_status.value if self.ci_status else None,
            "reviews": [
                {
                    "reviewer": review.reviewer,
                    "state": review.state,
                    "submitted_at": review.submitted_at.isoformat(),
                    "is_bot": review.is_bot,
                    "body": review.body,
                }
                for review in (self.reviews or [])
            ],
            "commits": [
                {
                    "message": commit.message,
                    "sha": commit.sha,
                    "authored_at": commit.authored_at.isoformat() if commit.authored_at else None,
                }
                for commit in (self.commits or [])
            ],
            "commit_count": self.commit_count,
            "last_edited_at": self.last_edited_at.isoformat() if self.last_edited_at else None,
            "total_comments_count": self.total_comments_count,
        }
