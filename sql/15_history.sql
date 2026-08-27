-- ============================================================================
-- Issue history — the memory that makes "how many did we fix?" answerable.
--
-- data_health.issues is CREATE OR REPLACE, so each run destroys the last. This
-- appends every run's rows to a permanent, partitioned table BEFORE the rebuild
-- overwrites them, keyed by a stable identity so consecutive runs can be diffed.
--
-- Run order inside data_health.rebuild():
--     10_issues.sql   →  rebuild data_health.issues   (current state)
--     15_history.sql  →  append it to issue_history   (this file)
--     20_summary.sql  →  roll up counts + movement
--
-- Why this cannot come from the Dealroom app: a search only ever returns what
-- matches *now*. Nothing stores what matched yesterday, so progress is not
-- observable there at any price.
--
-- Size: ~57k rows per run, so a year of daily runs is ~20M rows — a few hundred
-- MB. Partitioning by run_date keeps the diff to two partitions rather than a
-- full scan.
-- ============================================================================

CREATE TABLE IF NOT EXISTS data_health.issue_history (
  run_date     DATE,
  rule_id      STRING,
  -- Stable identity for the flagged thing: the round for round-level checks,
  -- the company for company-level ones. Diffing consecutive runs on this is
  -- what separates "fixed" from "newly broken".
  issue_key    STRING,
  company_id   STRING,
  company_name STRING,
  company_url  STRING,
  hq_country   STRING,
  impact_usd   INT64
)
PARTITION BY run_date
CLUSTER BY rule_id;

-- Idempotent: re-running on the same day replaces that day rather than
-- double-counting it.
DELETE FROM data_health.issue_history WHERE run_date = CURRENT_DATE();

INSERT INTO data_health.issue_history
  (run_date, rule_id, issue_key, company_id, company_name, company_url,
   hq_country, impact_usd)
SELECT
  CURRENT_DATE()                                    AS run_date,
  rule_id,
  -- Round-level issues are identified by the round; company-level ones by the
  -- company. Prefixed so the two can never collide.
  CASE WHEN round_id IS NOT NULL THEN CONCAT('round:', round_id)
       ELSE CONCAT('company:', company_id) END      AS issue_key,
  company_id, company_name, company_url, hq_country,
  IFNULL(impact_usd, 0)                             AS impact_usd
FROM data_health.issues;
