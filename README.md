# Data Health

Automated **data-quality checks** over Dealroom data, surfacing an actionable fix
queue — what to correct first, ranked by the value at stake. Results are shown in
the **Data Health** tab of
[Profile-edit-monitor](https://github.com/dealroom-ai/Profile-edit-monitor).

**Funding is the first domain covered**; the pipeline is built so further domains
(people, tagging, locations, …) drop in alongside it — each contributes its own
rules to the same `data_health.issues` table and the same fix queue.

## What it checks

Eight checks, in two groups. Every count is the whole database, not a sample.

**Funding**

| Check | Definition | Count |
|-------|-----------|------:|
| Rounds without a round type | Funding rounds with no round type set | 36,536 |
| Big rounds not verified | Rounds ≥ $10M carrying Dealroom's `Unverified` status | 10,419 |
| Big funding, ≤10 staff | ≥ $100M raised recently, ≤10 employees, excluding mature, post-acquisition and pre-1990 | 152 |
| No location | Funded recently with no country set — equals the app's `regions/not_Global` | 117 |

**Profile completeness.** The two company-profile checks are scoped to
VC-backed **tech** companies founded 1990 or later and not mature — the same
exclusions the team applies when working these lists, so the counts are
actionable rather than a backlog.

| Check | Definition | Count |
|-------|-----------|------:|
| VC-backed, no founder | VC-backed with no founder recorded | 123,217 |
| Investor, no key people | Investors with nobody in key people | 62,799 |
| VC-backed, no description | Neither tagline nor description | 6,626 |
| VC-backed, no web presence | Neither website nor LinkedIn | 2,659 |

Three principles, each learned from getting it wrong first:

* **Read Dealroom's flags, don't re-derive them.** `flg_is_verified`,
  `flg_is_funding_round`, `flg_is_vcbacked`. Hand-rolled approximations of the
  first two were both wrong; the second overstated every round-level check by
  ~4,300 rows.
* **A check is only as trustworthy as its link.** Where the app cannot express a
  rule, either the rule is redefined so it can be, or it ships with no link and
  a stated reason. A count that disagrees with its own link is worse than none.
* **Gates must roll, not sit on a fixed year.** A hard `2025` gate lets records
  age out of the window and be counted as *fixed* when nothing was fixed. The
  window is 24 rolling months, and the dashboard derives its link years from the
  same rule so the two cannot drift.

## The three checks the app cannot express

`sql/standalone/` holds a runnable query for each check with no app filter.
Paste one into the BigQuery console and it returns the companies, most-funded
first. Each is verified to return exactly the count the dashboard shows:

| Query | Returns | Why no app filter |
|-------|--------:|-------------------|
| `vc_no_description.sql` | 6,626 | no filter for a missing tagline or description |
| `investor_no_people.sql` | 62,799 | no filter for investors missing key people |
| `vc_no_web_presence.sql` | 2,659 | the app filters a missing website but not a missing LinkedIn |

## Measuring progress

`data_health.issues` is rebuilt each run, so on its own it can only ever say
what is broken now. Two extra steps make improvement observable:

* `15_history.sql` appends every run to `issue_history` (partitioned by date,
  keyed by a stable `issue_key`) before the rebuild overwrites it.
* `25_movement.sql` diffs the two newest runs into **fixed / newly flagged /
  persisting**.

That distinction matters: a net figure cancels work against decay. 210 fixed and
153 newly broken shows up as "−57", and an even split as "no change" — so a team
fixing hundreds could see a dashboard reporting nothing happened.

The Dealroom app cannot do this at any price: a search returns what matches now,
and nothing stores what matched yesterday.

**Known deviations.** Two checks differ slightly from the app for the same
underlying reason — a field measured differently there than in the warehouse:

| Check | Warehouse | App | Delta |
|-------|----------:|----:|------:|
| VC-backed, no founder | 123,206 | 123,116 | 0.07% |
| Big funding, ≤10 staff | 152 | 137 | ~10% |

For the founder check the filter is confirmed correct and the gap is noise. For
the headcount check, warehouse staleness, NULL launch years, NULL employees and
the mature/acquisition exclusions are all ruled out; relaxing every remaining
condition still caps below the app's figure, so one of employees, total_funding
or last_funding_year is measured differently there.

## Architecture

The checks run **in BigQuery**. A daily Apps Script lands the results in the
data-health Google Sheet, and the **Data Health tab in
[Profile-edit-monitor](https://github.com/dealroom-ai/Profile-edit-monitor)**
reads that sheet as published CSV. That is the same pattern every other tab in
that dashboard already uses — no backend, no build step, no credentials in the
client.

```
BigQuery (raw)
  └── sql/10_issues.sql    eight checks → data_health.issues (current state)
  └── sql/15_history.sql   append that run → data_health.issue_history
  └── sql/25_movement.sql  diff newest two runs → data_health.movement
  └── sql/20_summary.sql   rollups    → data_health.summary / _by_country / runs
        └── apps_script/funding_health.gs   (daily trigger)
              → Sheet tabs: funding_health_summary (appends, gives the trend)
                            funding_health_queue   (replaced, top-N worklist)
                    └── Profile-edit-monitor → src/DataHealth.tsx (#data-health)
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

4. **Create the rebuild procedure** — run
   [`sql/30_rebuild_procedure.sql`](sql/30_rebuild_procedure.sql) once in the
   BigQuery console. It wraps both check files in `data_health.rebuild()`, so
   the SQL has a single definition and the daily driver needs one line.
   Regenerate it with `PYTHONPATH=. python scripts/build_procedure.py` whenever
   the checks change.
5. **Land it in the Sheet** — paste
   [`apps_script/funding_health.gs`](apps_script/funding_health.gs) into the
   [Data Health sheet](https://docs.google.com/spreadsheets/d/1geXbBHZO4HXuoJbO8CkwBJlz5nbUzFaMqQEDBu_-MEM)
   (Extensions → Apps Script), add the BigQuery service, then add a daily
   trigger for **`dailyDataHealthRun`**. The sheet must be set to *Anyone with
   the link → Viewer* so the static dashboard can fetch its CSV.

   `dailyDataHealthRun` does both stages in order — recompute, then copy. That
   ordering matters: with two separate schedules the Sheet can refresh from
   tables that have not been rebuilt yet, and the dashboard then shows stale
   numbers while looking perfectly healthy.
6. **Nothing to wire in the tab** — it reads the sheet tabs by name, not by gid.
   (A wrong gid makes gviz silently fall back to sheet 0, which shows plausible
   but wrong numbers instead of failing.)

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
