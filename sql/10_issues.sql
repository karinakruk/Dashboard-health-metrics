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
DECLARE high_funding_usd        INT64   DEFAULT 100000000;  -- matches total_funding_min in the app link
DECLARE min_launch_year         INT64   DEFAULT 1990;
-- "Recently funded" is a ROLLING window, not a fixed year. With a hard 2025
-- gate, companies age out of the window as time passes and the movement report
-- would count them as fixed when nothing was fixed — a progress metric that
-- flatters itself. Rolling keeps "fixed" honest.
-- NOTE: the app links in the dashboard hardcode a year, so when this window
-- rolls past a year boundary those links need the same bump.
DECLARE rolling_window_months   INT64   DEFAULT 24;
DECLARE recent_funding_year     INT64   DEFAULT EXTRACT(YEAR FROM DATE_SUB(CURRENT_DATE(), INTERVAL rolling_window_months MONTH));
DECLARE employee_ceiling        INT64   DEFAULT 10;  -- inclusive, matching the app's {1, 2-10} buckets

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
    -- Location completeness = has a country. That is exactly what the app's
    -- regions/not_Global filter expresses (every located company carries Global
    -- in its country_region hierarchy), so the check and its link agree by
    -- construction. Street address is not required.
    EXISTS(SELECT 1 FROM UNNEST(e.locations) l
             WHERE l.country IS NOT NULL AND TRIM(l.country) != '') AS has_country,
    CAST(e.employees AS INT64)             AS employees,
    CAST(e.total_funding_usd AS INT64)     AS total_funding_usd,
    CAST(e.latest_valuation_usd AS INT64)  AS latest_valuation_usd,
    IFNULL(e.growth_stage_desc, '')        AS growth_stage,
    CAST(e.launch_year AS INT64)           AS launch_year,
    -- The company's LAST transaction, whatever its type. Deliberately not
    -- restricted to funding rounds: the app's last_funding_round filter offers
    -- ACQUISITION as a value, so it considers all transactions.
    --
    -- The year comes from MAX(funding.year), NOT from
    -- entities.last_funding_round_id: that pointer does not reliably reference
    -- the most recent transaction, and trusting it undercounted this check by
    -- roughly a third (81 rows instead of 115).
    e.flg_is_vcbacked                      AS is_vc_backed,
    -- "Outside Tech" is a SECTOR (dim_tags id 11028), not a separate tag list —
    -- it sits in entities.sectors alongside Hard Tech, Climate Tech and so on.
    NOT EXISTS(SELECT 1 FROM UNNEST(e.sectors) sec
                 WHERE sec.name = 'Outside Tech') AS is_tech,
    e.website                              AS website,
    e.linkedin                             AS linkedin,
    e.tagline                              AS tagline,
    e.about                                AS about,
    lr.round                               AS last_round_type,
    CAST(ly.last_funding_year AS INT64)    AS last_funding_year
  FROM dealroom_intelligence.entities e
  LEFT JOIN dealroom_intelligence.funding lr
    ON lr.id = e.last_funding_round_id
  LEFT JOIN (
    SELECT entity_id, MAX(year) AS last_funding_year
    FROM dealroom_intelligence.funding GROUP BY entity_id
  ) ly ON ly.entity_id = e.id
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
-- People linked to an entity. flg_is_founder marks the founder relationship;
-- any row at all means the entity has at least one person attached.
entity_founders AS (
  SELECT DISTINCT entity_id
  FROM dealroom_intelligence.people_organizations
  WHERE flg_is_founder
),
entity_people AS (
  SELECT DISTINCT entity_id FROM dealroom_intelligence.people_organizations
),
-- VC firms only. investors.bobject_investor_id keys back to entities.id.
--
-- Scoped to 'venture capital' deliberately: the investors table is dominated by
-- 'corporate' (98,142 of 215,689), i.e. operating companies that happen to have
-- made an investment. Including them filled the list with the likes of GAC Aion
-- and Sunwoda — companies, not investors, so "no key people" was not a
-- meaningful finding for them.
investor_entities AS (
  SELECT DISTINCT CAST(i.bobject_investor_id AS STRING) AS company_id
  FROM dealroom_intelligence.investors i
  WHERE 'venture capital' IN UNNEST(i.investor_types)
),

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

-- =========================== the eight checks =============================

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

-- 5. Recently funded companies with no country at all, so the amount cannot
--     reach any ecosystem. Gated on recency rather than round size, matching
--     the app filter this links to:
--       companies/f/last_funding_year_min/anyof_<year>/regions/not_Global
--     `regions/not_Global` is exactly "has no country": every located company
--     carries Global in its country_region hierarchy. Verified — both give
--     1,257,922 overall and 56 for 2025+.
missing_location AS (
  SELECT
    'missing_location', 'serious',
    c.company_id, c.company_name, c.company_url, c.hq_country,
    CAST(NULL AS STRING), CAST(NULL AS STRING), CAST(NULL AS STRING), c.total_funding_usd,
    c.total_funding_usd,
    'Funded recently but has no location set — the amount cannot flow into an ecosystem value.',
    y.latest_round_year
  FROM companies c
  JOIN company_latest_year y USING (company_id)
  WHERE y.latest_round_year >= recent_funding_year
    AND NOT c.has_country
),

-- 6. Heavily funded but almost no staff — headcount is probably missing.
--    Mirrors the app filter this links to, condition for condition:
--      employees_max/anyof_10
--      total_funding_min/anyof_100000000_USD
--      growth_stages/not_mature
--      last_funding_round/not_ACQUISITION
--      last_funding_year_min/anyof_2025
--      launch_year_min/anyof_1990
--    The exclusions matter: mature companies and post-acquisition shells
--    legitimately run on few staff, and pre-1990 launches are mostly bad data.
--    Verified against the app's own filter set.
high_funding_few_employees AS (
  SELECT
    'high_funding_few_employees', 'serious',
    c.company_id, c.company_name, c.company_url, c.hq_country,
    CAST(NULL AS STRING), CAST(NULL AS STRING), CAST(NULL AS STRING), c.total_funding_usd,
    c.total_funding_usd,
    CONCAT(
      CASE WHEN c.employees IS NULL THEN 'No employee count recorded'
           ELSE CONCAT(CAST(c.employees AS STRING), ' employees') END,
      ' but ', FORMAT('%.1f', c.total_funding_usd / 1e9),
      'B total funding — headcount likely missing or stale.'),
    c.last_funding_year
  FROM companies c
  -- An unknown employee count counts as <= the ceiling. This matches the app's
  -- employees_max filter, and it is also the stronger reading of the check: the
  -- rule is about headcount being missing or stale, and NULL is the purest case
  -- of missing. Requiring a known value excluded exactly those profiles.
  WHERE (c.employees IS NULL OR c.employees <= employee_ceiling)
    AND c.total_funding_usd >= high_funding_usd
    AND c.growth_stage != 'Mature'
    AND IFNULL(c.last_round_type, '') != 'ACQUISITION'
    AND c.last_funding_year >= recent_funding_year
    AND c.launch_year >= min_launch_year
),

-- 7. VC-backed companies with no founder recorded.
--    A funded company with no founder is a materially incomplete profile.
vc_no_founder AS (
  SELECT
    'vc_no_founder', 'warning',
    c.company_id, c.company_name, c.company_url, c.hq_country,
    CAST(NULL AS STRING), CAST(NULL AS STRING), CAST(NULL AS STRING), c.total_funding_usd,
    IFNULL(c.total_funding_usd, 0),
    'VC-backed but no founder recorded.',
    c.last_funding_year
  FROM companies c
  LEFT JOIN entity_founders f ON f.entity_id = CAST(c.company_id AS INT64)
  WHERE c.is_vc_backed AND f.entity_id IS NULL
),

-- 8. VC-backed companies with neither a website nor a LinkedIn page.
--    With no web presence at all the profile can barely be identified, let
--    alone enriched — a stronger signal than either field missing on its own.
vc_no_web_presence AS (
  SELECT
    'vc_no_web_presence', 'serious',
    c.company_id, c.company_name, c.company_url, c.hq_country,
    CAST(NULL AS STRING), CAST(NULL AS STRING), CAST(NULL AS STRING), c.total_funding_usd,
    IFNULL(c.total_funding_usd, 0),
    'VC-backed but has neither a website nor a LinkedIn page.',
    c.last_funding_year
  FROM companies c
  WHERE c.is_vc_backed
    AND c.website IS NULL AND c.linkedin IS NULL
    AND c.is_tech
    AND c.growth_stage != 'Mature'
    AND c.launch_year >= min_launch_year
),

-- 9. VC firms with nobody in key people.
vc_investor_no_people AS (
  SELECT
    'vc_investor_no_people', 'warning',
    c.company_id, c.company_name, c.company_url, c.hq_country,
    CAST(NULL AS STRING), CAST(NULL AS STRING), CAST(NULL AS STRING), c.total_funding_usd,
    IFNULL(c.total_funding_usd, 0),
    'VC firm with no key people recorded.',
    c.last_funding_year
  FROM companies c
  JOIN investor_entities ie USING (company_id)
  LEFT JOIN entity_people p ON p.entity_id = CAST(c.company_id AS INT64)
  WHERE p.entity_id IS NULL
),

-- 10. VC-backed companies with neither a tagline nor a description.
--     Scoped to VC-backed deliberately: unscoped this is 1.25M profiles, which
--     is a backlog rather than a worklist.
vc_no_description AS (
  SELECT
    'vc_no_description', 'warning',
    c.company_id, c.company_name, c.company_url, c.hq_country,
    CAST(NULL AS STRING), CAST(NULL AS STRING), CAST(NULL AS STRING), c.total_funding_usd,
    IFNULL(c.total_funding_usd, 0),
    'VC-backed but has neither a tagline nor a description.',
    c.last_funding_year
  FROM companies c
  WHERE c.is_vc_backed
    AND c.tagline IS NULL AND c.about IS NULL
    AND c.is_tech
    AND c.growth_stage != 'Mature'
    AND c.launch_year >= min_launch_year
)

SELECT
  CURRENT_TIMESTAMP() AS run_at,
  *
FROM (
  SELECT * FROM big_unverified
  UNION ALL SELECT * FROM missing_round_type
  UNION ALL SELECT * FROM missing_location
  UNION ALL SELECT * FROM high_funding_few_employees
  UNION ALL SELECT * FROM vc_no_founder
  UNION ALL SELECT * FROM vc_no_web_presence
  UNION ALL SELECT * FROM vc_investor_no_people
  UNION ALL SELECT * FROM vc_no_description
);
-- No ORDER BY here: a clustered CTAS cannot be ordered, and clustering by
-- rule_id is what makes the per-rule reads cheap. Ranking by impact happens in
-- 20_summary.sql and in the Apps Script export, which is where it matters.
