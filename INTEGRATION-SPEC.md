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
2. **Join key (P0)** — PR number is the join key. Define how to handle local-git-only mode where PR numbers are absent
3. **Rework signal alignment (P1)** — the three tools detect rework independently. Define which tool is authoritative for which classification
4. **CLI integration (P1)** — `--from-upfront` and `--from-catchrate` flags on changeledger
5. **Enriched report (P2)** — HTML report with cost broken down by spec quality tier and pipeline classification

### Alternatives Considered

- **Shared rework detection library** — too much coupling. Each tool has domain-specific detection needs (CatchRate needs CI status, Upfront needs spec linkage). Aligned but independent detection is the right trade-off.
- **Central database** — overkill for CLI tools. JSON file exchange is sufficient.
- **changeledger as orchestrator** — would make changeledger depend on both tools at runtime. File-based integration keeps tools independently installable.

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
| **Spec'd** | A PR with a linked spec (ticket ID, spec URL, or filled template section) | Upfront |
| **Unspec'd** | A PR without a linked spec | Upfront |
| **Observation window** | The number of days after merge to wait before classifying | All (default: 14 days) |
| **Lookback** | How far back to scan for commits/PRs | All (default: 90 days) |
| **Pending** | A change too recent to classify (merged < window days ago) | All |

### Rework vs Escape

These are **not** the same thing:

- **Rework** (changeledger) = any change that required follow-up work. Includes reverts AND bug-fix commits. Does not consider whether gates caught anything.
- **Escape** (CatchRate) = a change that passed ALL gates (CI clean, review approved) and STILL needed follow-up. A subset of rework.

A change can be rework but not an escape (if review requested changes that were applied before merge, but it was still reverted later — that's a human_save that became an escape). The classifications are complementary, not competing.

---

## Data Contract

### Join Key

**PR number** is the primary join key across all three tools.

- All three tools fetch PR metadata from GitHub via `gh pr list`
- changeledger stores `pr_number` on the `Commit` dataclass and in rework results
- CatchRate stores `number` on each classified PR
- Upfront stores `pr_number` on each coverage/quality result

**Local git mode:** When changeledger runs without `--repo` (local git only), `pr_number` is `None`. Upfront and CatchRate data cannot be joined. The integration flags are ignored with a warning.

### Shared Defaults

| Parameter | Default | CLI flag |
|-----------|---------|----------|
| Observation window | 14 days | `--window 14` |
| Lookback | 90 days | `--lookback 90` |
| Window boundary | Inclusive (`<=`) | — |

All three tools MUST use these defaults so their output is comparable. If a user overrides on one tool, they must override on all three.

### Upfront Output Schema (consumed by changeledger)

changeledger reads the JSON produced by `upfront report --repo owner/repo --json upfront.json`:

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

**Required fields for join:** `coverage.prs[].number`, `coverage.prs[].specd`, `quality.specs[].pr_number`, `quality.specs[].overall`

**Optional fields:** Everything else. changeledger degrades gracefully when optional fields are missing.

### CatchRate Output Schema (consumed by changeledger)

changeledger reads the JSON produced by `catchrate check --repo owner/repo --json catchrate.json`:

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

**Required fields for join:** `prs[].number`, `prs[].classification`

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
  }
}
```

`unmatched` = rework items with no corresponding PR in the enrichment data (join failure).

---

## Rework Signal Alignment

### What each tool detects

| Signal | changeledger | CatchRate | Upfront |
|--------|-------------|-----------|---------|
| Explicit revert (SHA in body) | Yes | Yes | Yes (git mode) |
| Revert #N (PR number) | Yes | Yes | Yes |
| Revert by title match | Yes (subject in message) | Yes (title string match) | No |
| Fixes: trailer | Yes | No | No |
| Fix prefix (fix:/bugfix:/hotfix:/patch:) | Yes | Yes (no patch:) | Yes (+ labels + keywords) |
| Trivial fix filtering | No | No | Yes |
| Ticket ID cross-reference | Yes | Yes | No |
| File overlap (>50% of candidate) | Yes | Yes* | Yes (by count, no threshold) |

*CatchRate currently uses % of original — to be aligned to % of candidate per decision.

### Authority rules

When tools disagree on classification, the following precedence applies:

1. **CatchRate is authoritative for pipeline classification** (machine_catch / human_save / escape). It has CI and review data that the other tools lack.
2. **Upfront is authoritative for spec classification** (specd / unspecd). It has spec detection logic the other tools lack.
3. **changeledger is authoritative for cost attribution**. It owns the cost formula and the rework-hours input.
4. **For rework detection**, changeledger's classification stands for its own denominator. CatchRate's escape classification enriches but does not override.

### Aligned patterns (current state)

These patterns are already aligned or will be aligned:

| Pattern | Aligned value |
|---------|--------------|
| Fix prefixes | `fix\|bugfix\|hotfix\|patch` |
| File overlap direction | % of candidate (fix PR) |
| Observation window | 14 days, inclusive boundary (`<=`) |
| Lookback default | 90 days |
| Revert PR# matching | `Revert(s\|ed\|ing)? #N` |

### Still divergent (accepted)

| Difference | Reason |
|-----------|--------|
| Upfront filters trivial fixes | Appropriate for spec effectiveness; changeledger and CatchRate count all rework including trivial |
| Upfront uses no ticket matching | Spec effectiveness is about file-level signal, not ticket linkage |
| CatchRate uses dynamic high-touch exclusion | Appropriate for escape detection at scale; changeledger uses static list |
| changeledger has Fixes: trailer signal | Unique to git conventions; other tools don't need it |

---

## Acceptance Criteria

### Data Contract

1. **Given** `upfront report --json upfront.json` produces valid JSON, **When** `changeledger cost --from-upfront upfront.json --json costs.json`, **Then** changeledger reads the file, joins on `prs[].number`, and includes `by_spec_quality` in output.
2. **Given** `catchrate check --json catchrate.json` produces valid JSON, **When** `changeledger cost --from-catchrate catchrate.json --json costs.json`, **Then** changeledger reads the file, joins on `prs[].number`, and includes `by_pipeline_classification` in output.
3. **Given** both `--from-upfront` and `--from-catchrate` are provided, **When** changeledger runs, **Then** both enrichment sections appear in output.
4. **Given** Upfront JSON with a PR number that doesn't appear in changeledger's rework results, **When** joined, **Then** that PR is silently skipped and counted in `unmatched`.
5. **Given** changeledger runs in local-git mode (no `--repo`), **When** `--from-upfront` is specified, **Then** changeledger warns "Upfront data requires GitHub mode (--repo). Ignoring --from-upfront." and proceeds without enrichment.
6. **Given** Upfront JSON missing the `quality` section, **When** changeledger reads it, **Then** `by_spec_quality` uses coverage data only (specd/unspecd split without quality tiers).
7. **Given** `--from-upfront` points to a non-existent file, **When** changeledger starts, **Then** exit code 1 with error "File not found: {path}".
8. **Given** `--from-upfront` points to invalid JSON, **When** changeledger reads it, **Then** exit code 1 with error "Invalid JSON in {path}".

### Join Logic

9. **Given** rework results with `pr_number` field and Upfront data with `coverage.prs[].number`, **When** joined, **Then** each rework item is enriched with `specd` boolean from the matching Upfront PR.
10. **Given** rework results with `pr_number` field and CatchRate data with `prs[].number`, **When** joined, **Then** each rework item is enriched with `classification` from the matching CatchRate PR.
11. **Given** a rework item with `pr_number: null` (local git), **When** join is attempted, **Then** that item is counted as `unmatched`.

### Enriched Report

12. **Given** cost data enriched with Upfront data, **When** HTML report is generated, **Then** the report includes a "Cost by Spec Quality" section showing specd vs unspecd cost-per-change.
13. **Given** cost data enriched with CatchRate data, **When** HTML report is generated, **Then** the report includes a "Cost by Pipeline Classification" section showing machine_catch / human_save / escape cost-per-change.
14. **Given** enrichment data where all rework items are `unmatched`, **When** report is generated, **Then** the enrichment sections show "No matching data" instead of empty tables.

### Window Alignment

15. **Given** changeledger with `--window 14` and CatchRate JSON produced with `--window 14`, **When** joined, **Then** a change classified as "escape" by CatchRate is always classified as "rework" or "fix" by changeledger (not "accepted"), because the windows are aligned.
16. **Given** changeledger with `--window 14` and CatchRate JSON produced with `--window 30`, **When** joined, **Then** changeledger warns "Window mismatch: changeledger=14d, CatchRate=30d. Classifications may disagree." and proceeds.

---

## Edge Cases

### Mismatched lookback periods

If changeledger scanned 90 days but Upfront only scanned 30 days, some changeledger rework items will have no Upfront match (they're outside Upfront's lookback). These are counted as `unmatched`, not as "unspec'd." The report should note: "X items outside Upfront lookback range."

### PR renumbering / squash merges

GitHub squash-merges produce a single commit with a single PR number. All three tools handle this correctly because they key on PR number, not commit SHA.

### Rebase merges without merge commits

Some repos use rebase-merge, which produces no merge commit. The GitHub API still provides `mergeCommit.oid` for the head commit. If missing, changeledger falls back to `PR#N` synthetic SHA. CatchRate and Upfront key on PR number and are unaffected.

### Tool version skew

If Upfront adds a new field to its JSON output, changeledger must not break. Use `.get()` with defaults for all optional fields. Required fields (PR number, specd boolean, classification) must be validated on load.
