"""Emit the two sheet-shaped CSVs for local development.

Produces exactly the schema `apps_script/funding_health.gs` writes into the
dashboard Sheet, but from the local snapshot — so the Profile-edit-monitor tab
can be developed with no BigQuery access, no Apps Script and no published
Sheet, and still exercise the real rule logic and the real column names.

    PYTHONPATH=. python scripts/export_sheet_csv.py               # local snapshot
    PYTHONPATH=. python scripts/export_sheet_csv.py --from-bigquery  # real global data

With --from-bigquery it reads the materialized data_health.* tables through the
bq CLI, so the local dashboard shows the true global numbers without any Sheet,
Apps Script or OAuth in the loop. Requires `gcloud auth login`.

Default output is Profile-edit-monitor's dev-data folder if it sits alongside
this repo, otherwise ./dev-data.

Mirrors production behaviour:
  * summary  APPENDS one dated row per rule per run (so repeated runs build a
             trend locally, exactly as the daily trigger does)
  * queue    is REPLACED each run — it is a worklist, not history
"""

from __future__ import annotations

import argparse
import csv
import json
import subprocess
from datetime import date
from pathlib import Path

from app.checks import run_checks
from app.fixqueue import build_queue
from app.models import load_snapshot

SUMMARY_FILE = "funding_health_summary.csv"
QUEUE_FILE = "funding_health_queue.csv"

SUMMARY_HEADERS = [
    "run_date", "rule_id", "severity", "issue_count",
    "companies_affected", "impact_usd_total",
]
QUEUE_HEADERS = [
    "run_date", "rule_id", "severity", "company_name", "company_url",
    "hq_country", "round_date", "round_type", "amount_usd", "impact_usd", "detail",
]


PROJECT = "omega-dahlia-347111"
LOCATION = "europe-west4"

SUMMARY_SQL = """
SELECT rule_id, severity, issue_count, companies_affected,
       CAST(IFNULL(impact_usd_total, 0) AS INT64) AS impact_usd_total
FROM data_health.summary ORDER BY issue_count DESC
"""

QUEUE_SQL = """
WITH ranked AS (
  SELECT *, ROW_NUMBER() OVER (
    PARTITION BY rule_id ORDER BY impact_usd DESC NULLS LAST) AS rn
  FROM data_health.issues)
SELECT rule_id, severity, company_name, IFNULL(company_url,'') AS company_url,
       IFNULL(hq_country,'') AS hq_country, IFNULL(round_date,'') AS round_date,
       IFNULL(round_type,'') AS round_type,
       CAST(IFNULL(amount_usd,0) AS INT64) AS amount_usd,
       CAST(IFNULL(impact_usd,0) AS INT64) AS impact_usd, detail
FROM ranked WHERE rn <= {limit}
ORDER BY impact_usd DESC
"""


def bq_json(sql: str) -> list[dict]:
    """Run a query through the bq CLI and return rows as dicts."""
    out = subprocess.run(
        ["bq", f"--project_id={PROJECT}", f"--location={LOCATION}", "query",
         "--use_legacy_sql=false", "--format=json", "--max_rows=100000", sql],
        capture_output=True, text=True,
    )
    if out.returncode != 0:
        raise SystemExit(
            "bq failed — is `gcloud auth login` current?\n" + out.stderr[-800:])
    return json.loads(out.stdout or "[]")


def from_bigquery(out: Path, run_date: str, limit: int) -> tuple[int, int]:
    """Write both CSVs from the materialized data_health.* tables."""
    summary = bq_json(SUMMARY_SQL)
    queue = bq_json(QUEUE_SQL.format(limit=limit))

    summary_path = out / SUMMARY_FILE
    existing: list[dict] = []
    if summary_path.exists():
        with summary_path.open() as fh:
            existing = [r for r in csv.DictReader(fh)
                        if r.get("run_date") != run_date]
    with summary_path.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=SUMMARY_HEADERS)
        w.writeheader()
        w.writerows(existing)
        for r in summary:
            w.writerow({"run_date": run_date, **{k: r[k] for k in SUMMARY_HEADERS[1:]}})

    with (out / QUEUE_FILE).open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=QUEUE_HEADERS)
        w.writeheader()
        for r in queue:
            w.writerow({"run_date": run_date, **{k: r[k] for k in QUEUE_HEADERS[1:]}})

    return len(summary), len(queue)


def default_out() -> Path:
    sibling = Path(__file__).resolve().parent.parent.parent / "Profile-edit-monitor"
    if sibling.is_dir():
        return sibling / "public" / "dev-data"
    return Path("dev-data")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument("--run-date", default=date.today().isoformat())
    ap.add_argument("--from-bigquery", action="store_true",
                    help="read the real data_health.* tables instead of the local snapshot")
    ap.add_argument("--limit", type=int, default=300,
                    help="rows per rule in the queue (default 300)")
    args = ap.parse_args()

    out = args.out or default_out()
    out.mkdir(parents=True, exist_ok=True)

    if args.from_bigquery:
        n_sum, n_queue = from_bigquery(out, args.run_date, args.limit)
        print(f"Wrote {out / SUMMARY_FILE}  ({n_sum} rules)")
        print(f"Wrote {out / QUEUE_FILE}  ({n_queue} rows, top {args.limit} per rule)")
        print("\nReal global data — the tab reads it while the sheet gids are blank.")
        return

    snapshot = load_snapshot()
    report = run_checks(snapshot.companies)
    queue = build_queue(report, snapshot.companies, limit=300)
    by_id = {c.id: c for c in snapshot.companies}

    # ── summary: append this run, replacing any row already stored for it ──
    summary_path = out / SUMMARY_FILE
    existing: list[dict] = []
    if summary_path.exists():
        with summary_path.open() as fh:
            existing = [r for r in csv.DictReader(fh)
                        if r.get("run_date") != args.run_date]

    new_rows = [
        {
            "run_date": args.run_date,
            "rule_id": r.meta.id,
            "severity": r.meta.severity,
            "issue_count": r.count,
            "companies_affected": len({i.company_id for i in r.issues}),
            "impact_usd_total": sum(
                i.amount_usd or by_id[i.company_id].total_funding_usd
                for i in r.issues if i.company_id in by_id
            ),
        }
        for r in report.results
    ]

    with summary_path.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=SUMMARY_HEADERS)
        w.writeheader()
        w.writerows(existing + new_rows)

    # ── queue: replaced wholesale ──
    with (out / QUEUE_FILE).open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=QUEUE_HEADERS)
        w.writeheader()
        for item in queue:
            company = by_id.get(
                next((c.id for c in snapshot.companies if c.name == item.company_name), "")
            )
            w.writerow({
                "run_date": args.run_date,
                "rule_id": item.rule_id,
                "severity": item.severity,
                "company_name": item.company_name,
                "company_url": item.company_url,
                "hq_country": item.country or "",
                "round_date": item.round_date or "",
                "round_type": item.round_type or "",
                "amount_usd": item.impact_usd if item.round_date else 0,
                "impact_usd": item.impact_usd,
                "detail": item.detail,
            })
            del company  # slug comes from the queue item's own URL

    runs = len({r["run_date"] for r in existing} | {args.run_date})
    print(f"Wrote {summary_path}  ({len(existing) + len(new_rows)} rows, {runs} run(s))")
    print(f"Wrote {out / QUEUE_FILE}  ({len(queue)} rows)")
    print("\nThe Funding Data tab reads these automatically while the sheet gids are blank.")


if __name__ == "__main__":
    main()
