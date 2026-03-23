"""Signal detection — re-exported from delivery-gap-signals shared package."""
from delivery_gap_signals import (
    compute_file_overlap,
    extract_fixes_sha,
    extract_pr_number_from_subject,
    extract_revert_pr_numbers,
    extract_ticket_ids,
    is_fix_message,
    is_revert_message,
    is_source_file,
)

__all__ = [
    "compute_file_overlap",
    "extract_fixes_sha",
    "extract_pr_number_from_subject",
    "extract_revert_pr_numbers",
    "extract_ticket_ids",
    "is_fix_message",
    "is_revert_message",
    "is_source_file",
]
