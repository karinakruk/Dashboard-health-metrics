# Funding Data Health

Automated **data-health checks** over Dealroom funding data, with a FastAPI
dashboard that surfaces an actionable fix queue — what to correct first, ranked
by the money at stake.

## What it checks

| # | Check | Why it matters |
|---|-------|----------------|
| 1 | **Big rounds not verified** | Rounds ≥ $10M carrying Dealroom's literal `Unverified` status. |
| 2 | **Rounds without a round type** | A funding round with no type set. Shouldn't happen. |
| 3 | **Rounds out of stage order** | An earlier-stage round recorded *after* a later-stage one. |
| 4 | **Late stage with no early stage** | A late-stage round but no early-stage round on record — possible duplicate profile or missed early rounds. |
| 5 | **Big rounds without a location** | Big rounds on profiles with no location, so the amount can't flow into an ecosystem's value. |
| 6 | **High funding, few employees** | High funding/valuation but < 10 employees — headcount likely missing or stale. |

Verification is **read, not inferred**: check 1 fires only when a round is
explicitly marked unverified. Unknown status is never reported as a problem, and
there is no round-type carve-out — an unverified $15B acquisition is still an
unverified big round.

## Architecture

The checks run **in BigQuery**; the dashboard only ever reads a small exported
file. That keeps serving free and credential-less no matter how large the
universe is.

```
BigQuery (raw)
  └── sql/10_issues.sql    six checks → data_health.issues   (one row per issue)
  └── sql/20_summary.sql   rollups    → data_health.summary / _by_country / runs
        └── scripts/bq_refresh.sh  → data/bq_export.json  (summary + top-N queue)
              └── FastAPI dashboard reads that JSON. No warehouse access at serve time.
```

**Scale matters here.** At global scope a single rule flags tens of thousands of
records (e.g. ~14,700 transactions are ≥$10M and unverified), so the dashboard
never tries to render them all. It shows: true counts, aggregates by country, and
the **top-N highest-impact rows** — with the full set one CSV away. Where rows are
truncated the UI says so explicitly rather than implying the list is complete.

### Setting it up

```bash
gcloud auth login
```

1. **Discover and validate the schema** — `sql/00_schema_check.sql` lists candidate
   tables and asserts every column the checks assume actually exists.
2. **Map the real tables** — edit only the two `>>> EDIT HERE` CTEs at the top of
   `sql/10_issues.sql`. Everything downstream reads a normalized shape.
3. **Estimate cost before running** (nothing is billed by a dry run):

```bash
./scripts/bq_dry_run.sh
```

4. **Refresh** — run the checks, roll up, and export:

```bash
./scripts/bq_refresh.sh
```

Schedule step 4 (BigQuery scheduled query, cron, or Cloud Run job) and the
dashboard stays current. Because on-demand BigQuery bills per *byte scanned* and
the checks touch only a handful of columns once per refresh, cost stays low —
dashboard page views cost nothing at all.

## Run the dashboard

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

If `data/bq_export.json` is present it is used; otherwise the app falls back to
the bundled snapshot, so it runs with no warehouse access.

Endpoints: `/` (dashboard), `/api/report` (JSON), `/api/fixqueue.csv` (full
queue), `/healthz`, `/docs`.

## Tests

```bash
pytest
```

23 tests cover each rule's boundaries (including that unknown verification is
never flagged) and the BigQuery export path, which is exercised with a synthetic
payload shaped exactly like `bq --format=json` output — string INT64s, truncated
counts and all — so the global-scale path is verified without warehouse access.

## The fallback snapshot

`data/funding_snapshot.json` is a real Dealroom pull (9 companies, 115 rounds)
used for local development and demos. It is rebuilt by
[`scripts/build_snapshot.py`](scripts/build_snapshot.py), which documents the
provenance of every field. Notes:

- Non-USD amounts are converted at fixed approximate rates — fine for the ≥$10M
  thresholds, not for exact accounting.
- Per-round verification status was sourced from the transactions index
  (`is_verified`). Only one round in the sample is genuinely unverified
  (Klarna's $1.4B IPO); rounds whose status wasn't confirmed are recorded as
  unknown, not as unverified.
- Null HQ locations and null round types are **genuine gaps** in the source —
  exactly what checks 2 and 5 exist to surface.

## Layout

```
sql/         00_schema_check · 10_issues · 20_summary   (the checks, in SQL)
scripts/     bq_dry_run.sh · bq_refresh.sh · build_snapshot.py
app/
  models.py    Company / Round dataclasses + snapshot loader (pure)
  checks.py    the six checks + report aggregation (pure)
  fixqueue.py  impact ranking, capping, CSV export
  bq_source.py reads the BigQuery export
  dashboard.py self-contained themed HTML (light/dark)
  main.py      FastAPI app
tests/       test_checks.py · test_bq_source.py
```
