# Funding Data Health

A small FastAPI dashboard that runs automated **data-health checks** over
Dealroom funding data and surfaces the records that need attention.

![checks](https://img.shields.io/badge/checks-6-blue) ![data-source-real-Dealroom-snapshot](https://img.shields.io/badge/data-real%20Dealroom%20snapshot-1baf7a)

## What it checks

| # | Check | Why it matters |
|---|-------|----------------|
| 1 | **Big rounds not verified** | Rounds ≥ $10M with no lead investor and no valuation — likely entered but not yet verified. |
| 2 | **Rounds without a round type** | A funding round with no type set. Shouldn't happen. |
| 3 | **Rounds out of stage order** | An earlier-stage round recorded *after* a later-stage one (generalises "early round after a late round"). |
| 4 | **Late stage with no early stage** | A late-stage round but no early-stage round on record — possible duplicate profile or missed early rounds. |
| 5 | **Big rounds without a location** | Big rounds on profiles with no location, so the amount can't flow into an ecosystem's value. |
| 6 | **High funding, few employees** | High funding/valuation but < 10 employees — headcount is likely missing or stale. |

Each check contributes to a weighted **data-health score** (0–100) and a
drill-down table of the exact companies and rounds flagged.

## Run it

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
# open http://127.0.0.1:8000
```

Endpoints: `/` (dashboard), `/api/report` (JSON), `/healthz` (liveness), `/docs` (OpenAPI).

## Tests

```bash
pip install -r requirements-dev.txt
pytest
```

## The data

`data/funding_snapshot.json` is a **real Dealroom snapshot** (9 companies, 115
funding rounds) pulled on 2026-07-16 via the Dealroom API
(`analyze_company` + `entity_fundings`). It is rebuilt from
[`scripts/build_snapshot.py`](scripts/build_snapshot.py), which documents the
provenance of every field. To refresh:

```bash
python scripts/build_snapshot.py
```

**Transparency notes** (kept explicit rather than hidden):

- Non-USD round amounts are converted to USD at fixed approximate rates — fine
  for the ≥$10M anomaly thresholds, not for exact accounting.
- The endpoints used don't expose Dealroom's internal *verified* flag, so
  `verified` is a **completeness proxy**: a disclosed amount plus a lead investor
  or a stated valuation. Dealroom's transaction index exposes a real
  `is_verified` field — swap it in for production.
- Where a company's HQ location or a round's type is `null`, that is a genuine
  gap in the pulled data — which is exactly what checks 2 and 5 exist to surface.

## Layout

```
app/
  models.py      # Company / Round dataclasses + snapshot loader (pure)
  checks.py      # the six checks + report aggregation (pure)
  dashboard.py   # self-contained themed HTML renderer
  main.py        # FastAPI app
data/
  funding_snapshot.json
scripts/
  build_snapshot.py   # provenance + rebuilds the snapshot
tests/
  test_checks.py
```
