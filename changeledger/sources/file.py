"""File source adapter — reads cached merged-change data from JSON."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from ..signals import extract_ticket_ids
from ..source_models import CIStatus, MergedChange, Review


def fetch_changes(path: str, **kwargs) -> list[MergedChange]:
    """Read cached merged-change data from a JSON file."""
    del kwargs
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError(f"Expected JSON array in {path}, got {type(data).__name__}")

    changes: list[MergedChange] = []
    for item in data:
        reviews = None
        if item.get("reviews") is not None:
            reviews = [
                Review(
                    reviewer=review.get("reviewer", ""),
                    state=review.get("state", "commented"),
                    submitted_at=datetime.fromisoformat(review["submitted_at"]),
                    is_bot=review.get("is_bot", False),
                    body=review.get("body", ""),
                )
                for review in item["reviews"]
                if review.get("submitted_at")
            ]

        ci_status = None
        if item.get("ci_status"):
            ci_status = CIStatus(item["ci_status"])

        merged_at = item.get("merged_at") or item.get("mergedAt", "")
        created_at = item.get("created_at") or item.get("createdAt")

        changes.append(MergedChange(
            id=str(item.get("id", item.get("number", ""))),
            source=item.get("source", "file"),
            repo=item.get("repo", ""),
            title=item.get("title", ""),
            body=item.get("body", "") or "",
            author=item.get("author", ""),
            merged_at=datetime.fromisoformat(merged_at.replace("Z", "+00:00"))
            if isinstance(merged_at, str) else merged_at,
            created_at=datetime.fromisoformat(created_at.replace("Z", "+00:00"))
            if isinstance(created_at, str) and created_at else None,
            files=item.get("files", []),
            additions=item.get("additions", 0) or 0,
            deletions=item.get("deletions", 0) or 0,
            ticket_ids=frozenset(item["ticket_ids"])
            if "ticket_ids" in item
            else frozenset(extract_ticket_ids(f"{item.get('title', '')}\n{item.get('body', '')}")),
            reviews=reviews,
            ci_status=ci_status,
            merge_commit_sha=item.get("merge_commit_sha"),
            pr_number=item.get("pr_number"),
        ))

    return changes
