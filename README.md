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

The checks run **in BigQuery**. A daily Apps Script lands the results in the
Dealroom dashboard Google Sheet, and the **Funding Data tab in
[Profile-edit-monitor](https://github.com/dealroom-ai/Profile-edit-monitor)**
reads that sheet as published CSV. That is the same pattern every other tab in
that dashboard already uses — no backend, no build step, no credentials in the
client.

```
BigQuery (raw)
  └── sql/10_issues.sql    six checks → data_health.issues   (one row per issue)
  └── sql/20_summary.sql   rollups    → data_health.summary / _by_country / runs
        └── apps_script/funding_health.gs   (daily trigger)
              → Sheet tabs: funding_health_summary (appends, gives the trend)
                            funding_health_queue   (replaced, top-N worklist)
                    └── Profile-edit-monitor → src/FundingDataHealth.tsx (#funding-data)
```

**Scale matters here.** At global scope a single rule flags tens of thousands of
records (e.g. ~14,700 rounds are ≥$10M and literally unverified), so nothing
tries to render them all. The sheet carries the summary plus the top-N rows per
rule; the tab reports the *true* count from the summary while displaying only
that slice, and says so rather than implying the list is complete.

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

4. **Schedule the checks** — run `sql/10_issues.sql` and `sql/20_summary.sql`
   as a BigQuery **scheduled query** (daily). These are the only steps that
   touch the raw warehouse.
5. **Land them in the Sheet** — follow the setup notes at the top of
   [`apps_script/funding_health.gs`](apps_script/funding_health.gs): paste it
   into the dashboard Sheet's Apps Script, enable the BigQuery service, run it
   once, then add a daily trigger.
6. **Wire the tab** — copy each created tab's `gid` from the Sheet URL into
   `GID_SUMMARY` / `GID_QUEUE` in `src/FundingDataHealth.tsx` in
   Profile-edit-monitor. Until then the tab renders a note saying exactly that.

Because on-demand BigQuery bills per *byte scanned* and the checks touch only a
handful of columns once per day, cost stays low. The Apps Script reads the small
`data_health.*` result tables (kilobytes), and dashboard page views cost nothing
at all — they only fetch a published CSV.

> **Note on the Sheet:** a published CSV is readable by anyone with the link.
> That is already how the other dashboard tabs work, so this adds no new
> exposure — but the queue does contain company names, so keep the sheet on the
> same sharing setting as the rest of the dashboard data.

## Local dev harness (not the product)

The production surface is the Funding Data tab in Profile-edit-monitor. This
repo also carries a small FastAPI app that renders the same checks locally —
useful for iterating on rule logic without BigQuery access, and for the CSV
export. It is a dev tool, not something to deploy.

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

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
apps_script/ funding_health.gs   (BigQuery → Sheet, daily trigger)
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

## Developing locally (no BigQuery, no Sheet)

The Funding Data tab works offline. Generate the sheet-shaped extracts from the
local snapshot, then run the dashboard:

```bash
PYTHONPATH=. python scripts/export_sheet_csv.py
```

That writes `funding_health_summary.csv` and `funding_health_queue.csv` into
Profile-edit-monitor's `public/dev-data/` using **exactly the schema the Apps
Script writes into the Sheet** — same columns, same rule ids. While
`GID_SUMMARY` / `GID_QUEUE` are blank the tab reads those files and shows a
"Local dev data" badge, so sample numbers can never be mistaken for the real
universe. Re-running appends a new dated row per rule, so the trend chart fills
in locally the same way the daily trigger fills it in production.

```bash
cd ../Profile-edit-monitor && npm run dev   # → http://localhost:5173/#funding-data
```
