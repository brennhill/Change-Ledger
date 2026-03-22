"""
HTML report generation with SVG pie charts.

All user-derived strings are HTML-escaped before interpolation.
"""

import math
import re
from datetime import datetime
from html import escape


def polar_to_cart(cx, cy, r, angle_deg):
    angle_rad = math.radians(angle_deg - 90)
    return cx + r * math.cos(angle_rad), cy + r * math.sin(angle_rad)


def pie_slice(cx, cy, r, start_deg, end_deg, color, opacity=1.0):
    if end_deg - start_deg >= 360:
        end_deg = start_deg + 359.99
    x1, y1 = polar_to_cart(cx, cy, r, start_deg)
    x2, y2 = polar_to_cart(cx, cy, r, end_deg)
    large = 1 if (end_deg - start_deg) > 180 else 0
    op = f' fill-opacity="{opacity}"' if opacity < 1 else ""
    return f'<path d="M{cx},{cy} L{x1:.1f},{y1:.1f} A{r},{r} 0 {large},1 {x2:.1f},{y2:.1f} Z" fill="{color}"{op}/>'


def label_pos(cx, cy, r, start_deg, end_deg):
    mid = (start_deg + end_deg) / 2
    lr = r * 0.65
    return polar_to_cart(cx, cy, lr, mid)


def generate_svg(r: dict) -> str:
    cx, cy, radius = 150, 150, 130
    font = "'Avenir Next', 'Helvetica Neue', Arial, sans-serif"

    slices = [
        (r["model_cost"], r["breakdown"]["model_pct"], "#2BA99A", 1.0),
        (r["infra_cost"], r["breakdown"]["infra_pct"], "#2BA99A", 0.5),
        (r["prompting_cost"], r["breakdown"]["prompting_pct"], "#B06835", 1.0),
        (r["review_cost"], r["breakdown"]["review_pct"], "#C9962A", 1.0),
        (r["rework_cost"], r["breakdown"]["rework_pct"], "#1D3557", 1.0),
    ]

    paths = []
    labels = []
    angle = 0

    for _cost, pct, color, opacity in slices:
        sweep = pct / 100 * 360
        if sweep < 1:
            angle += sweep
            continue
        paths.append(pie_slice(cx, cy, radius, angle, angle + sweep, color, opacity))

        lx, ly = label_pos(cx, cy, radius, angle, angle + sweep)
        if pct >= 8:
            labels.append(
                f'<text x="{lx:.0f}" y="{ly:.0f}" text-anchor="middle" dominant-baseline="middle" '
                f'font-family="{font}" font-size="13" fill="white" font-weight="600">{pct:.0f}%</text>'
            )
        angle += sweep

    return f'''<svg viewBox="0 0 300 300" xmlns="http://www.w3.org/2000/svg" width="300" height="300">
  <rect width="480" height="400" fill="#fcfaf6" rx="8"/>
  {"".join(paths)}
  {"".join(labels)}
</svg>'''


def _safe_repo_url(repo_url: str) -> str:
    """Validate repo_url starts with https:// to prevent javascript: injection."""
    if repo_url and repo_url.startswith("https://"):
        return escape(repo_url, quote=True)
    return ""


def format_signal_html(signal: str, repo_url: str = "") -> str:
    safe_url = _safe_repo_url(repo_url)
    m = re.match(r"(Reverted by|Fixes: trailer in|Same ticket .+ fixed by|Fix) (\w{10})(.*)", signal)
    if m:
        prefix = escape(m.group(1))
        fix_sha = escape(m.group(2))
        rest = escape(m.group(3))
        if safe_url:
            sha_html = f'<a href="{safe_url}/commit/{fix_sha}" class="fix-sha">{fix_sha}</a>'
        else:
            sha_html = f'<span class="fix-sha">{fix_sha}</span>'
        files_match = re.search(r"touches same source files: (.+)$", m.group(3))
        if files_match:
            files = escape(files_match.group(1))
            return f'{prefix} {sha_html}<br><span class="file-list">{files}</span>'
        return f'{prefix} {sha_html}{rest}'
    return escape(signal)


def render_warnings(warnings: list, repo_url: str = "") -> str:
    if not warnings:
        return ""
    safe_url = _safe_repo_url(repo_url)
    cards = []
    for w in warnings:
        items_html = ""
        if w.get("structured_items"):
            item_blocks = []
            for item in w["structured_items"]:
                sha = escape(item["sha"])
                subject = escape(item["subject"][:60])
                if safe_url:
                    sha_link = f'<a href="{safe_url}/commit/{sha}" class="commit-sha">{sha}</a>'
                else:
                    sha_link = f'<span class="commit-sha">{sha}</span>'
                signals_html = "".join(
                    f'<div class="signal">{format_signal_html(s, repo_url)}</div>'
                    for s in item.get("signals", [])
                )
                item_blocks.append(
                    f'<div class="warning-item">'
                    f'<div class="commit-header">{sha_link} <span class="commit-subject">— {subject}</span></div>'
                    f'{signals_html}'
                    f'</div>'
                )
            items_html = f'<div class="warning-items">{"".join(item_blocks)}</div>'
        elif w.get("items"):
            item_blocks = [
                f'<div class="warning-item"><div class="commit-header">{escape(str(t))}</div></div>'
                for t in w["items"]
            ]
            items_html = f'<div class="warning-items">{"".join(item_blocks)}</div>'

        cards.append(
            f'<div class="warning-card {escape(w["level"])}">'
            f'<div class="warning-title">{escape(w["title"])}</div>'
            f'<div class="warning-detail">{escape(w["detail"])}</div>'
            f'{items_html}'
            f'</div>'
        )
    return f'<div class="warnings">{"".join(cards)}</div>'


def generate_html(r: dict, team_name: str = "", warnings: list | None = None, repo_url: str = "") -> str:
    svg = generate_svg(r)
    warnings = warnings or []
    date = datetime.now().strftime("%B %d, %Y")
    safe_team = escape(team_name) if team_name else ""
    team_line = f" — {safe_team}" if safe_team else ""
    c = escape(r.get("currency", "$"))

    return f'''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>Cost per Accepted Change{team_line}</title>
<style>
  :root {{ --cream: #FCFAF6; --navy: #1D3557; --copper: #B06835; --gold: #C9962A; --copy: #2F353C; --muted: #6D7178; }}
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{ font-family: 'Avenir Next', 'Helvetica Neue', Arial, sans-serif; background: var(--cream); color: var(--copy); padding: 40px 20px; max-width: 860px; margin: 0 auto; }}
  .header {{ border-bottom: 3px solid var(--gold); padding-bottom: 20px; margin-bottom: 32px; }}
  .header h1 {{ font-family: Georgia, serif; font-size: 28px; color: var(--navy); font-weight: bold; }}
  .header .subtitle {{ font-size: 14px; color: var(--copper); letter-spacing: 0.08em; text-transform: uppercase; font-weight: 600; margin-top: 4px; }}
  .header .date {{ font-size: 13px; color: var(--muted); margin-top: 8px; }}
  .metrics {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 16px; margin-bottom: 32px; }}
  .metric-card {{ background: white; border: 1px solid #e8e2d8; border-radius: 8px; padding: 20px; text-align: center; }}
  .metric-card .value {{ font-family: Georgia, serif; font-size: 28px; font-weight: bold; color: var(--navy); }}
  .metric-card .label {{ font-size: 12px; color: var(--muted); text-transform: uppercase; letter-spacing: 0.06em; margin-top: 6px; }}
  .metric-card.highlight .value {{ color: var(--gold); font-size: 32px; }}
  .chart-row {{ display: flex; gap: 32px; align-items: flex-start; margin-bottom: 32px; }}
  .chart-row .pie {{ flex: 0 0 auto; }}
  .chart-row .pie svg {{ display: block; }}
  .chart-row .breakdown {{ flex: 1; min-width: 0; }}
  .breakdown-table {{ width: 100%; border-collapse: collapse; }}
  .breakdown-table th {{ text-align: left; font-size: 11px; text-transform: uppercase; letter-spacing: 0.08em; color: var(--copper); padding: 8px 12px; border-bottom: 2px solid var(--navy); }}
  .breakdown-table td {{ padding: 10px 12px; border-bottom: 1px solid #e8e2d8; font-size: 14px; }}
  .breakdown-table td:nth-child(2), .breakdown-table td:nth-child(3) {{ text-align: right; font-family: 'SF Mono', 'Menlo', monospace; }}
  .breakdown-table tr:last-child td {{ border-bottom: 2px solid var(--navy); font-weight: 600; }}
  .color-dot {{ display: inline-block; width: 10px; height: 10px; border-radius: 2px; margin-right: 8px; }}
  .footer {{ border-top: 1px solid #e8e2d8; padding-top: 16px; font-size: 11px; color: var(--muted); text-align: center; }}
  .footer a {{ color: var(--copper); text-decoration: none; }}
  .warnings {{ margin-bottom: 32px; }}
  .warning-card {{ border-left: 4px solid var(--gold); background: white; border-radius: 0 8px 8px 0; padding: 16px 20px; margin-bottom: 12px; }}
  .warning-card.high {{ border-left-color: #C0392B; }}
  .warning-card .warning-title {{ font-weight: 600; color: var(--navy); font-size: 14px; margin-bottom: 4px; }}
  .warning-card .warning-detail {{ font-size: 13px; color: var(--muted); }}
  .warning-card .warning-items {{ margin-top: 12px; }}
  .warning-item {{ background: var(--cream); border: 1px solid #e8e2d8; border-radius: 6px; padding: 12px 16px; margin-bottom: 8px; }}
  .warning-item .commit-header {{ font-size: 13px; color: var(--navy); margin-bottom: 6px; }}
  .warning-item .commit-sha {{ font-family: 'SF Mono', 'Menlo', monospace; font-weight: 700; font-size: 12px; }}
  .warning-item .commit-subject {{ font-weight: 500; }}
  .warning-item .signal {{ font-size: 12px; color: var(--muted); padding-left: 16px; line-height: 1.7; }}
  .warning-item .signal .fix-sha {{ font-family: 'SF Mono', 'Menlo', monospace; font-weight: 600; color: var(--copper); }}
  .warning-item .signal .file-list {{ color: var(--copy); font-family: 'SF Mono', 'Menlo', monospace; font-size: 11px; }}
  @media print {{ body {{ padding: 20px; }} .metric-card {{ break-inside: avoid; }} }}
</style>
</head>
<body>

<div class="header">
  <h1>Cost per Accepted Change</h1>
  <div class="subtitle">changeledger</div>
  <div class="date">{date}{team_line}</div>
</div>

<div class="metrics">
  <div class="metric-card highlight">
    <div class="value">{c}{r["cost_per_accepted_change"]:,.2f}</div>
    <div class="label">Cost per accepted change</div>
  </div>
  <div class="metric-card">
    <div class="value">{r["accepted_changes"]}</div>
    <div class="label">Accepted changes</div>
  </div>
  <div class="metric-card">
    <div class="value">{r["merged_prs"]}</div>
    <div class="label">Merged PRs</div>
  </div>
  <div class="metric-card">
    <div class="value">{r["reverted_prs"]}</div>
    <div class="label">Reverted / fixed</div>
  </div>
</div>

<div class="chart-row">
  <div class="pie">{svg}</div>
  <div class="breakdown">
    <table class="breakdown-table">
      <thead><tr><th>Category</th><th>Cost</th><th>Share</th></tr></thead>
      <tbody>
        <tr><td><span class="color-dot" style="background: #2BA99A"></span>Model / API</td><td>{c}{r["model_cost"]:,.0f}</td><td>{r["breakdown"]["model_pct"]}%</td></tr>
        <tr><td><span class="color-dot" style="background: #2BA99A; opacity: 0.5"></span>Infrastructure</td><td>{c}{r["infra_cost"]:,.0f}</td><td>{r["breakdown"]["infra_pct"]}%</td></tr>
        <tr><td><span class="color-dot" style="background: #B06835"></span>Human engineering</td><td>{c}{r["prompting_cost"]:,.0f}</td><td>{r["breakdown"]["prompting_pct"]}%</td></tr>
        <tr><td><span class="color-dot" style="background: #C9962A"></span>Human review</td><td>{c}{r["review_cost"]:,.0f}</td><td>{r["breakdown"]["review_pct"]}%</td></tr>
        <tr><td><span class="color-dot" style="background: #1D3557"></span>Rework</td><td>{c}{r["rework_cost"]:,.0f}</td><td>{r["breakdown"]["rework_pct"]}%</td></tr>
        <tr><td>Total</td><td>{c}{r["total_cost"]:,.0f}</td><td>100%</td></tr>
      </tbody>
    </table>
  </div>
</div>

{render_warnings(warnings, repo_url)}

<div class="footer">
  Generated by <a href="https://github.com/brennhill/change-ledger">changeledger</a>
</div>

</body>
</html>'''
