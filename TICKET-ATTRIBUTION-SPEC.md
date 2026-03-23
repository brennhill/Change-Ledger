# Ticket-Level Attribution Spec

## Context

### Problem / Why Now

The three tools (changeledger, Upfront, CatchRate) measure delivery health at the PR level. But management thinks in tickets — JIRA issues, Linear tasks, GitHub issues. The questions they ask are:

- "Which initiative produced the most rework?" (ticket → rework cost)
- "Did the checkout epic have good specs?" (ticket → spec quality)
- "Which team's tickets escape review most often?" (ticket → escape rate)

PRs are the unit of work. Tickets are the unit of planning. The gap between them is the attribution problem.

### The Attribution Model

A PR is the atomic unit of delivery. A ticket is the atomic unit of planning. The relationship is many-to-many:

- A PR can reference multiple tickets ("Fixes PROJ-123, PROJ-456")
- A ticket can span multiple PRs (implementation PR, follow-up PR, fix PR)

**Normalized units** bridge the size gap. A 1500-LOC PR is 3 units (at 500 LOC per unit). Cost is distributed per-unit, not per-PR.

**Attribution rule:** When a PR references multiple tickets, all tickets share responsibility for the full PR. A 3-unit reworked PR with tickets PROJ-123 and PROJ-456 attributes 3 units of rework to both tickets. This intentionally double-counts at the ticket level — both tickets were involved in a change that failed. The PR-level totals remain correct.

### Expected Outcomes

- **Ticket-level cost dashboard**: "PROJ-123 cost $4,200 across 3 PRs (7 normalized units)"
- **Initiative-level rework rates**: "The checkout epic had 28% rework rate vs 8% for the auth epic"
- **Spec quality correlation by ticket**: "Tickets with SHIP-quality specs had 3x lower cost-per-unit"
- **Escape attribution to planning**: "5 of 8 escapes came from tickets with no spec"

### Priorities

1. **Ticket extraction alignment (P0)** — all three tools must extract the same ticket IDs from the same text
2. **Ticket-level aggregation in changeledger (P1)** — `--group-by ticket` output mode
3. **Cross-tool ticket join (P1)** — join changeledger, Upfront, and CatchRate data by ticket ID
4. **Initiative/epic rollup (P2)** — group tickets by parent epic or label

### Alternatives Considered

- **Split PR cost by ticket using file attribution** — requires knowing which files belong to which ticket within a single PR. This data doesn't exist after squash merge. Too fragile.
- **Count each ticket once regardless of PR size** — ignores the reality that a 1500-LOC PR costs more than a 50-LOC PR. Size normalization matters.
- **Attribute proportionally by ticket count** — a PR with 2 tickets gives each 50% of the cost. Punishes well-organized work (many small tickets linked to one PR) and rewards poorly-organized work (one vague ticket for a huge PR).

---

## Ticket Extraction Alignment

### Current State

| Pattern | changeledger | CatchRate | Upfront |
|---------|-------------|-----------|---------|
| `PROJ-123` (JIRA/Linear) | `[A-Z]{2,10}-\d+` | `\b([A-Z][A-Z0-9]+-\d+)\b` | Not used for rework |
| `#123` (GitHub issue) | `#(\d+)` | `(?<!\w)#(\d+)\b` | Not used for rework |
| `fixes #123` (keyword link) | `(?:fixes\|closes\|resolves)\s+#(\d+)` | — (covered by #N pattern) | — |
| Linear-style `PROJ-abc123` | — | `\b([A-Z][A-Z0-9]+-[a-z0-9]+)\b` | — |

### Aligned Extraction

All three tools SHOULD extract ticket IDs using the same patterns and normalization:

```
Pattern 1: [A-Z]{2,10}-\d+          → JIRA/Linear numeric (PROJ-123)
Pattern 2: (?<!\w)#(\d+)\b          → GitHub/GitLab issue (#123)
Pattern 3: [A-Z]{2,10}-[a-z0-9]+    → Linear alphanumeric (PROJ-abc123)
```

**Normalization rules:**
- JIRA/Linear: uppercase the prefix → `PROJ-123`
- GitHub issues: prefix with `#` → `#123`
- Deduplicate: `fixes #123` and `#123` in the same message produce one ID

**Where each tool extracts from:**
- changeledger: commit subject + body (local git), PR title + body (GitHub)
- CatchRate: PR title + body
- Upfront: PR title + body (for spec linkage, not rework)

### Acceptance Criteria

1. **Given** a PR body containing "Fixes PROJ-123 and #456", **When** all three tools extract tickets, **Then** all produce `{"PROJ-123", "#456"}`.
2. **Given** a PR body containing "fixes #123" and a title containing "#123", **When** extracted, **Then** the result is `{"#123"}` (deduplicated).
3. **Given** a PR body containing "Linear issue ENG-abc123", **When** extracted, **Then** `{"ENG-ABC123"}` is included (uppercase normalized).
4. **Given** a commit message with no ticket references, **When** extracted, **Then** the result is an empty set. The PR is "untracked" at the ticket level.

---

## Ticket-Level Aggregation

### changeledger: `--group-by ticket`

New output mode that pivots rework results by ticket ID.

**CLI:**
```bash
changeledger rework --repo owner/repo --group-by ticket --json ticket-rework.json
changeledger full --repo owner/repo --json costs.json --group-by ticket --html report.html
```

**Output schema:**
```json
{
  "by_ticket": {
    "PROJ-123": {
      "ticket_id": "PROJ-123",
      "prs": [42, 45, 48],
      "total_units": 7,
      "accepted_units": 5,
      "rework_units": 2,
      "rework_rate_pct": 28.6,
      "status_summary": {
        "accepted": 2,
        "rework": 1,
        "fix": 0,
        "pending": 0
      }
    },
    "PROJ-456": {
      "ticket_id": "PROJ-456",
      "prs": [42, 50],
      "total_units": 4,
      "accepted_units": 4,
      "rework_units": 0,
      "rework_rate_pct": 0.0,
      "status_summary": {
        "accepted": 2,
        "rework": 0,
        "fix": 0,
        "pending": 0
      }
    },
    "_untracked": {
      "ticket_id": null,
      "prs": [51, 52],
      "total_units": 2,
      "accepted_units": 1,
      "rework_units": 1,
      "rework_rate_pct": 50.0,
      "status_summary": {
        "accepted": 1,
        "rework": 1,
        "fix": 0,
        "pending": 0
      }
    }
  },
  "summary": {
    "total_tickets": 2,
    "untracked_prs": 2,
    "tickets_with_rework": 1,
    "highest_rework_ticket": "PROJ-123"
  }
}
```

**Rules:**
- A PR with tickets `{PROJ-123, PROJ-456}` contributes its full normalized units to BOTH tickets
- A PR with no tickets goes into `_untracked`
- `rework_rate_pct` uses `rework_summary` formula: `(rework + fix) / (accepted + rework + fix) * 100`
- Pending PRs are excluded from rate calculation (same as PR-level)

### Acceptance Criteria

5. **Given** a 1500-LOC PR (3 units) with tickets PROJ-123 and PROJ-456 classified as "rework", **When** `--group-by ticket`, **Then** both PROJ-123 and PROJ-456 show 3 rework units.
6. **Given** two PRs for PROJ-123: one accepted (2 units) and one rework (1 unit), **When** grouped, **Then** PROJ-123 shows 3 total units, 1 rework unit, 33.3% rework rate.
7. **Given** a PR with no ticket IDs, **When** grouped, **Then** it appears under `_untracked`.
8. **Given** `--group-by ticket` without `--repo`, **When** run on local git with merge commits containing PR numbers, **Then** ticket grouping still works (ticket IDs come from commit messages).

---

## Cross-Tool Ticket Join

### How it works

Each tool already extracts ticket IDs. The join happens at the ticket level:

```
changeledger rework result:
  PR #42 → tickets: {PROJ-123, PROJ-456} → status: rework → 3 units

Upfront coverage result:
  PR #42 → specd: true → quality: 84 → tickets: {PROJ-123, PROJ-456}

CatchRate check result:
  PR #42 → classification: escape → review_cycles: 0
```

**Joined at ticket level:**
```
PROJ-123:
  PRs: [#42]
  cost: 3 units of rework
  spec quality: 84 (SHIP)
  pipeline: escaped
  → "This ticket's change escaped with an 84-quality spec"

PROJ-456:
  PRs: [#42, #50]
  cost: 3 units rework + 2 units accepted = 5 total
  spec quality: 84 (PR #42), 72 (PR #50)
  pipeline: escaped (PR #42), machine_catch (PR #50)
  → "This ticket had mixed results — one escape, one clean catch"
```

### Enriched ticket-level output

When `--from-upfront` and `--from-catchrate` are provided with `--group-by ticket`:

```json
{
  "by_ticket": {
    "PROJ-123": {
      "ticket_id": "PROJ-123",
      "prs": [42],
      "total_units": 3,
      "rework_units": 3,
      "rework_rate_pct": 100.0,
      "avg_spec_quality": 84,
      "specd": true,
      "classifications": {
        "machine_catch": 0,
        "human_save": 0,
        "escape": 1
      }
    }
  }
}
```

### Acceptance Criteria

9. **Given** changeledger rework data, Upfront coverage data, and CatchRate classification data all referencing PR #42 with tickets PROJ-123 and PROJ-456, **When** `--group-by ticket --from-upfront upfront.json --from-catchrate catchrate.json`, **Then** both tickets show enriched data with spec quality and classification breakdown.
10. **Given** a ticket that appears in changeledger but not in Upfront output (Upfront had a shorter lookback), **When** joined, **Then** `specd` is `null` and `avg_spec_quality` is `null` for that ticket. Not treated as unspec'd.
11. **Given** a ticket with 3 PRs where 2 were spec'd and 1 was not, **When** joined, **Then** `specd` is `"mixed"` (not true/false).

---

## Upfront: Ticket-Level Effectiveness

Upfront's effectiveness module already compares spec'd vs unspec'd rework rates. With ticket-level attribution:

### Enhanced effectiveness output

```json
{
  "effectiveness": {
    "specd_rework_rate": 0.08,
    "unspecd_rework_rate": 0.21,
    "by_ticket": {
      "specd_tickets": {
        "count": 30,
        "avg_quality_score": 78,
        "rework_rate": 0.08,
        "tickets_with_rework": ["PROJ-123"]
      },
      "unspecd_tickets": {
        "count": 15,
        "avg_quality_score": null,
        "rework_rate": 0.21,
        "tickets_with_rework": ["PROJ-789", "PROJ-790", "PROJ-791"]
      }
    }
  }
}
```

### Acceptance Criteria

12. **Given** Upfront effectiveness data with ticket attribution, **When** consumed by changeledger with `--from-upfront`, **Then** ticket-level report shows "Spec'd tickets: 8% rework, Unspec'd tickets: 21% rework".
13. **Given** a ticket with one spec'd PR (quality 90) and one unspec'd PR, **When** Upfront classifies, **Then** the ticket is "mixed" — counted separately from pure spec'd and pure unspec'd.

---

## CatchRate: Ticket-Level Escape Attribution

CatchRate already produces per-PR classifications. With ticket-level attribution:

### Enhanced output

```json
{
  "by_ticket": {
    "PROJ-123": {
      "prs": [42, 45],
      "classifications": {
        "machine_catch": 1,
        "escape": 1
      },
      "escape_rate": 0.50,
      "escaped_prs": [42]
    }
  }
}
```

### Acceptance Criteria

14. **Given** CatchRate data with ticket attribution, **When** consumed by changeledger with `--from-catchrate --group-by ticket`, **Then** ticket-level report shows escape rate per ticket.
15. **Given** a ticket with 5 PRs where 1 escaped, **When** grouped, **Then** ticket escape rate is 20% (1/5).

---

## Edge Cases

### Tickets with no PRs in the lookback window

A ticket may exist in the tracker but have no merged PRs in the scan period. These tickets are invisible to all three tools. This is correct — if no code shipped for a ticket, there's nothing to measure.

### Tickets spanning multiple repos

PROJ-123 might have PRs in `frontend/app` and `backend/api`. Each tool scans one repo at a time. Cross-repo ticket aggregation requires running each tool per-repo and merging the outputs. This is a P3 concern — document it, don't build it.

### Renamed tickets

If a JIRA ticket is moved from PROJ-123 to NEWPROJ-123, the old ID in commit messages won't match the new ID. This is a data quality issue in the source system, not something the tools can fix.

### PR with 10+ tickets

Some teams link PRs to many tickets. Attribution still works — each ticket gets the full PR cost. But a ticket-level rollup will show inflated totals (the same 3 units counted 10 times). The `summary.total_units` at the PR level remains correct. Document this: "Ticket-level totals double-count when PRs reference multiple tickets. Use PR-level totals for aggregate cost."

### Zero-LOC PRs

A PR that only modifies config files excluded by `is_source_file` has `lines_changed=0` and `normalized_units=1` (floor). It still counts as 1 unit for ticket attribution.

---

## Implementation Order

1. **changeledger: include `pr_number` in rework results** — DONE (e96ff93)
2. **Align ticket extraction patterns** across all three tools — changeledger already has the patterns, CatchRate and Upfront may need updates
3. **changeledger: `--group-by ticket`** — pivot rework results by ticket ID, output `by_ticket` section
4. **changeledger: `--from-upfront` / `--from-catchrate` with ticket join** — enrich ticket-level data with spec quality and classification
5. **Upfront: add `ticket_ids` to effectiveness output** — currently not extracted for rework
6. **CatchRate: add `ticket_ids` to per-PR output** — already extracted internally, just needs to be included in JSON output
7. **HTML report: ticket-level sections** — "Cost by Ticket", "Rework by Ticket", with Upfront/CatchRate enrichment columns
