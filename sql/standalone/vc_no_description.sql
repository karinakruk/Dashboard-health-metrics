-- ============================================================================
-- VC-backed companies with neither a tagline nor a description.
--
-- Standalone: paste into the BigQuery console and run. There is no equivalent
-- filter in the Dealroom app, which is why this query exists.
--
-- Scoped to VC-backed on purpose. Without that scope it returns 1,253,339
-- profiles — a backlog rather than a worklist.
-- ============================================================================

SELECT
  e.name                                   AS company,
  e.dealroom_url                           AS profile,
  ROUND(e.total_funding_usd / 1e6, 1)      AS total_funding_musd,
  (SELECT l.country FROM UNNEST(e.locations) l
    WHERE l.flg_is_hq AND l.country IS NOT NULL LIMIT 1) AS hq_country,
  e.growth_stage_desc                      AS growth_stage,
  e.launch_year
FROM dealroom_intelligence.entities e
WHERE e.entity_type = 'organization'
  AND e.flg_is_vcbacked
  AND e.tagline IS NULL
  AND e.about IS NULL
-- Most-funded first: the same value-at-stake ordering the dashboard uses.
ORDER BY e.total_funding_usd DESC NULLS LAST;
