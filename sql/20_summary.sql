-- ============================================================================
-- Aggregates for the dashboard headline and slices. Reads only the small
-- `data_health.issues` table, so this is cheap regardless of universe size.
-- ============================================================================

CREATE OR REPLACE TABLE data_health.summary AS
SELECT
  i.rule_id,
  ANY_VALUE(i.severity)              AS severity,
  COUNT(*)                           AS issue_count,
  COUNT(DISTINCT i.company_id)       AS companies_affected,
  SUM(i.impact_usd)                  AS impact_usd_total,
  MAX(i.run_at)                      AS run_at,
  -- Movement since the previous run. NULL until a second run exists, which the
  -- dashboard renders as "awaiting 2nd run" rather than as zero — no movement
  -- and no comparison yet are different things.
  ANY_VALUE(m.newly_flagged)         AS newly_flagged,
  ANY_VALUE(m.no_longer_flagged)     AS no_longer_flagged,
  ANY_VALUE(m.persisting)            AS persisting,
  -- Fingerprint of the SQL that produced this run's rows. Movement between two
  -- runs only means something if this matches on both.
  ANY_VALUE(v.rule_version)          AS rule_version
FROM data_health.issues i
LEFT JOIN data_health.movement m USING (rule_id)
LEFT JOIN data_health.rule_versions v USING (rule_id)
GROUP BY i.rule_id;

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
