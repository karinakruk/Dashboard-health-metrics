-- ============================================================================
-- GENERATED FILE — do not edit by hand.
--   Source, in run order:
--     10_issues.sql   rebuild the current issue set
--     15_history.sql  append it to issue_history (before anything overwrites it)
--     25_movement.sql diff the two newest runs -> fixed / new / persisting
--     20_summary.sql  per-rule counts, joined to that movement
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
  DECLARE high_funding_usd        INT64   DEFAULT 100000000;  -- matches total_funding_min in the app link
  DECLARE min_launch_year         INT64   DEFAULT 1990;
  DECLARE rolling_window_months   INT64   DEFAULT 24;
  DECLARE recent_funding_year     INT64   DEFAULT EXTRACT(YEAR FROM DATE_SUB(CURRENT_DATE(), INTERVAL rolling_window_months MONTH));
  DECLARE employee_ceiling        INT64   DEFAULT 10;  -- inclusive, matching the app's {1, 2-10} buckets

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

  -- "Recently funded" is a ROLLING window, not a fixed year. With a hard 2025
  -- gate, companies age out of the window as time passes and the movement report
  -- would count them as fixed when nothing was fixed — a progress metric that
  -- flatters itself. Rolling keeps "fixed" honest.
  -- NOTE: the app links in the dashboard hardcode a year, so when this window
  -- rolls past a year boundary those links need the same bump.


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
  -- Which entities are investors. investors.bobject_investor_id keys back to
  -- entities.id.
  investor_entities AS (
    SELECT DISTINCT CAST(bobject_investor_id AS STRING) AS company_id
    FROM dealroom_intelligence.investors
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
  --    Verified at 115 companies (the app reports 137 — see README).
  high_funding_few_employees AS (
    SELECT
      'high_funding_few_employees', 'serious',
      c.company_id, c.company_name, c.company_url, c.hq_country,
      CAST(NULL AS STRING), CAST(NULL AS STRING), CAST(NULL AS STRING), c.total_funding_usd,
      c.total_funding_usd,
      CONCAT(CAST(c.employees AS STRING), ' employees but ',
             FORMAT('%.1f', c.total_funding_usd / 1e9), 'B total funding — headcount likely missing or stale.'),
      c.last_funding_year
    FROM companies c
    WHERE c.employees IS NOT NULL
      AND c.employees <= employee_ceiling
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
  ),

  -- 9. Investors with nobody in key people.
  investor_no_people AS (
    SELECT
      'investor_no_people', 'warning',
      c.company_id, c.company_name, c.company_url, c.hq_country,
      CAST(NULL AS STRING), CAST(NULL AS STRING), CAST(NULL AS STRING), c.total_funding_usd,
      IFNULL(c.total_funding_usd, 0),
      'Investor with no key people recorded.',
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
    UNION ALL SELECT * FROM investor_no_people
    UNION ALL SELECT * FROM vc_no_description
  );
  -- No ORDER BY here: a clustered CTAS cannot be ordered, and clustering by
  -- rule_id is what makes the per-rule reads cheap. Ranking by impact happens in
  -- 20_summary.sql and in the Apps Script export, which is where it matters.

    -- ── from 15_history.sql ──
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

    -- ── from 25_movement.sql ──
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

    -- ── from 20_summary.sql ──
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
    ANY_VALUE(m.persisting)            AS persisting
  FROM data_health.issues i
  LEFT JOIN data_health.movement m USING (rule_id)
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
END;
