<p align="center">
  <img src="banner.svg" alt="changeledger banner" width="100%"/>
</p>

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

# From pre-fetched PR data (shared with CatchRate/Upfront)
changeledger rework --from-prs prs.json
changeledger full --from-prs prs.json --json costs.json --html report.html
```

## The Formula

```
cost per accepted change = (model + infra + human engineering + review + rework) / accepted changes
```

**Accepted changes** = merged PRs minus reverts and hotfixes within a 14-day window. This is the denominator your dashboard is missing.

**Human engineering** includes discussion, whiteboarding, spec writing, prompting, and context preparation — not just time at the keyboard. This is often the largest hidden cost.

### LOC Normalization (default: on)

By default, changeledger normalizes the denominator by lines changed. Instead of each PR counting as 1 unit, a PR contributes `max(1, ceil(lines_changed / 500))` units. This controls for size variance: a 1500-LOC PR counts as 3 units, not 1.

The 500-LOC threshold aligns with CATCHRATE and UPFRONT sizing. Review effectiveness drops after ~400 LOC; 500 provides margin.

Disable with `--no-normalize` for raw PR counts.

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

## Where to Get Your Numbers

The hardest part isn't running the tool — it's getting honest inputs. Here's where to find each one.

### `model_cost` — AI model and API spend

This is the easiest number because vendors invoice you for it.

| Source | Where to find it |
|--------|-----------------|
| **OpenAI** | platform.openai.com → Usage → export by date range |
| **Anthropic** | console.anthropic.com → Usage → billing period total |
| **GitHub Copilot** | $19-39/seat/month × seats. GitHub billing page. |
| **Cursor** | Per-seat subscription cost from your billing page |
| **AWS Bedrock** | Cost Explorer → filter by Bedrock service |
| **Google Vertex AI** | Cloud billing → filter by Vertex AI |

For per-session tracking, tools like [Stripe's token-meter](https://github.com/stripe/terminal-apps), LangSmith, Langfuse, or AgentOps can attribute costs to individual tasks or agent runs.

**Tip:** If you use multiple AI tools, sum them. If you're not sure, start with the invoice total for the period. Precision matters less than having the number at all — most teams have never computed this.

### `infra_cost` — CI/CD and infrastructure

The compute cost of running your pipeline — not your production infrastructure, just what it costs to build, test, and deploy changes.

| Source | Where to find it |
|--------|-----------------|
| **GitHub Actions** | Settings → Billing → Actions minutes × per-minute rate |
| **CircleCI** | Plan usage page → compute credits used |
| **GitLab CI** | CI/CD minutes from admin panel |
| **Jenkins/self-hosted** | Rough estimate: (instance cost × % time running CI) |
| **Cloud build** | AWS CodeBuild, GCP Cloud Build → billing console |

If you can't isolate CI cost, estimate 5-15% of your total cloud bill as a starting point.

### `prompting_hours` — Human engineering time

This is the trickiest number because it's invisible. It includes every hour a human spends *before and around* the AI-generated code:

- Whiteboarding and design discussions
- Writing specs and acceptance criteria
- Crafting prompts and context for AI tools
- Debugging AI output that looked right but wasn't
- Context-switching between AI sessions

**How to estimate it:**

- **Quick method:** (number of engineers on the team) × (hours per week on AI-assisted work) × (weeks in period). Be honest — include the time spent re-reading AI output, not just typing prompts.
- **Better method:** Ask 3-5 engineers to track their time for one week. Multiply by team size and period length.
- **Decision: include meetings?** If the meetings are about AI-assisted work (design reviews, spec discussions, prompt strategy), yes. If they're general standups, no.

### `review_hours` — Time spent reviewing AI output

Time humans spend reading, commenting on, and approving AI-generated or AI-assisted changes.

| Source | Where to find it |
|--------|-----------------|
| **GitHub** | Average review time per PR from your analytics tool (LinearB, Swarmia, etc.) × number of reviewed PRs |
| **Manual estimate** | (PRs reviewed per week) × (avg minutes per review / 60) × (weeks in period) |

No tool measures actual *active review minutes* (as opposed to wall-clock time from request to approval). If you use LinearB or Swarmia, their "review time" is elapsed time — actual review effort is typically 30-50% of that.

### `rework_hours` — Time spent fixing mistakes

Time humans spend on reverts, hotfixes, and patches to changes that already merged.

**Best approach:** Let changeledger detect rework from git history (`changeledger rework`), then estimate hours per rework incident:

```bash
# Get the rework count
changeledger rework --repo owner/repo --json rework.json

# Then estimate: (rework incidents) × (avg hours per fix)
# A typical hotfix takes 2-8 hours including investigation, fix, review, and deploy
```

### `burdened_rate` — Fully loaded hourly cost per engineer

This is salary + benefits + taxes + equipment + office space, divided by working hours per year. Your finance team knows this number. If they won't share it:

| Region | Rough range (2025-2026) |
|--------|------------------------|
| US (SF/NYC) | $120-180/hr |
| US (other) | $80-130/hr |
| Western Europe | $80-140/hr |
| Eastern Europe | $40-80/hr |
| India | $20-50/hr |

If you're unsure, $120/hr is a reasonable default for a US-based team.

### `merged_prs` and `reverted_prs` — Change counts

**Best approach:** Let changeledger compute these from git history:

```bash
changeledger full --repo owner/repo --json costs.json
```

This overrides both fields with real data — merged PRs from the lookback period and rework detected within the observation window.

**Manual approach:** GitHub Insights → Pull Requests → Merged. For reverts, search your repo for `revert` or `hotfix` in commit messages within the period.

### Connecting to Jira / Linear / GitHub Issues

The rework detector already extracts ticket IDs from commit messages (patterns like `PROJ-123`, `#456`, `ENG-789`). It uses these for rework signal #3: "same ticket fixed again within 14 days."

For deeper integration:

- **Jira:** Export the sprint/period's resolved issues. Cross-reference with merged PRs to compute spec coverage (% of PRs with linked tickets). The toolkit's `spec-coverage.py` script does this via the GitHub API.
- **Linear:** Linear's API exposes issue-to-PR links. Same cross-reference approach.
- **GitHub Issues:** Already captured via `#123` patterns in commit messages.

Future versions of changeledger may add direct Jira/Linear API integration to pull ticket linkage rates and rework-by-ticket-status. For now, the git-based signals are surprisingly accurate — most rework shows up as reverts, fix commits, or file overlap within the window.

### Starting Point: Don't Wait for Perfect Data

Run `changeledger cost` with rough estimates. The number will be wrong. It will still be more useful than the number you have now, which is zero.

The insight isn't the exact dollar amount — it's the *ratio*. When hidden costs (people) are 3x visible costs (model + infra), that tells you where to invest regardless of whether the total is $200 or $250 per change.

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

## Ecosystem Integration

changeledger is part of the **delivery-gap** ecosystem alongside [Upfront](https://github.com/delivery-gap/upfront) (spec quality) and [CatchRate](https://github.com/delivery-gap/catchrate) (pipeline trustworthiness). All three tools share signal detection via [delivery-gap-signals](https://github.com/delivery-gap/delivery-gap-signals).

The `--from-prs` flag accepts a pre-fetched JSON file of GitHub PR data. This lets all three tools share a single GitHub API fetch instead of each hitting the API independently:

```bash
# Fetch once, use everywhere
delivery-gap-signals fetch --repo owner/repo --out prs.json
changeledger full --from-prs prs.json --json costs.json --html report.html
upfront analyze --from-prs prs.json
catchrate score --from-prs prs.json
```

Coming soon: `--from-upfront` and `--from-catchrate` will enrich the cost report with spec quality and pipeline trust scores.

## License

Apache 2.0
