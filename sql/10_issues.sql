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
    -- "Has a location" = country AND city, both present on the same location
    -- record. That is the granularity the data team needs to attribute a round
    -- to an ecosystem: a country with no city is not enough, and street address
    -- is not required. It need not be the flagged HQ.
    EXISTS(SELECT 1 FROM UNNEST(e.locations) l
             WHERE l.country IS NOT NULL AND TRIM(l.country) != ''
               AND l.city    IS NOT NULL AND TRIM(l.city)    != '') AS has_location,
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
    f.flg_is_verified           AS is_verified  -- nullable: NULL = unknown
  FROM dealroom_intelligence.funding f
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
    c.company_id, c.company_name, c.company_url, c.hq_country,
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
    c.company_id, c.company_name, c.company_url, c.hq_country,
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
    c.company_id, c.company_name, c.company_url, c.hq_country,
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
    c.company_id, c.company_name, c.company_url, c.hq_country,
    CAST(NULL AS STRING), CAST(NULL AS STRING), CAST(NULL AS STRING), CAST(NULL AS INT64),
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
    c.company_id, c.company_name, c.company_url, c.hq_country,
    CAST(NULL AS STRING), CAST(NULL AS STRING), CAST(NULL AS STRING), b.biggest_amount_usd,
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
    c.company_id, c.company_name, c.company_url, c.hq_country,
    CAST(NULL AS STRING), CAST(NULL AS STRING), CAST(NULL AS STRING), c.total_funding_usd,
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
);
-- No ORDER BY here: a clustered CTAS cannot be ordered, and clustering by
-- rule_id is what makes the per-rule reads cheap. Ranking by impact happens in
-- 20_summary.sql and in the Apps Script export, which is where it matters.
