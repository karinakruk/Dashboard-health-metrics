-- ============================================================================
-- VC-backed companies with neither a website nor a LinkedIn page.
--
-- Standalone: paste into the BigQuery console and run. The app can filter a
-- missing website (has_website_url) but not a missing LinkedIn, so it cannot
-- express "both missing" — hence this query.
--
-- Scope: VC-backed, founded 1990 or later, not Outside Tech, not mature.
--
-- Both-missing is the deliberate definition. For reference, either-missing is a
-- much weaker signal: 4,611 lack a website and 56,301 lack LinkedIn, since
-- plenty of real companies have one but not the other.
-- ============================================================================

SELECT
  e.name                                   AS company,
  e.dealroom_url                           AS profile,
  ROUND(e.total_funding_usd / 1e6, 1)      AS total_funding_musd,
  (SELECT l.country FROM UNNEST(e.locations) l
    WHERE l.flg_is_hq AND l.country IS NOT NULL LIMIT 1) AS hq_country,
  e.twitter,                                -- any remaining trace to work from
  e.launch_year
FROM dealroom_intelligence.entities e
WHERE e.entity_type = 'organization'
  AND e.flg_is_vcbacked
  AND e.website IS NULL
  AND e.linkedin IS NULL
  AND e.launch_year >= 1990
  -- "Outside Tech" is a SECTOR (dim_tags id 11028), sitting in entities.sectors
  -- alongside Hard Tech, Climate Tech and the rest.
  AND NOT EXISTS(SELECT 1 FROM UNNEST(e.sectors) sec WHERE sec.name = 'Outside Tech')
  AND IFNULL(e.growth_stage_desc, '') != 'Mature'
ORDER BY e.total_funding_usd DESC NULLS LAST;
