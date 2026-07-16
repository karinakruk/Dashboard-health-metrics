"""Render the funding data-health report as a self-contained HTML dashboard.

No template engine and no external assets — everything (CSS + a tiny theme
toggle) is inlined, so the page is a single string the FastAPI app can return.
Colours follow the data-viz reference palette (light + dark, status roles).
"""

from __future__ import annotations

import html

from .checks import CRITICAL, SERIOUS, WARNING, Report
from .models import Snapshot

# Status-palette roles (fixed, never themed).
SEVERITY_COLOR = {
    CRITICAL: "#d03b3b",
    SERIOUS: "#ec835a",
    WARNING: "#fab219",
    "good": "#0ca30c",
}
SEVERITY_LABEL = {CRITICAL: "Critical", SERIOUS: "Serious", WARNING: "Warning"}


def fmt_usd(value: int | None) -> str:
    if value is None:
        return "—"
    if value == 0:
        return "undisclosed"
    if value >= 1_000_000_000:
        return f"${value / 1_000_000_000:.1f}B"
    if value >= 1_000_000:
        return f"${value / 1_000_000:.0f}M"
    if value >= 1_000:
        return f"${value / 1_000:.0f}K"
    return f"${value}"


def fmt_int(value: int | None) -> str:
    return f"{value:,}" if value is not None else "—"


def _score_band(score: int) -> str:
    if score >= 80:
        return "good"
    if score >= 60:
        return WARNING
    if score >= 40:
        return SERIOUS
    return CRITICAL


def esc(text: str) -> str:
    return html.escape(str(text))


# --------------------------------------------------------------------------- #
# Sections
# --------------------------------------------------------------------------- #


def _stat_tiles(snapshot: Snapshot, report: Report) -> str:
    n_rounds = sum(len(c.rounds) for c in snapshot.companies)
    tiles = [
        ("Companies checked", fmt_int(report.total_companies), None),
        ("Funding rounds", fmt_int(n_rounds), None),
        ("Issues found", fmt_int(report.total_issues), CRITICAL if report.total_issues else "good"),
        ("Companies affected", fmt_int(len(report.flagged_company_ids)), None),
        ("Clean companies", fmt_int(report.clean_companies), "good"),
    ]
    cells = "".join(
        f"""
        <div class="tile">
          <div class="tile-value"{f' style="color:{SEVERITY_COLOR[color]}"' if color else ''}>{value}</div>
          <div class="tile-label">{esc(label)}</div>
        </div>"""
        for label, value, color in tiles
    )
    return f'<div class="tiles">{cells}</div>'


def _overview_chart(report: Report) -> str:
    counts = [(r.meta, r.count) for r in report.results]
    max_count = max((c for _, c in counts), default=0) or 1
    rows = ""
    for meta, count in counts:
        color = SEVERITY_COLOR[meta.severity]
        pct = max(count / max_count * 100, 1.5) if count else 0
        bar = (
            f'<div class="bar-fill" style="width:{pct:.1f}%;background:{color}"></div>'
            if count
            else '<div class="bar-empty">no issues</div>'
        )
        rows += f"""
        <a class="bar-row" href="#check-{meta.id}">
          <div class="bar-label">
            <span class="dot" style="background:{color}"></span>{esc(meta.title)}
          </div>
          <div class="bar-track">{bar}</div>
          <div class="bar-count">{count}</div>
        </a>"""
    return f'<div class="chart card">{rows}</div>'


def _company_cell(name: str, url: str | None) -> str:
    name = esc(name)
    if url:
        return (
            f'<td class="co"><a href="{esc(url)}" target="_blank" '
            f'rel="noopener noreferrer">{name}<span class="ext"> ↗</span></a></td>'
        )
    return f'<td class="co">{name}</td>'


def _issue_table(result, url_by_id: dict[str, str]) -> str:
    if not result.issues:
        return '<p class="empty">No issues found for this check. ✓</p>'
    show_round = any(i.round_date for i in result.issues)
    header = "<th>Company</th>"
    if show_round:
        header += "<th>Round</th><th class='num'>Amount</th>"
    header += "<th>Detail</th>"
    body = ""
    for i in result.issues:
        row = _company_cell(i.company_name, url_by_id.get(i.company_id))
        if show_round:
            rt = esc(i.round_type) if i.round_type else "—"
            date = esc(i.round_date) if i.round_date else ""
            amt = fmt_usd(i.amount_usd) if i.amount_usd is not None else "—"
            row += (
                f'<td><span class="rt">{rt}</span> '
                f'<span class="muted">{date}</span></td>'
                f'<td class="num">{amt}</td>'
            )
        row += f"<td>{esc(i.detail)}</td>"
        body += f"<tr>{row}</tr>"
    return f"""
    <div class="table-wrap">
      <table>
        <thead><tr>{header}</tr></thead>
        <tbody>{body}</tbody>
      </table>
    </div>"""


def _check_sections(report: Report, url_by_id: dict[str, str]) -> str:
    sections = ""
    for result in report.results:
        meta = result.meta
        color = SEVERITY_COLOR[meta.severity]
        open_attr = " open" if result.count else ""
        sections += f"""
        <details class="check card" id="check-{meta.id}"{open_attr}>
          <summary>
            <span class="sev-badge" style="background:{color}">{SEVERITY_LABEL[meta.severity]}</span>
            <span class="check-title">{esc(meta.title)}</span>
            <span class="check-count">{result.count}</span>
          </summary>
          <p class="check-q">{esc(meta.question)}</p>
          {_issue_table(result, url_by_id)}
        </details>"""
    return sections


CSS = """
:root {
  --surface:#fcfcfb; --page:#f9f9f7; --ink:#0b0b0b; --ink2:#52514e;
  --muted:#898781; --grid:#e1e0d9; --border:rgba(11,11,11,0.10);
  --track:#eeede9;
}
:root[data-theme="dark"], :root.dark {
  --surface:#1a1a19; --page:#0d0d0d; --ink:#ffffff; --ink2:#c3c2b7;
  --muted:#898781; --grid:#2c2c2a; --border:rgba(255,255,255,0.10);
  --track:#26261f;
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    --surface:#1a1a19; --page:#0d0d0d; --ink:#ffffff; --ink2:#c3c2b7;
    --muted:#898781; --grid:#2c2c2a; --border:rgba(255,255,255,0.10);
    --track:#26261f;
  }
}
* { box-sizing:border-box; }
body {
  margin:0; background:var(--page); color:var(--ink);
  font-family:system-ui,-apple-system,"Segoe UI",sans-serif; line-height:1.5;
  -webkit-font-smoothing:antialiased;
}
.wrap { max-width:960px; margin:0 auto; padding:32px 20px 64px; }
a { color:inherit; }
.card {
  background:var(--surface); border:1px solid var(--border);
  border-radius:12px; padding:20px;
}
header.top { display:flex; justify-content:space-between; align-items:flex-start; gap:16px; margin-bottom:8px; }
h1 { font-size:24px; margin:0 0 4px; letter-spacing:-0.01em; }
.subtitle { color:var(--ink2); font-size:14px; margin:0; }
.source { color:var(--muted); font-size:12px; margin-top:6px; }
.theme-btn {
  border:1px solid var(--border); background:var(--surface); color:var(--ink2);
  border-radius:8px; padding:6px 10px; font-size:13px; cursor:pointer;
}
.hero { display:flex; align-items:center; gap:20px; margin:20px 0; }
.score { font-size:56px; font-weight:700; line-height:1; letter-spacing:-0.02em; }
.score small { font-size:20px; color:var(--muted); font-weight:500; }
.hero-meta { font-size:14px; color:var(--ink2); }
.hero-meta .status-chip {
  display:inline-flex; align-items:center; gap:6px; font-weight:600; color:var(--ink);
}
.hero-meta .status-chip .dot { width:10px; height:10px; }
.tiles { display:grid; grid-template-columns:repeat(auto-fit,minmax(150px,1fr)); gap:12px; margin:16px 0 28px; }
.tile { background:var(--surface); border:1px solid var(--border); border-radius:12px; padding:16px; }
.tile-value { font-size:28px; font-weight:700; font-variant-numeric:tabular-nums; letter-spacing:-0.01em; }
.tile-label { font-size:12px; color:var(--ink2); margin-top:4px; text-transform:uppercase; letter-spacing:0.04em; }
h2 { font-size:15px; text-transform:uppercase; letter-spacing:0.05em; color:var(--ink2); margin:32px 0 12px; }
.chart { padding:12px 20px; }
.bar-row { display:flex; align-items:center; gap:14px; padding:9px 0; text-decoration:none; border-bottom:1px solid var(--border); }
.bar-row:last-child { border-bottom:none; }
.bar-label { flex:0 0 210px; font-size:14px; display:flex; align-items:center; gap:9px; }
.dot { width:9px; height:9px; border-radius:50%; flex:none; display:inline-block; }
.bar-track { flex:1; background:var(--track); border-radius:5px; height:14px; overflow:hidden; position:relative; }
.bar-fill { height:100%; border-radius:5px; min-width:6px; }
.bar-empty { font-size:11px; color:var(--muted); padding-left:8px; line-height:14px; }
.bar-count { flex:0 0 32px; text-align:right; font-variant-numeric:tabular-nums; font-weight:600; font-size:14px; }
details.check { padding:0; margin-bottom:12px; overflow:hidden; }
details.check summary {
  list-style:none; cursor:pointer; padding:16px 20px; display:flex; align-items:center; gap:12px;
}
details.check summary::-webkit-details-marker { display:none; }
.sev-badge { color:#fff; font-size:11px; font-weight:700; padding:3px 8px; border-radius:6px; text-transform:uppercase; letter-spacing:0.03em; }
.check-title { font-weight:600; font-size:15px; flex:1; }
.check-count { font-variant-numeric:tabular-nums; font-weight:700; font-size:16px; color:var(--ink2); }
.check-q { color:var(--ink2); font-size:13px; margin:0 20px 12px; }
.table-wrap { overflow-x:auto; border-top:1px solid var(--border); }
table { width:100%; border-collapse:collapse; font-size:13px; }
th, td { text-align:left; padding:9px 20px; border-bottom:1px solid var(--border); vertical-align:top; }
th { font-size:11px; text-transform:uppercase; letter-spacing:0.04em; color:var(--muted); font-weight:600; }
th.num, td.num { text-align:right; font-variant-numeric:tabular-nums; white-space:nowrap; }
td.co { font-weight:600; white-space:nowrap; }
td.co a { color:inherit; text-decoration:none; border-bottom:1px solid var(--border); padding-bottom:1px; }
td.co a:hover { border-bottom-color:currentColor; }
td.co a .ext { color:var(--muted); font-weight:400; }
.rt { font-weight:600; }
.muted { color:var(--muted); }
.empty { color:var(--muted); font-size:13px; padding:16px 20px; margin:0; }
footer { margin-top:40px; color:var(--muted); font-size:12px; line-height:1.6; }
footer code { background:var(--track); padding:1px 5px; border-radius:4px; }
"""

THEME_JS = """
(function(){
  var root=document.documentElement, btn=document.getElementById('themeBtn');
  function cur(){ return root.getAttribute('data-theme') ||
    (matchMedia('(prefers-color-scheme: dark)').matches?'dark':'light'); }
  function set(t){ root.setAttribute('data-theme',t); btn.textContent = t==='dark'?'☀︎ Light':'☾ Dark'; }
  set(cur());
  btn.addEventListener('click', function(){ set(cur()==='dark'?'light':'dark'); });
})();
"""


def render_page(snapshot: Snapshot, report: Report) -> str:
    score = report.health_score
    band = _score_band(score)
    band_color = SEVERITY_COLOR[band]
    band_word = {"good": "Healthy", WARNING: "Needs attention",
                 SERIOUS: "At risk", CRITICAL: "Critical"}[band]
    url_by_id = {c.id: c.dealroom_url for c in snapshot.companies}

    return f"""<!doctype html>
<html lang="en" data-theme="">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Funding Data Health</title>
<style>{CSS}</style>
</head>
<body>
<div class="wrap">
  <header class="top">
    <div>
      <h1>Funding Data Health</h1>
      <p class="subtitle">Automated quality checks over Dealroom funding data.</p>
      <p class="source">Source: {esc(snapshot.source)} · snapshot {esc(snapshot.pulled_at)}</p>
    </div>
    <button id="themeBtn" class="theme-btn">☾ Dark</button>
  </header>

  <div class="hero">
    <div class="score" style="color:{band_color}">{score}<small>/100</small></div>
    <div class="hero-meta">
      <div class="status-chip"><span class="dot" style="background:{band_color}"></span>{band_word}</div>
      <div>{report.total_issues} issues across {len(report.flagged_company_ids)} of {report.total_companies} companies</div>
    </div>
  </div>

  {_stat_tiles(snapshot, report)}

  <h2>Issues by check</h2>
  {_overview_chart(report)}

  <h2>Findings</h2>
  {_check_sections(report, url_by_id)}

  <footer>
    <p><strong>Methodology.</strong> Data-health checks run over a real Dealroom
    snapshot (<code>analyze_company</code> + <code>entity_fundings</code>, pulled
    {esc(snapshot.pulled_at)}). Non-USD amounts converted to USD at fixed rates.
    A round is treated as <em>verified</em> when it has a disclosed amount plus a
    lead investor or a stated valuation (a completeness proxy for Dealroom's
    internal verified flag). "Big" rounds are ≥ $10M. Where a company's HQ
    location or round type is null, that is a genuine gap in the pulled data.</p>
  </footer>
</div>
<script>{THEME_JS}</script>
</body>
</html>"""
