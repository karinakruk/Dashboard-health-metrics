-- ============================================================================
-- Run this FIRST, before 10_issues.sql.
--
-- Part A lists candidate tables and their columns so the real names can be
-- discovered. Part B asserts that every column 10_issues.sql assumes actually
-- exists — so a rename fails loudly here instead of silently skewing a metric.
-- Costs nothing: INFORMATION_SCHEMA queries are free.
-- ============================================================================

-- Part A — discovery: which tables look like companies / funding rounds?
SELECT table_schema, table_name, column_name, data_type
FROM `region-eu`.INFORMATION_SCHEMA.COLUMNS
WHERE LOWER(table_name) LIKE '%compan%'
   OR LOWER(table_name) LIKE '%funding%'
   OR LOWER(table_name) LIKE '%round%'
   OR LOWER(table_name) LIKE '%transaction%'
ORDER BY table_schema, table_name, ordinal_position;

-- Part B — assertion: every assumed column must resolve. Anything listed in
-- the output below is MISSING and must be remapped in 10_issues.sql.
WITH assumed AS (
  SELECT * FROM UNNEST([
    STRUCT('companies'      AS tbl, 'id'                AS col),
    ('companies', 'name'), ('companies', 'path'),
    ('companies', 'hq_country'), ('companies', 'employees_latest'),
    ('companies', 'total_funding_usd'), ('companies', 'last_valuation_usd'),
    ('funding_rounds', 'id'), ('funding_rounds', 'company_id'),
    ('funding_rounds', 'round_date'), ('funding_rounds', 'round_type'),
    ('funding_rounds', 'amount_usd'), ('funding_rounds', 'valuation_usd'),
    ('funding_rounds', 'is_verified')
  ])
)
SELECT a.tbl AS missing_from_table, a.col AS missing_column
FROM assumed a
LEFT JOIN `dealroom_intelligence`.INFORMATION_SCHEMA.COLUMNS c
  ON c.table_name = a.tbl AND c.column_name = a.col
WHERE c.column_name IS NULL
ORDER BY 1, 2;
