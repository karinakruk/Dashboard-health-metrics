"""The fix queue: one ranked, exportable worklist across all checks.

At global scope a single rule can flag tens of thousands of records, so the
dashboard's job is not to render them all — it is to say *what to fix first*.
Issues are therefore ranked by the money at stake (`impact_usd`) and capped;
the full set stays in BigQuery and is reachable via CSV export.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass

from .checks import Report
from .models import Company

DEFAULT_LIMIT = 500

# Ordering when impact ties: fix the definitely-broken before the suspicious.
_SEVERITY_RANK = {"critical": 0, "serious": 1, "warning": 2}


@dataclass(frozen=True)
class QueueItem:
    rank: int
    rule_id: str
    rule_title: str
    severity: str
    company_name: str
    company_url: str
    country: str | None
    round_date: str | None
    round_type: str | None
    impact_usd: int
    detail: str


def build_queue(
    report: Report, companies: list[Company], limit: int = DEFAULT_LIMIT
) -> list[QueueItem]:
    """Flatten every check's issues into one impact-ranked worklist."""

    by_id = {c.id: c for c in companies}
    rows: list[tuple[int, int, str, object]] = []

    for result in report.results:
        for issue in result.issues:
            company = by_id.get(issue.company_id)
            # Round-level issues carry their own amount; company-level ones fall
            # back to total funding, which is the value at stake either way.
            impact = issue.amount_usd or (company.total_funding_usd if company else 0)
            rows.append((impact or 0, _SEVERITY_RANK[result.meta.severity],
                         result.meta.title, issue))

    rows.sort(key=lambda t: (-t[0], t[1], t[3].company_name))

    items: list[QueueItem] = []
    for rank, (impact, sev_rank, title, issue) in enumerate(rows[:limit], start=1):
        company = by_id.get(issue.company_id)
        severity = next(
            s for s, r in _SEVERITY_RANK.items() if r == sev_rank
        )
        items.append(
            QueueItem(
                rank=rank,
                rule_id=issue.check_id,
                rule_title=title,
                severity=severity,
                company_name=issue.company_name,
                company_url=company.dealroom_url if company else "",
                country=company.country if company else None,
                round_date=issue.round_date,
                round_type=issue.round_type,
                impact_usd=impact,
                detail=issue.detail,
            )
        )
    return items


def to_csv(items: list[QueueItem]) -> str:
    """Render the queue as CSV so it can be worked through outside the browser."""

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([
        "rank", "rule", "severity", "company", "dealroom_url", "country",
        "round_date", "round_type", "impact_usd", "detail",
    ])
    for i in items:
        writer.writerow([
            i.rank, i.rule_title, i.severity, i.company_name, i.company_url,
            i.country or "", i.round_date or "", i.round_type or "",
            i.impact_usd, i.detail,
        ])
    return buf.getvalue()
