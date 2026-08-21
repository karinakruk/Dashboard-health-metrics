"""Read the BigQuery export produced by ``scripts/bq_refresh.sh``.

The dashboard never queries BigQuery directly: a scheduled job runs the checks
in SQL and exports a small JSON, which this module turns into the same
``Report`` / ``QueueItem`` shapes the snapshot path produces. That keeps serving
free, credential-less and fast no matter how large the universe is.

Because the export carries only the top slice of rows per rule (plus the *true*
counts), ``CheckResult.total_count`` is populated from the summary while
``issues`` holds just the displayed rows.
"""

from __future__ import annotations

import json
from pathlib import Path

from .checks import ALL_CHECKS, CheckResult, Issue, Report
from .fixqueue import QueueItem

EXPORT_FILE = Path(__file__).resolve().parent.parent / "data" / "bq_export.json"

DEALROOM_BASE = "https://app.dealroom.co/companies/"


def export_exists(path: Path | str = EXPORT_FILE) -> bool:
    return Path(path).exists()


def _rows(payload) -> dict:
    """`bq --format=json` returns a list of rows; we export exactly one."""
    if isinstance(payload, list):
        if not payload:
            raise ValueError("BigQuery export is empty")
        return payload[0]
    return payload


def load_export(path: Path | str = EXPORT_FILE) -> tuple[Report, list[QueueItem], dict]:
    """Return (report, fix_queue, by_country) from the exported JSON."""

    data = _rows(json.loads(Path(path).read_text()))

    counts = {
        row["rule_id"]: row
        for row in (data.get("summary") or [])
    }
    queue_rows = data.get("queue") or []

    # Group the displayed rows back under their rule.
    rows_by_rule: dict[str, list[dict]] = {}
    for row in queue_rows:
        rows_by_rule.setdefault(row["rule_id"], []).append(row)

    results: list[CheckResult] = []
    for meta, _fn in ALL_CHECKS:
        summary = counts.get(meta.id, {})
        issues = [
            Issue(
                check_id=meta.id,
                company_id=row.get("company_id") or row.get("company_slug") or "",
                company_name=row.get("company_name") or "",
                detail=row.get("detail") or "",
                round_date=row.get("round_date"),
                round_type=row.get("round_type"),
                amount_usd=_int(row.get("amount_usd")),
            )
            for row in rows_by_rule.get(meta.id, [])
        ]
        results.append(
            CheckResult(
                meta=meta,
                issues=issues,
                total_count=_int(summary.get("issue_count")) or len(issues),
            )
        )

    total_companies = _int(data.get("universe_companies")) or 0
    report = Report(results=results, total_companies=total_companies)

    queue = [
        QueueItem(
            rank=i,
            rule_id=row["rule_id"],
            rule_title=_title(row["rule_id"]),
            severity=row.get("severity") or "warning",
            company_name=row.get("company_name") or "",
            company_url=_url(row.get("company_slug")),
            country=row.get("hq_country"),
            round_date=row.get("round_date"),
            round_type=row.get("round_type"),
            impact_usd=_int(row.get("impact_usd")) or 0,
            detail=row.get("detail") or "",
        )
        for i, row in enumerate(
            sorted(queue_rows, key=lambda r: -(_int(r.get("impact_usd")) or 0)), start=1
        )
    ]

    by_country: dict[str, int] = {}
    for row in data.get("by_country") or []:
        key = row.get("hq_country") or "(no location)"
        by_country[key] = by_country.get(key, 0) + (_int(row.get("issue_count")) or 0)

    return report, queue, by_country


def _int(value) -> int | None:
    """BigQuery JSON returns INT64 as a string."""
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _title(rule_id: str) -> str:
    return next((m.title for m, _ in ALL_CHECKS if m.id == rule_id), rule_id)


def _url(slug: str | None) -> str:
    return f"{DEALROOM_BASE}{slug}" if slug else ""
