/**
 * Data health — funding domain: BigQuery → Google Sheet.
 *
 * Runs the materialized check results out of BigQuery and writes them into the
 * data-health sheet, which Profile-edit-monitor reads as published CSV. This is
 * the whole pipeline — no server, no build step, no credentials in the client.
 *
 *   sql/10_issues.sql  ─┐
 *   sql/20_summary.sql ─┴→ data_health.*  →  [this script, daily]  →  Sheet
 *                                                                      ↓
 *                                        Profile-edit-monitor fetches CSV
 *
 * SETUP
 *  1. Open the Data Health Sheet:
 *       https://docs.google.com/spreadsheets/d/1geXbBHZO4HXuoJbO8CkwBJlz5nbUzFaMqQEDBu_-MEM
 *     → Extensions → Apps Script; paste this file.
 *     (Dedicated sheet: the main 'Edits tracking' sheet is read-only
 *      for the data team, so the script cannot live there.)
 *  2. Services → add "BigQuery API" (identifier: BigQuery).
 *  3. Set PROJECT_ID below.
 *  4. Run `testBigQueryConnection` and grant the OAuth prompt. This proves
 *     the Sheet↔BigQuery link works before any of the real SQL exists.
 *  5. Once the scheduled query has created the result tables, confirm with
 *     `checkResultTablesExist`.
 *  6. Run `refreshFundingHealth` once — it creates and fills both tabs.
 *  7. Triggers → add a daily time-based trigger for `refreshFundingHealth`.
 *  8. Note each created tab's gid from the URL and put them in
 *     src/FundingDataHealth.tsx (until then the tab reads local dev data).
 *
 * COST: this reads the small data_health.* result tables, not the raw
 * warehouse, so each refresh scans kilobytes. The expensive step is
 * sql/10_issues.sql, which runs once per refresh as a BigQuery scheduled query.
 */

var PROJECT_ID = 'omega-dahlia-347111';

// The summary tab APPENDS one dated row per rule per run, so the trend builds
// up on its own. The queue tab is REPLACED each run — it is a worklist, not history.
var SHEET_SUMMARY = 'funding_health_summary';
var SHEET_QUEUE = 'funding_health_queue';

// Rows per rule kept in the fix queue. The full set stays in BigQuery; Sheets
// is a transport for the actionable slice, not a mirror of the warehouse.
var QUEUE_LIMIT_PER_RULE = 300;

var SUMMARY_SQL =
  "SELECT " +
  "  FORMAT_DATE('%Y-%m-%d', CURRENT_DATE()) AS run_date, " +
  "  rule_id, severity, issue_count, companies_affected, impact_usd_total " +
  "FROM data_health.summary " +
  "ORDER BY issue_count DESC";

var QUEUE_SQL =
  "WITH ranked AS ( " +
  "  SELECT *, ROW_NUMBER() OVER ( " +
  "      PARTITION BY rule_id ORDER BY impact_usd DESC NULLS LAST) AS rn " +
  "  FROM data_health.issues) " +
  "SELECT " +
  "  FORMAT_DATE('%Y-%m-%d', CURRENT_DATE()) AS run_date, " +
  "  rule_id, severity, company_name, company_url, " +
  "  IFNULL(hq_country, '') AS hq_country, " +
  "  IFNULL(round_date, '') AS round_date, " +
  "  IFNULL(round_type, '') AS round_type, " +
  "  IFNULL(amount_usd, 0) AS amount_usd, " +
  "  IFNULL(impact_usd, 0) AS impact_usd, " +
  "  detail " +
  "FROM ranked WHERE rn <= " + QUEUE_LIMIT_PER_RULE + " " +
  "ORDER BY impact_usd DESC";

/**
 * SMOKE TEST — run this FIRST, before anything else works.
 *
 * Verifies only one thing: that this Sheet can reach BigQuery and write the
 * answer back. It queries a literal, so it passes even before sql/10_issues.sql
 * has ever run and regardless of what the real tables are called. That keeps
 * "is the connection set up?" separate from "is the SQL right?".
 *
 * Expect a `_bq_smoke_test` tab containing one row. Delete the tab afterwards.
 */
function testBigQueryConnection() {
  var probe = runQuery(
    "SELECT 'connected' AS status, " +
    "CURRENT_TIMESTAMP() AS server_time, " +
    "SESSION_USER() AS running_as"
  );
  replaceRows('_bq_smoke_test', probe);
  Logger.log('BigQuery reachable. %s', probe.rows[0].join(' | '));
  return probe.rows[0];
}

/**
 * Reports whether the tables this script depends on exist yet, without
 * touching the raw warehouse. Run it after the scheduled query has been set up.
 */
function checkResultTablesExist() {
  var found = runQuery(
    "SELECT table_name FROM data_health.INFORMATION_SCHEMA.TABLES " +
    "WHERE table_name IN ('issues','summary') ORDER BY table_name"
  );
  var names = found.rows.map(function (r) { return r[0]; });
  Logger.log(names.length === 2
    ? 'Ready: data_health.issues and data_health.summary both exist.'
    : 'Not ready yet — found: [' + names.join(', ') + ']. Run sql/10_issues.sql and sql/20_summary.sql first.');
  return names;
}

/** Entry point — point the daily trigger at this. */
function refreshFundingHealth() {
  var summary = runQuery(SUMMARY_SQL);
  var queue = runQuery(QUEUE_SQL);

  appendRows(SHEET_SUMMARY, summary);   // history: keeps every run
  replaceRows(SHEET_QUEUE, queue);      // worklist: current run only

  Logger.log('Funding health refreshed: %s summary rows, %s queue rows',
             summary.rows.length, queue.rows.length);
}

/** Run a query and return {headers: [...], rows: [[...]]}, following pagination. */
function runQuery(sql) {
  var request = { query: sql, useLegacySql: false, timeoutMs: 120000 };
  var result = BigQuery.Jobs.query(request, PROJECT_ID);
  var jobId = result.jobReference.jobId;

  // The first response may return before the job completes.
  while (!result.jobComplete) {
    Utilities.sleep(2000);
    result = BigQuery.Jobs.getQueryResults(PROJECT_ID, jobId);
  }

  var headers = result.schema.fields.map(function (f) { return f.name; });
  var rows = [];
  while (true) {
    (result.rows || []).forEach(function (r) {
      rows.push(r.f.map(function (cell) { return cell.v === null ? '' : cell.v; }));
    });
    if (!result.pageToken) break;
    result = BigQuery.Jobs.getQueryResults(PROJECT_ID, jobId, { pageToken: result.pageToken });
  }
  return { headers: headers, rows: rows };
}

/** Append rows, writing the header only when the tab is new. */
function appendRows(name, data) {
  var sheet = getOrCreateSheet(name);
  if (sheet.getLastRow() === 0) {
    sheet.appendRow(data.headers);
  }
  if (!data.rows.length) return;
  sheet.getRange(sheet.getLastRow() + 1, 1, data.rows.length, data.headers.length)
       .setValues(data.rows);
}

/** Replace the tab's contents wholesale. */
function replaceRows(name, data) {
  var sheet = getOrCreateSheet(name);
  sheet.clear();
  sheet.appendRow(data.headers);
  if (!data.rows.length) return;
  sheet.getRange(2, 1, data.rows.length, data.headers.length).setValues(data.rows);
}

function getOrCreateSheet(name) {
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  return ss.getSheetByName(name) || ss.insertSheet(name);
}
