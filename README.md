# changeledger

**Cost per accepted change — the delivery metric no commercial tool computes.**

```
pip install changeledger
```

---

AI made code generation cheap. But what does each *trusted, shipped* change actually cost? Not the model bill — the full cost: model spend, infrastructure, human engineering time, review hours, and rework.

No commercial tool computes this today. changeledger does.

## How to Use

```bash
# Interactive — answer prompts, get your number
changeledger cost

# From a JSON file
changeledger cost --json costs.json

# Detect rework from git history (local repo)
changeledger rework

# Detect rework from GitHub
changeledger rework --repo owner/repo

# Full pipeline: rework detection + cost calculation + HTML report
changeledger full --json costs.json --html report.html

# Full pipeline from GitHub
changeledger full --repo owner/repo --json costs.json --html report.html
```

## The Formula

```
cost per accepted change = (model + infra + human engineering + review + rework) / accepted changes
```

**Accepted changes** = merged PRs minus reverts and hotfixes within a 14-day window. This is the denominator your dashboard is missing.

**Human engineering** includes discussion, whiteboarding, spec writing, prompting, and context preparation — not just time at the keyboard. This is often the largest hidden cost.

## Input Format

```json
{
    "model_cost": 4200,
    "infra_cost": 1800,
    "prompting_hours": 30,
    "review_hours": 40,
    "rework_hours": 20,
    "burdened_rate": 120,
    "merged_prs": 88,
    "reverted_prs": 12
}
```

When using `changeledger full` or `--from-rework`, the `merged_prs` and `reverted_prs` fields are overridden with real data from git history.

## Output

```
==================================================
 COST PER ACCEPTED CHANGE BREAKDOWN
==================================================

  Model/API cost:      $     4,200  (15.7%)
  Infrastructure:      $     1,800  (6.7%)
  Human engineering:   $     3,600  (13.5%)
  Human review:        $     4,800  (18.0%)
  Rework:              $     2,400  (9.0%)
  ────────────────────────────────────────
  Total cost:          $    16,800

  Merged PRs:                  88
  Reverted/fixed:              12
  Accepted changes:            76

  ┌─────────────────────────────────────────┐
  │  Cost per accepted change:    $221.05   │
  └─────────────────────────────────────────┘

  Visible cost (model + infra): 22%
  Hidden cost (people):         78%
```

## HTML Report

The `--html` flag generates a branded report with:
- SVG pie chart showing cost breakdown (visible vs hidden costs)
- Metric cards (cost per change, accepted, merged, reverted)
- Breakdown table with color-coded categories
- Warning cards for high rework rate, oversized PRs, and reworked changes
- Commit SHAs linked to GitHub

## Rework Detection

The rework detector classifies each merge as **accepted**, **rework**, or **pending** using four signals:

1. **Explicit revert** — `git revert` of the original commit
2. **Fixes trailer** — `Fixes: <sha>` pointing to the original
3. **Same ticket** — shared JIRA/Linear/GitHub ticket ID in a fix commit
4. **File overlap** — >50% of a fix commit's source files overlap with the original (excludes docs, configs, lock files)

Changes less than 14 days old are **pending** — not enough time to observe rework.

## CI Integration

```yaml
# GitHub Actions
- name: Calculate delivery cost
  run: |
    pip install changeledger
    changeledger full --repo ${{ github.repository }} \
      --json costs.json \
      --html cost-report.html

- name: Upload report
  uses: actions/upload-artifact@v4
  with:
    name: delivery-cost-report
    path: cost-report.html
```

## Why This Metric Matters

Most teams track the wrong denominator. PR volume, lines of code, and token spend measure *activity*. Cost per accepted change measures *delivery* — the full unit economics of getting a trusted change into production.

When this number is rising, your verification layer is the bottleneck. When the hidden cost (people) dominates the visible cost (model + infra), your AI investment is creating review burden faster than it's creating value.

This is the metric the Verification Triangle framework uses to close the loop between spec quality, eval quality, and delivery economics.

## License

Apache 2.0
