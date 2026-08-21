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

DECLARE big_round_threshold_usd INT64   DEFAULT 10000000;
DECLARE high_funding_usd        INT64   DEFAULT 25000000;
DECLARE high_valuation_usd      INT64   DEFAULT 100000000;
DECLARE employee_floor          INT64   DEFAULT 10;

CREATE SCHEMA IF NOT EXISTS data_health;

CREATE OR REPLACE TABLE data_health.issues
CLUSTER BY rule_id AS

WITH
-- >>> EDIT HERE (1/2): map the real companies table onto this shape ----------
companies AS (
  SELECT
    CAST(id            AS STRING) AS company_id,
    name                          AS company_name,
    path                          AS company_slug,
    hq_country                    AS hq_country,
    -- "has a location" = anything usable for ecosystem attribution
    (hq_country IS NOT NULL AND TRIM(hq_country) != '') AS has_location,
    CAST(employees_latest AS INT64)   AS employees,
    CAST(total_funding_usd AS INT64)  AS total_funding_usd,
    CAST(last_valuation_usd AS INT64) AS latest_valuation_usd
  FROM `dealroom_intelligence.companies`
),
-- >>> EDIT HERE (2/2): map the real funding-rounds table onto this shape -----
rounds AS (
  SELECT
    CAST(id         AS STRING) AS round_id,
    CAST(company_id AS STRING) AS company_id,
    round_date                 AS round_date,
    round_type                 AS round_type,
    CAST(amount_usd    AS INT64) AS amount_usd,
    CAST(valuation_usd AS INT64) AS valuation_usd,
    is_verified                AS is_verified  -- nullable: NULL = unknown, not unverified
  FROM `dealroom_intelligence.funding_rounds`
),
-- ---------------------------------------------------------------------------
-- Stage ladder. Types absent here are not a fundraising *stage* (debt, grants,
-- secondaries, M&A) and are ignored by the sequence checks.
stage_tiers AS (
  SELECT * FROM UNNEST([
    STRUCT('PRE-SEED' AS round_type, 1 AS tier), ('ANGEL', 1), ('MICRO-SEED', 1),
    ('SEED', 1), ('SEED EXTENSION', 1), ('EARLY VC', 1), ('SERIES A', 1),
    ('SERIES B', 2), ('SERIES C', 2),
    ('SERIES D', 3), ('SERIES E', 3), ('SERIES F', 3), ('SERIES G', 3),
    ('SERIES H', 3), ('LATE VC', 3),
    ('GROWTH EQUITY VC', 3), ('GROWTH EQUITY NON VC', 3),
    ('IPO', 4), ('POST IPO DEBT', 4), ('POST IPO EQUITY', 4),
    ('POST IPO CONVERTIBLE', 4), ('POST IPO SECONDARY', 4)
  ])
),
r AS (
  SELECT
    rounds.*,
    UPPER(TRIM(COALESCE(rounds.round_type, ''))) AS rtype_norm,
    stage_tiers.tier                             AS tier
  FROM rounds
  LEFT JOIN stage_tiers
    ON UPPER(TRIM(rounds.round_type)) = stage_tiers.round_type
),

-- Highest stage tier reached *before* each round, for the sequence check.
r_seq AS (
  SELECT
    r.*,
    MAX(tier) OVER (
      PARTITION BY company_id
      ORDER BY round_date, tier DESC
      ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING
    ) AS prior_max_tier
  FROM r
),

company_tiers AS (
  SELECT
    company_id,
    LOGICAL_OR(tier >= 3) AS has_late,
    LOGICAL_OR(tier  = 1) AS has_early
  FROM r
  GROUP BY company_id
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

-- ============================ the six checks ==============================

-- 1. Big rounds (>=$10M) carrying the literal "Unverified" status.
--    Reads is_verified as recorded — no inference, and no round-type carve-out
--    (an unverified $15B acquisition is still an unverified big round).
big_unverified AS (
  SELECT
    'big_unverified' AS rule_id, 'serious' AS severity,
    c.company_id, c.company_name, c.company_slug, c.hq_country,
    r.round_id, r.round_date, r.round_type, r.amount_usd,
    r.amount_usd AS impact_usd,
    'Round is marked Unverified in Dealroom.' AS detail
  FROM r_seq r
  JOIN companies c USING (company_id)
  WHERE r.amount_usd >= big_round_threshold_usd
    AND r.is_verified = FALSE
),

-- 2. Rounds with no round type set.
missing_round_type AS (
  SELECT
    'missing_round_type', 'critical',
    c.company_id, c.company_name, c.company_slug, c.hq_country,
    r.round_id, r.round_date, r.round_type, r.amount_usd,
    r.amount_usd,
    'Round has no round type set.'
  FROM r_seq r
  JOIN companies c USING (company_id)
  WHERE r.rtype_norm IN ('', 'NOT SET')
),

-- 3. An earlier-stage round recorded after a later-stage one.
sequence_out_of_order AS (
  SELECT
    'sequence_out_of_order', 'warning',
    c.company_id, c.company_name, c.company_slug, c.hq_country,
    r.round_id, r.round_date, r.round_type, r.amount_usd,
    r.amount_usd,
    CONCAT(r.round_type, ' recorded after a later-stage round already took place.')
  FROM r_seq r
  JOIN companies c USING (company_id)
  WHERE r.tier IS NOT NULL
    AND r.prior_max_tier IS NOT NULL
    AND r.tier < r.prior_max_tier
),

-- 4. Late-stage rounds with no early-stage round on record.
late_without_early AS (
  SELECT
    'late_without_early', 'warning',
    c.company_id, c.company_name, c.company_slug, c.hq_country,
    NULL, NULL, NULL, NULL,
    c.total_funding_usd,
    'Has late-stage rounds but no early-stage round on record — possible duplicate profile or missing early rounds.'
  FROM company_tiers t
  JOIN companies c USING (company_id)
  WHERE t.has_late AND NOT t.has_early
),

-- 5. Big rounds on profiles with no location.
big_round_no_location AS (
  SELECT
    'big_round_no_location', 'serious',
    c.company_id, c.company_name, c.company_slug, c.hq_country,
    NULL, NULL, NULL, b.biggest_amount_usd,
    b.biggest_amount_usd,
    CONCAT(CAST(b.big_round_count AS STRING),
           ' big round(s) but no location set — the amount cannot flow into an ecosystem value.')
  FROM company_big_rounds b
  JOIN companies c USING (company_id)
  WHERE NOT c.has_location
),

-- 6. High funding or valuation but fewer than 10 employees.
high_funding_few_employees AS (
  SELECT
    'high_funding_few_employees', 'serious',
    c.company_id, c.company_name, c.company_slug, c.hq_country,
    NULL, NULL, NULL, c.total_funding_usd,
    c.total_funding_usd,
    CONCAT(CAST(c.employees AS STRING), ' employees but ',
           FORMAT('%.1f', c.total_funding_usd / 1e9), 'B total funding — headcount likely missing or stale.')
  FROM companies c
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
  UNION ALL SELECT * FROM sequence_out_of_order
  UNION ALL SELECT * FROM late_without_early
  UNION ALL SELECT * FROM big_round_no_location
  UNION ALL SELECT * FROM high_funding_few_employees
)
ORDER BY impact_usd DESC NULLS LAST;
