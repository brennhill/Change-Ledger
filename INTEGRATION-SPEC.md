# Integration Spec: changeledger + Upfront + CatchRate

## Context

### Problem / Why Now

Three tools measure different dimensions of the same delivery pipeline:

- **changeledger** — cost per accepted change (the financial denominator)
- **Upfront** — spec quality and coverage (the input quality)
- **CatchRate** — pipeline trustworthiness (the gate effectiveness)

Each tool works standalone. But the high-value questions require data from all three:
- "Did poorly-spec'd changes cost more?" (changeledger + Upfront)
- "Did escaped changes cost more than caught ones?" (changeledger + CatchRate)
- "Do high-quality specs reduce escape rates?" (Upfront + CatchRate)
- "What is the total cost of spec-quality-driven rework?" (all three)

No tool can answer these alone. This spec defines how they exchange data.

### Expected Outcomes

Teams running all three tools should see:
- **Cost attributed to root cause** — not just "rework was expensive" but "rework on unspec'd changes was 3x more expensive"
- **Pipeline ROI quantified** — "human review caught $X of defects that would have escaped"
- **Spec quality linked to cost** — "high-quality specs correlate with $Y lower cost-per-change"
- **One report** — a unified HTML report that combines all three perspectives

### Priorities

1. **Data contract (P0)** — define the shared vocabulary and JSON schemas so tools can exchange data without breaking each other
2. **Join key (P0)** — ticket ID is the primary join key. PR number is a secondary key for GitHub repos. Define the hierarchy.
3. **Ticket extraction alignment (P0)** — all three tools must extract the same ticket IDs from the same text
4. **Rework signal alignment (P1)** — the three tools detect rework independently. Define which tool is authoritative for which classification
5. **CLI integration (P1)** — `--from-upfront` and `--from-catchrate` flags on changeledger
6. **Enriched report (P2)** — HTML report with cost broken down by spec quality tier and pipeline classification

### Alternatives Considered

- **PR number as primary join key** — only works on GitHub repos with PR-based workflow. Internal git repos, GitLab, Bitbucket, and direct-push workflows have no PR numbers. Ticket IDs are universal.
- **Shared rework detection library** — too much coupling. Each tool has domain-specific detection needs (CatchRate needs CI status, Upfront needs spec linkage). Aligned but independent detection is the right trade-off. Signal detection patterns ARE shared via `delivery-gap-signals`.
- **Central database** — overkill for CLI tools. JSON file exchange is sufficient.
- **changeledger as orchestrator** — would make changeledger depend on both tools at runtime. File-based integration keeps tools independently installable.
- **Each tool calls APIs directly** — replaced with Ports & Adapters architecture. See `delivery-gap-signals/ARCHITECTURE.md`. Tools receive `list[MergedChange]` from source adapters, never call `gh`/`glab`/`git` directly. One fetch per repo, shared across tools via `--from-prs` or the `delivery-gap scan` orchestrator.

---

## Shared Vocabulary

These terms have the same meaning across all three tools:

| Term | Definition | Owner |
|------|-----------|-------|
| **Accepted change** | A merged PR/commit that survived the observation window without revert or bug-fix follow-up | changeledger |
| **Rework** | A merged change that was reverted or required a bug-fix follow-up within the window | changeledger |
| **Escape** | A change that passed all gates (CI + review) but was reverted or fixed post-merge | CatchRate |
| **Machine catch** | A change that passed gates cleanly and survived the window | CatchRate |
| **Human save** | A change where a human reviewer requested changes before merge | CatchRate |
| **Spec'd** | A change with a linked spec (ticket ID, spec URL, or filled template section) | Upfront |
| **Unspec'd** | A change without a linked spec | Upfront |
| **Observation window** | The number of days after merge to wait before classifying | All (default: 14 days) |
| **Lookback** | How far back to scan for commits/PRs | All (default: 90 days) |
| **Pending** | A change too recent to classify (merged < window days ago) | All |
| **Normalized unit** | 1 unit = max(1, ceil(lines_changed / 500)) — size-adjusted change count | changeledger |

### Rework vs Escape

These are **not** the same thing:

- **Rework** (changeledger) = any change that required follow-up work. Includes reverts AND bug-fix commits. Does not consider whether gates caught anything.
- **Escape** (CatchRate) = a change that passed ALL gates (CI clean, review approved) and STILL needed follow-up. A subset of rework.

A change can be rework but not an escape (if review requested changes that were applied before merge, but it was still reverted later — that's a human_save that became an escape). The classifications are complementary, not competing.

---

## Data Contract

### Join Key Hierarchy

**Ticket ID** is the primary join key across all three tools.

Ticket IDs (JIRA-123, #456, ENG-abc123) appear in commit messages, PR titles, and PR bodies across every git workflow — GitHub PRs, GitLab MRs, internal repos with direct pushes. They are the universal identifier that connects a unit of work across tools.

**PR number** is a secondary join key, used as an optimization when available. On GitHub repos, PR numbers are more reliable than ticket IDs (every PR has a number, not every PR has a ticket). When both are available, join on PR number first, ticket ID second.

**Commit SHA** is a tertiary key for deduplication, not for joining. Two tools referencing the same SHA are definitely talking about the same commit, but SHAs don't aggregate.

| Join key | Availability | Reliability | Aggregation |
|----------|-------------|-------------|-------------|
| Ticket ID | Any git workflow with ticket discipline | High (if teams use tickets) | Yes — multiple PRs/commits per ticket |
| PR number | GitHub/GitLab PR workflows only | Very high (always present on PRs) | No — 1:1 with PR |
| Commit SHA | Always | Exact match only | No — 1:1 with commit |

**Join precedence:** When consuming `--from-upfront` or `--from-catchrate` data, changeledger joins in this order:

1. Match on `pr_number` if both sides have it (fast, exact)
2. Fall back to `ticket_ids` intersection if PR number is missing or unmatched
3. Items with no match on either key are counted as `unmatched`

### Ticket Extraction Alignment

All three tools MUST extract ticket IDs using the same patterns and normalization:

```
Pattern 1: [A-Z]{2,10}-\d+           → JIRA/Linear numeric (PROJ-123)
Pattern 2: (?<!\w)#(\d+)\b           → GitHub/GitLab issue (#123)
Pattern 3: [A-Z]{2,10}-[a-z0-9]+     → Linear alphanumeric (ENG-abc123)
```

**Normalization rules:**
- JIRA/Linear: uppercase the prefix → `PROJ-123`
- GitHub issues: prefix with `#` → `#123`
- Deduplicate: `fixes #123` and `#123` in the same message produce one ID

**Where each tool extracts from:**
- changeledger: commit subject + body (local git), PR title + body (GitHub)
- CatchRate: PR title + body
- Upfront: PR title + body (for spec linkage and coverage)

**All three tools MUST include `ticket_ids` in their JSON output** for every PR/commit result.

### Shared Defaults

| Parameter | Default | CLI flag |
|-----------|---------|----------|
| Observation window | 14 days | `--window 14` |
| Lookback | 90 days | `--lookback 90` |
| Window boundary | Inclusive (`<=`) | — |

All three tools MUST use these defaults so their output is comparable. If a user overrides on one tool, they must override on all three.

### Upfront Output Schema (consumed by changeledger)

changeledger reads the JSON produced by `upfront report --json upfront.json`:

```json
{
  "repo": "owner/repo",
  "lookback_days": 90,
  "coverage": {
    "coverage_pct": 75,
    "total_prs": 100,
    "specd_prs": 75,
    "prs": [
      {
        "number": 123,
        "ticket_ids": ["PROJ-123", "#789"],
        "title": "Fix checkout flow",
        "specd": true,
        "spec_source": "#789",
        "merged_at": "2026-01-15T10:30:00Z"
      }
    ]
  },
  "quality": {
    "specs": [
      {
        "pr_number": 123,
        "ticket_ids": ["PROJ-123"],
        "overall": 84,
        "verdict": "SHIP"
      }
    ]
  },
  "effectiveness": {
    "specd_rework_rate": 0.08,
    "unspecd_rework_rate": 0.21,
    "signals": [
      {
        "type": "revert",
        "source": "125",
        "target": "130"
      }
    ]
  }
}
```

**Required fields for join:** `coverage.prs[].ticket_ids` OR `coverage.prs[].number`, `coverage.prs[].specd`

**Optional fields:** Everything else. changeledger degrades gracefully when optional fields are missing.

### CatchRate Output Schema (consumed by changeledger)

changeledger reads the JSON produced by `catchrate check --json catchrate.json`:

```json
{
  "repo": "owner/repo",
  "lookback_days": 90,
  "window_days": 14,
  "machine_catch_rate": 0.80,
  "human_save_rate": 0.12,
  "escape_rate": 0.08,
  "prs": [
    {
      "number": 142,
      "ticket_ids": ["PROJ-456"],
      "title": "Fix checkout null pointer",
      "classification": "machine_catch",
      "review_cycles": 0,
      "time_to_merge_hours": 36.5,
      "lines_changed": 48,
      "size_bucket": "small"
    }
  ]
}
```

**Required fields for join:** `prs[].ticket_ids` OR `prs[].number`, `prs[].classification`

**Optional fields:** Everything else.

### changeledger Enriched Output Schema

When `--from-upfront` and/or `--from-catchrate` are provided, changeledger adds enrichment sections to its output:

```json
{
  "cost_per_accepted_change": 1200,
  "breakdown": { "...": "..." },

  "by_spec_quality": {
    "specd": {
      "count": 45,
      "cost_per_change": 800,
      "rework_rate_pct": 8.0
    },
    "unspecd": {
      "count": 25,
      "cost_per_change": 2100,
      "rework_rate_pct": 28.0
    },
    "unmatched": 5
  },

  "by_pipeline_classification": {
    "machine_catch": {
      "count": 56,
      "cost_per_change": 400
    },
    "human_save": {
      "count": 11,
      "cost_per_change": 1400
    },
    "escape": {
      "count": 8,
      "cost_per_change": 2800
    },
    "unmatched": 0
  },

  "by_ticket": {
    "PROJ-123": {
      "ticket_id": "PROJ-123",
      "total_units": 7,
      "rework_units": 2,
      "rework_rate_pct": 28.6,
      "specd": true,
      "avg_spec_quality": 84,
      "classifications": {"machine_catch": 1, "escape": 1}
    }
  }
}
```

`unmatched` = rework items with no corresponding entry in the enrichment data (join failure on both ticket ID and PR number).

---

## Rework Signal Alignment

### What each tool detects

| Signal | changeledger | CatchRate | Upfront |
|--------|-------------|-----------|---------|
| Explicit revert (SHA in body) | Yes | Yes | Yes (git mode) |
| Revert #N (PR number) | Yes | Yes | Yes |
| Revert by title match | Yes (subject in message) | Yes (title string match) | No |
| Fixes: trailer | Yes | No | No |
| Fix prefix (fix:/bugfix:/hotfix:/patch:) | Yes | Yes | Yes (+ labels + keywords) |
| Trivial fix filtering | No | No | Yes |
| Ticket ID cross-reference | Yes | Yes | No |
| File overlap (>50% of candidate) | Yes | Yes | Yes (by count, no threshold) |

### Authority rules

When tools disagree on classification, the following precedence applies:

1. **CatchRate is authoritative for pipeline classification** (machine_catch / human_save / escape). It has CI and review data that the other tools lack.
2. **Upfront is authoritative for spec classification** (specd / unspecd). It has spec detection logic the other tools lack.
3. **changeledger is authoritative for cost attribution**. It owns the cost formula and the rework-hours input.
4. **For rework detection**, changeledger's classification stands for its own denominator. CatchRate's escape classification enriches but does not override.

### Aligned patterns (current state)

| Pattern | Aligned value |
|---------|--------------|
| Fix prefixes | `fix\|bugfix\|hotfix\|patch` |
| File overlap direction | % of candidate (fix PR) |
| Observation window | 14 days, inclusive boundary (`<=`) |
| Lookback default | 90 days |
| Revert PR# matching | `Revert(s\|ed\|ing)? #N` |
| Ticket extraction | Same 3 patterns, same normalization |

### Still divergent (accepted)

| Difference | Reason |
|-----------|--------|
| Upfront filters trivial fixes | Appropriate for spec effectiveness; changeledger and CatchRate count all rework including trivial |
| Upfront uses no ticket matching for rework | Spec effectiveness is about file-level signal, not ticket linkage |
| CatchRate uses dynamic high-touch exclusion | Appropriate for escape detection at scale; changeledger uses static list |
| changeledger has Fixes: trailer signal | Unique to git conventions; other tools don't need it |

---

## Acceptance Criteria

### Data Contract

1. **Given** `upfront report --json upfront.json` produces valid JSON, **When** `changeledger cost --from-upfront upfront.json --json costs.json`, **Then** changeledger reads the file, joins on ticket IDs (falling back to PR number), and includes `by_spec_quality` in output.
2. **Given** `catchrate check --json catchrate.json` produces valid JSON, **When** `changeledger cost --from-catchrate catchrate.json --json costs.json`, **Then** changeledger reads the file, joins on ticket IDs (falling back to PR number), and includes `by_pipeline_classification` in output.
3. **Given** both `--from-upfront` and `--from-catchrate` are provided, **When** changeledger runs, **Then** both enrichment sections appear in output.
4. **Given** Upfront JSON with a ticket ID that doesn't appear in changeledger's rework results, **When** joined, **Then** that entry is silently skipped and counted in `unmatched`.
5. **Given** changeledger runs on an internal git repo with no PRs but commits contain JIRA ticket IDs, **When** `--from-upfront` is specified and Upfront data also contains ticket IDs, **Then** the join works via ticket ID. No PR numbers needed.
6. **Given** Upfront JSON missing the `quality` section, **When** changeledger reads it, **Then** `by_spec_quality` uses coverage data only (specd/unspecd split without quality tiers).
7. **Given** `--from-upfront` points to a non-existent file, **When** changeledger starts, **Then** exit code 1 with error "File not found: {path}".
8. **Given** `--from-upfront` points to invalid JSON, **When** changeledger reads it, **Then** exit code 1 with error "Invalid JSON in {path}".

### Join Logic

9. **Given** rework results with `pr_number: 42` and Upfront data with `prs[].number: 42`, **When** joined, **Then** match on PR number (fast path).
10. **Given** rework results with `pr_number: null` and `ticket_ids: ["PROJ-123"]`, and Upfront data with `prs[].ticket_ids: ["PROJ-123"]`, **When** joined, **Then** match on ticket ID intersection (fallback path).
11. **Given** a rework item with `pr_number: null` and `ticket_ids: []`, **When** join is attempted, **Then** that item is counted as `unmatched`.
12. **Given** a PR in Upfront data that matches two changeledger items by ticket ID (the original and its fix both reference PROJ-123), **When** joined, **Then** both items are enriched with the same Upfront data. The ticket-level view aggregates them.

### Enriched Report

13. **Given** cost data enriched with Upfront data, **When** HTML report is generated, **Then** the report includes a "Cost by Spec Quality" section showing specd vs unspecd cost-per-change.
14. **Given** cost data enriched with CatchRate data, **When** HTML report is generated, **Then** the report includes a "Cost by Pipeline Classification" section showing machine_catch / human_save / escape cost-per-change.
15. **Given** enrichment data where all rework items are `unmatched`, **When** report is generated, **Then** the enrichment sections show "No matching data" instead of empty tables.

### Window Alignment

16. **Given** changeledger with `--window 14` and CatchRate JSON produced with `--window 14`, **When** joined, **Then** a change classified as "escape" by CatchRate is always classified as "rework" or "fix" by changeledger (not "accepted"), because the windows are aligned.
17. **Given** changeledger with `--window 14` and CatchRate JSON produced with `--window 30`, **When** joined, **Then** changeledger warns "Window mismatch: changeledger=14d, CatchRate=30d. Classifications may disagree." and proceeds.

---

## Edge Cases

### Internal git repo with no PRs

All three tools work on commit messages containing ticket IDs. changeledger extracts ticket IDs from `git log` subjects and bodies. Upfront and CatchRate require PR data for most features but can export ticket-keyed data if they support a git-only mode. The join on ticket ID works without PR numbers.

### Mismatched lookback periods

If changeledger scanned 90 days but Upfront only scanned 30 days, some changeledger rework items will have no Upfront match (they're outside Upfront's lookback). These are counted as `unmatched`, not as "unspec'd." The report should note: "X items outside Upfront lookback range."

### PR renumbering / squash merges

GitHub squash-merges produce a single commit with a single PR number. All three tools handle this correctly because they key on ticket ID first, PR number second.

### Rebase merges without merge commits

Some repos use rebase-merge, which produces no merge commit. The GitHub API still provides `mergeCommit.oid` for the head commit. changeledger extracts PR numbers from commit subjects as fallback. Ticket IDs in commit messages remain the primary join key regardless of merge strategy.

### Tool version skew

If Upfront adds a new field to its JSON output, changeledger must not break. Use `.get()` with defaults for all optional fields. Required fields (ticket IDs, specd boolean, classification) must be validated on load.

### Multiple tickets per PR

A PR referencing PROJ-123 and PROJ-456 contributes its full cost to both tickets. See TICKET-ATTRIBUTION-SPEC.md for the attribution model and double-counting rules.
