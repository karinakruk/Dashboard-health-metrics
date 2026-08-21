/**
 * Funding data-health: BigQuery → Google Sheet.
 *
 * Runs the materialized check results out of BigQuery and writes them into the
 * dashboard sheet, which Profile-edit-monitor reads as published CSV. This is
 * the whole pipeline — no server, no build step, no credentials in the client.
 *
 *   sql/10_issues.sql  ─┐
 *   sql/20_summary.sql ─┴→ data_health.*  →  [this script, daily]  →  Sheet
 *                                                                      ↓
 *                                        Profile-edit-monitor fetches CSV
 *
 * SETUP
 *  1. Open the dashboard Sheet → Extensions → Apps Script; paste this file.
 *  2. Services → add "BigQuery API" (identifier: BigQuery).
 *  3. Set PROJECT_ID below.
 *  4. Run `refreshFundingHealth` once and grant the OAuth prompt.
 *  5. Triggers → add a daily time-based trigger for `refreshFundingHealth`.
 *  6. Note each created tab's gid from the URL and put them in
 *     src/FundingDataHealth.tsx.
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
  "  rule_id, severity, company_name, company_slug, " +
  "  IFNULL(hq_country, '') AS hq_country, " +
  "  IFNULL(round_date, '') AS round_date, " +
  "  IFNULL(round_type, '') AS round_type, " +
  "  IFNULL(amount_usd, 0) AS amount_usd, " +
  "  IFNULL(impact_usd, 0) AS impact_usd, " +
  "  detail " +
  "FROM ranked WHERE rn <= " + QUEUE_LIMIT_PER_RULE + " " +
  "ORDER BY impact_usd DESC";

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
