-- ============================================================================
-- Aggregates for the dashboard headline and slices. Reads only the small
-- `data_health.issues` table, so this is cheap regardless of universe size.
-- ============================================================================

CREATE OR REPLACE TABLE data_health.summary AS
SELECT
  rule_id,
  ANY_VALUE(severity)              AS severity,
  COUNT(*)                         AS issue_count,
  COUNT(DISTINCT company_id)       AS companies_affected,
  SUM(impact_usd)                  AS impact_usd_total,
  MAX(run_at)                      AS run_at
FROM data_health.issues
GROUP BY rule_id;

-- Slice layer: issue counts by country, so "all the data" is represented as
-- aggregates rather than as an unrenderable row dump.
CREATE OR REPLACE TABLE data_health.summary_by_country AS
SELECT
  COALESCE(hq_country, '(no location)') AS hq_country,
  rule_id,
  COUNT(*)                   AS issue_count,
  COUNT(DISTINCT company_id) AS companies_affected
FROM data_health.issues
GROUP BY hq_country, rule_id;

-- Trend: one row per run, appended so health can be tracked over time.
CREATE TABLE IF NOT EXISTS data_health.runs (
  run_at             TIMESTAMP,
  rule_id            STRING,
  issue_count        INT64,
  companies_affected INT64
);

INSERT INTO data_health.runs (run_at, rule_id, issue_count, companies_affected)
SELECT run_at, rule_id, issue_count, companies_affected FROM data_health.summary;
