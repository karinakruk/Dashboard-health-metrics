"""FastAPI app serving the funding data-health dashboard.

Routes:
* ``GET /``          - the HTML dashboard.
* ``GET /api/report`` - the same findings as JSON (for programmatic use / tests).
* ``GET /healthz``   - liveness probe.

The snapshot is loaded once at startup and the checks are cheap, so each request
just re-runs them (keeps the data and derived report always consistent).
"""

from __future__ import annotations

from dataclasses import asdict

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse

from .checks import run_checks
from .dashboard import render_page
from .models import load_snapshot

app = FastAPI(title="Funding Data Health", docs_url="/docs")

SNAPSHOT = load_snapshot()


@app.get("/", response_class=HTMLResponse, tags=["dashboard"])
def dashboard() -> HTMLResponse:
    report = run_checks(SNAPSHOT.companies)
    return HTMLResponse(render_page(SNAPSHOT, report))


@app.get("/api/report", tags=["api"])
def report_json() -> JSONResponse:
    report = run_checks(SNAPSHOT.companies)
    payload = {
        "source": SNAPSHOT.source,
        "pulled_at": SNAPSHOT.pulled_at,
        "summary": {
            "total_companies": report.total_companies,
            "total_issues": report.total_issues,
            "companies_affected": len(report.flagged_company_ids),
            "clean_companies": report.clean_companies,
            "health_score": report.health_score,
        },
        "checks": [
            {
                "id": r.meta.id,
                "title": r.meta.title,
                "severity": r.meta.severity,
                "count": r.count,
                "issues": [asdict(i) for i in r.issues],
            }
            for r in report.results
        ],
    }
    return JSONResponse(payload)


@app.get("/healthz", tags=["ops"])
def healthz() -> dict[str, str]:
    return {"status": "ok"}
