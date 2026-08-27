-- ============================================================================
-- Movement between the two most recent runs: what got fixed, what appeared,
-- and what nobody has touched.
--
-- Reads only issue_history, and only its two newest partitions.
--
-- Caveat on "fixed": it means "no longer flagged". A record also leaves the set
-- if it was deleted or merged, so this measures the problem going away rather
-- than proving human effort. Checks gated on recency (see rolling_window_months
-- in 10_issues.sql) would otherwise let rows age out of the window and count as
-- fixed — which is why that gate rolls rather than sitting on a fixed year.
-- ============================================================================

CREATE OR REPLACE TABLE data_health.movement AS
WITH runs AS (
  SELECT DISTINCT run_date FROM data_health.issue_history
),
latest AS (SELECT MAX(run_date) AS d FROM runs),
previous AS (
  SELECT MAX(run_date) AS d FROM runs WHERE run_date < (SELECT d FROM latest)
),
now_set AS (
  SELECT rule_id, issue_key FROM data_health.issue_history
  WHERE run_date = (SELECT d FROM latest)
),
before_set AS (
  SELECT rule_id, issue_key FROM data_health.issue_history
  WHERE run_date = (SELECT d FROM previous)
)
SELECT
  (SELECT d FROM latest)                                   AS run_date,
  (SELECT d FROM previous)                                 AS compared_to,
  COALESCE(n.rule_id, b.rule_id)                           AS rule_id,
  COUNTIF(b.issue_key IS NULL)                             AS newly_flagged,
  COUNTIF(n.issue_key IS NULL)                             AS no_longer_flagged,
  COUNTIF(n.issue_key IS NOT NULL AND b.issue_key IS NOT NULL) AS persisting
FROM now_set n
FULL OUTER JOIN before_set b
  ON n.rule_id = b.rule_id AND n.issue_key = b.issue_key
GROUP BY rule_id;
