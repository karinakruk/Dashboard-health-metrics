-- ============================================================================
-- GENERATED FILE — do not edit by hand.
--   Source: sql/10_issues.sql + sql/20_summary.sql
--   Rebuild: PYTHONPATH=. python scripts/build_procedure.py
--
-- Creates data_health.rebuild(), which recomputes every check. Run this file
-- ONCE in the BigQuery console; after that the daily Apps Script trigger calls
-- the procedure, so the checks and the Sheet refresh happen in one ordered step.
-- ============================================================================

CREATE SCHEMA IF NOT EXISTS data_health
  OPTIONS(location = 'europe-west4');

CREATE OR REPLACE PROCEDURE data_health.rebuild()
BEGIN
  DECLARE big_round_threshold_usd INT64   DEFAULT 10000000;
  DECLARE high_funding_usd        INT64   DEFAULT 25000000;
  DECLARE high_valuation_usd      INT64   DEFAULT 100000000;
  DECLARE employee_floor          INT64   DEFAULT 10;

    -- ── from 10_issues.sql ──
  -- ============================================================================
  -- Funding data-health checks — global scope.
  --
  -- Materializes one row per issue into `data_health.issues`, ranked by impact so
  -- the dashboard can show an actionable fix queue.
  --
  -- HOW TO ADAPT THIS TO THE REAL SCHEMA
  -- Only the two mapping CTEs below (`companies`, `rounds`) touch real table and
  -- column names. Everything after them reads the normalized shape, so pointing
  -- this at production means editing those two blocks and nothing else.
  -- Run `sql/00_schema_check.sql` first — it verifies the assumed columns exist.
  -- ============================================================================



  CREATE OR REPLACE TABLE data_health.issues
  CLUSTER BY rule_id AS

  WITH
  -- ── Source mapping (verified against the live schema, 2026-08-21) ──────────
  -- dealroom_intelligence.entities : 5.8M rows / 8.85 GB, 92 cols. Companies are
  --   entity_type = 'organization' (people share the table). dealroom_url is
  --   always populated, so profile links are read, never constructed.
  -- dealroom_intelligence.funding  : 1.09M rows / 0.18 GB. flg_is_verified is
  --   Dealroom's own verification flag — the one shown as "Unverified" on the
  --   profile. Dates are year/month integers, not a DATE column.
  companies AS (
    SELECT
      CAST(e.id AS STRING)              AS company_id,
      e.name                            AS company_name,
      e.dealroom_url                    AS company_url,
      (SELECT l.country FROM UNNEST(e.locations) l
        WHERE l.flg_is_hq AND l.country IS NOT NULL LIMIT 1) AS hq_country,
      -- Location completeness needs country AND city on the same record. Street
      -- address is not required. The two failure modes are tracked separately
      -- because they are different amounts of work to fix: adding a missing city
      -- to a known country is quick, having neither is a research job.
      EXISTS(SELECT 1 FROM UNNEST(e.locations) l
               WHERE l.country IS NOT NULL AND TRIM(l.country) != ''
                 AND l.city    IS NOT NULL AND TRIM(l.city)    != '') AS has_location,
      EXISTS(SELECT 1 FROM UNNEST(e.locations) l
               WHERE l.country IS NOT NULL AND TRIM(l.country) != '') AS has_country,
      CAST(e.employees AS INT64)             AS employees,
      CAST(e.total_funding_usd AS INT64)     AS total_funding_usd,
      CAST(e.latest_valuation_usd AS INT64)  AS latest_valuation_usd
    FROM dealroom_intelligence.entities e
    WHERE e.entity_type = 'organization'
  ),
  rounds AS (
    SELECT
      CAST(f.id AS STRING)        AS round_id,
      CAST(f.entity_id AS STRING) AS company_id,
      -- Sortable YYYY-MM; month is often absent, year rarely.
      CASE
        WHEN f.year IS NULL  THEN NULL
        WHEN f.month IS NULL THEN FORMAT('%04d', f.year)
        ELSE FORMAT('%04d-%02d', f.year, f.month)
      END                         AS round_date,
      f.round                     AS round_type,
      CAST(f.amount_usd AS INT64) AS amount_usd,
      CAST(f.valuation_usd AS INT64) AS valuation_usd,
      f.flg_is_verified           AS is_verified,  -- nullable: NULL = unknown
      CAST(f.year AS INT64)       AS round_year
    FROM dealroom_intelligence.funding f
    -- Only actual funding rounds. The funding table also holds acquisitions,
    -- post-IPO equity/debt, secondaries and ICOs; counting those as "rounds"
    -- overstated every round-level check by ~4.3k rows and did not match the
    -- app's transactions.rounds view. Read Dealroom's own flag rather than
    -- maintaining a list of round types to exclude.
    WHERE f.flg_is_funding_round
  ),
  -- ---------------------------------------------------------------------------
  -- Rounds with a normalised type, used by every round-level check.
  r AS (
    SELECT
      rounds.*,
      UPPER(TRIM(COALESCE(rounds.round_type, ''))) AS rtype_norm
    FROM rounds
  ),

  -- Latest round year per company, so company-level issues can still be
  -- prioritised by recency.
  company_latest_year AS (
    SELECT company_id, MAX(round_year) AS latest_round_year
    FROM r GROUP BY company_id
  ),

  company_big_rounds AS (
    SELECT
      company_id,
      COUNT(*)         AS big_round_count,
      MAX(amount_usd)  AS biggest_amount_usd
    FROM r
    WHERE amount_usd >= big_round_threshold_usd
    GROUP BY company_id
  ),

  -- =========================== the five checks ==============================

  -- 1. Big rounds (>=$10M) carrying the literal "Unverified" status.
  --    Reads is_verified as recorded — no inference, and no round-type carve-out
  --    (an unverified $15B acquisition is still an unverified big round).
  big_unverified AS (
    SELECT
      'big_unverified' AS rule_id, 'serious' AS severity,
      c.company_id, c.company_name, c.company_url, c.hq_country,
      r.round_id, r.round_date, r.round_type, r.amount_usd,
      r.amount_usd AS impact_usd,
      'Round is marked Unverified in Dealroom.' AS detail,
      r.round_year
    FROM r
    JOIN companies c USING (company_id)
    WHERE r.amount_usd >= big_round_threshold_usd
      AND r.is_verified = FALSE
  ),

  -- 2. Rounds with no round type set.
  missing_round_type AS (
    SELECT
      'missing_round_type', 'critical',
      c.company_id, c.company_name, c.company_url, c.hq_country,
      r.round_id, r.round_date, r.round_type, r.amount_usd,
      r.amount_usd,
      'Round has no round type set.',
      r.round_year
    FROM r
    JOIN companies c USING (company_id)
    WHERE r.rtype_norm IN ('', 'NOT SET')
  ),

  -- 5a. Big rounds where the country is known but the city is missing.
  --     Quick fix: the ecosystem is already identifiable, the city just needs adding.
  big_round_missing_city AS (
    SELECT
      'big_round_missing_city', 'warning',
      c.company_id, c.company_name, c.company_url, c.hq_country,
      CAST(NULL AS STRING), CAST(NULL AS STRING), CAST(NULL AS STRING), b.biggest_amount_usd,
      b.biggest_amount_usd,
      CONCAT(CAST(b.big_round_count AS STRING),
             ' big round(s); country is set but the city is missing.'),
      y.latest_round_year
    FROM company_big_rounds b
    JOIN companies c USING (company_id)
    LEFT JOIN company_latest_year y USING (company_id)
    WHERE NOT c.has_location AND c.has_country
  ),

  -- 5b. Big rounds with neither country nor city — the amount cannot reach any
  --     ecosystem at all. Needs research, so it is the more serious of the two.
  big_round_missing_location AS (
    SELECT
      'big_round_missing_location', 'serious',
      c.company_id, c.company_name, c.company_url, c.hq_country,
      CAST(NULL AS STRING), CAST(NULL AS STRING), CAST(NULL AS STRING), b.biggest_amount_usd,
      b.biggest_amount_usd,
      CONCAT(CAST(b.big_round_count AS STRING),
             ' big round(s) with no country or city — the amount cannot flow into an ecosystem value.'),
      y.latest_round_year
    FROM company_big_rounds b
    JOIN companies c USING (company_id)
    LEFT JOIN company_latest_year y USING (company_id)
    WHERE NOT c.has_country
  ),

  -- 6. High funding or valuation but fewer than 10 employees.
  high_funding_few_employees AS (
    SELECT
      'high_funding_few_employees', 'serious',
      c.company_id, c.company_name, c.company_url, c.hq_country,
      CAST(NULL AS STRING), CAST(NULL AS STRING), CAST(NULL AS STRING), c.total_funding_usd,
      c.total_funding_usd,
      CONCAT(CAST(c.employees AS STRING), ' employees but ',
             FORMAT('%.1f', c.total_funding_usd / 1e9), 'B total funding — headcount likely missing or stale.'),
      y.latest_round_year
    FROM companies c
    LEFT JOIN company_latest_year y USING (company_id)
    WHERE c.employees IS NOT NULL
      AND c.employees < employee_floor
      AND (c.total_funding_usd >= high_funding_usd
           OR COALESCE(c.latest_valuation_usd, 0) >= high_valuation_usd)
  )

  SELECT
    CURRENT_TIMESTAMP() AS run_at,
    *
  FROM (
    SELECT * FROM big_unverified
    UNION ALL SELECT * FROM missing_round_type
    UNION ALL SELECT * FROM big_round_missing_city
    UNION ALL SELECT * FROM big_round_missing_location
    UNION ALL SELECT * FROM high_funding_few_employees
  );
  -- No ORDER BY here: a clustered CTAS cannot be ordered, and clustering by
  -- rule_id is what makes the per-rule reads cheap. Ranking by impact happens in
  -- 20_summary.sql and in the Apps Script export, which is where it matters.

    -- ── from 20_summary.sql ──
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
END;
