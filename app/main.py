"""FastAPI app serving the funding data-health dashboard.

Data source is resolved at request time:

* if ``data/bq_export.json`` exists (written by ``scripts/bq_refresh.sh``) it is
  used — this is the production path, covering the full global universe;
* otherwise the bundled ``data/funding_snapshot.json`` is used, so the app runs
  with no warehouse access at all.

Either way the app itself never queries BigQuery, so serving is free and needs
no credentials.

Routes:
* ``GET /``                 - the HTML dashboard.
* ``GET /api/report``       - findings as JSON.
* ``GET /api/fixqueue.csv`` - the full fix queue as CSV.
* ``GET /healthz``          - liveness probe.
"""

from __future__ import annotations

from dataclasses import asdict

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse

from . import bq_source
from .checks import run_checks
from .dashboard import render_page
from .fixqueue import build_queue, to_csv
from .models import load_snapshot

app = FastAPI(title="Funding Data Health", docs_url="/docs")

SNAPSHOT = load_snapshot()


def _load():
    """Resolve (snapshot, report, queue, source_label) from the best source."""

    if bq_source.export_exists():
        report, queue, _by_country = bq_source.load_export()
        label = "Source: BigQuery (data_health.issues) · global universe"
        # Keep the snapshot object only for its round-count tile fallback.
        return SNAPSHOT, report, queue, label

    report = run_checks(SNAPSHOT.companies)
    queue = build_queue(report, SNAPSHOT.companies)
    label = f"Source: {SNAPSHOT.source} · snapshot {SNAPSHOT.pulled_at}"
    return SNAPSHOT, report, queue, label


@app.get("/", response_class=HTMLResponse, tags=["dashboard"])
def dashboard() -> HTMLResponse:
    snapshot, report, queue, label = _load()
    return HTMLResponse(render_page(snapshot, report, queue=queue, source_label=label))


@app.get("/api/report", tags=["api"])
def report_json() -> JSONResponse:
    _snapshot, report, _queue, label = _load()
    payload = {
        "source": label,
        "summary": {
            "total_companies": report.total_companies,
            "total_issues": report.total_issues,
            "companies_affected": len(report.flagged_company_ids),
            "health_score": report.health_score,
        },
        "checks": [
            {
                "id": r.meta.id,
                "title": r.meta.title,
                "severity": r.meta.severity,
                "count": r.count,
                "displayed": len(r.issues),
                "truncated": r.is_truncated,
                "issues": [asdict(i) for i in r.issues],
            }
            for r in report.results
        ],
    }
    return JSONResponse(payload)


@app.get("/api/fixqueue.csv", response_class=PlainTextResponse, tags=["api"])
def fixqueue_csv() -> PlainTextResponse:
    _snapshot, _report, queue, _label = _load()
    return PlainTextResponse(
        to_csv(queue),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="fix_queue.csv"'},
    )


@app.get("/healthz", tags=["ops"])
def healthz() -> dict[str, str]:
    return {"status": "ok"}
