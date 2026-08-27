#!/usr/bin/env bash
# Full refresh: run the checks in BigQuery, roll up the summary, then export a
# SMALL json the dashboard reads. Only this script talks to BigQuery — the
# dashboard never does, so page views cost nothing.
#
# Intended to run on a schedule (BigQuery scheduled query, or cron/Cloud Run).
set -euo pipefail

PROJECT="${BQ_PROJECT:-omega-dahlia-347111}"
QUEUE_LIMIT="${QUEUE_LIMIT:-500}"   # rows per rule in the fix queue
# Size of the checked universe, shown as the denominator on the dashboard.
# Set to a COUNT(*) of the companies table once the real table name is known.
UNIVERSE_COUNT="${UNIVERSE_COUNT:-0}"
OUT="data/bq_export.json"

run_sql() {
  echo "→ $1"
  bq --project_id="${PROJECT}" query --use_legacy_sql=false --quiet < "$1" >/dev/null
}

run_sql sql/10_issues.sql
run_sql sql/20_summary.sql

echo "→ exporting top ${QUEUE_LIMIT} issues per rule to ${OUT}"
bq --project_id="${PROJECT}" query --use_legacy_sql=false --format=json --max_rows=1000000 <<SQL > "${OUT}.tmp"
WITH ranked AS (
  SELECT *, ROW_NUMBER() OVER (PARTITION BY rule_id ORDER BY impact_usd DESC NULLS LAST) AS rn
  FROM data_health.issues
)
SELECT
  (SELECT AS STRUCT
     ARRAY_AGG(STRUCT(rule_id, severity, issue_count, companies_affected, impact_usd_total))
   FROM data_health.summary)                                        AS summary,
  (SELECT ARRAY_AGG(STRUCT(hq_country, rule_id, issue_count))
   FROM data_health.summary_by_country)                             AS by_country,
  (SELECT ARRAY_AGG(STRUCT(rule_id, severity, company_id, company_name, company_slug,
                           hq_country, round_date, round_type, amount_usd, impact_usd, detail))
   FROM ranked WHERE rn <= ${QUEUE_LIMIT})                          AS queue,
  (SELECT COUNT(*) FROM data_health.issues)                          AS total_issues,
  (SELECT COUNT(DISTINCT company_id) FROM data_health.issues)        AS companies_affected,
  ${UNIVERSE_COUNT:-0}                                               AS universe_companies,
  CURRENT_TIMESTAMP()                                               AS exported_at
SQL

mv "${OUT}.tmp" "${OUT}"
echo "✓ wrote ${OUT} ($(wc -c < "${OUT}") bytes)"
echo "  The dashboard reads this file — no credentials needed at serve time."
