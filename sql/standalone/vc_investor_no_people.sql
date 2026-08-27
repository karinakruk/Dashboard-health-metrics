-- ============================================================================
-- VC firms with nobody recorded in key people.
--
-- Standalone: paste into the BigQuery console and run. No app filter exists for
-- this.
--
-- Scoped to VENTURE CAPITAL firms. The investors table is dominated by
-- 'corporate' (98,142 of 215,689) — operating companies that happen to have
-- made an investment — so an unscoped version returns mostly companies, for
-- which "no key people" is not a meaningful finding.
--
-- "Key people" means any row in people_organizations for that entity: past or
-- present, any title.
-- ============================================================================

WITH entity_people AS (
  SELECT DISTINCT entity_id
  FROM dealroom_intelligence.people_organizations
),
investor_entities AS (
  SELECT DISTINCT i.bobject_investor_id AS entity_id
  FROM dealroom_intelligence.investors i
  WHERE 'venture capital' IN UNNEST(i.investor_types)
)
SELECT
  e.name                                   AS investor,
  e.dealroom_url                           AS profile,
  ROUND(e.total_funding_usd / 1e6, 1)      AS total_funding_musd,
  (SELECT l.country FROM UNNEST(e.locations) l
    WHERE l.flg_is_hq AND l.country IS NOT NULL LIMIT 1) AS hq_country,
  e.launch_year
FROM dealroom_intelligence.entities e
JOIN investor_entities i ON i.entity_id = e.id
LEFT JOIN entity_people p ON p.entity_id = e.id
WHERE e.entity_type = 'organization'
  AND p.entity_id IS NULL          -- no person attached at all
ORDER BY e.total_funding_usd DESC NULLS LAST;
