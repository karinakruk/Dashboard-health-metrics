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
 *  7. Run sql/30_rebuild_procedure.sql ONCE in the BigQuery console to create
 *     the data_health.rebuild() procedure.
 *  8. Triggers → add a daily time-based trigger for `dailyDataHealthRun`
 *     (NOT refreshFundingHealth — the combined runner recomputes the checks
 *     first, so the Sheet is never refreshed from stale tables).
 *  9. The dashboard tab reads the tabs by name, so there is nothing to copy.
 *
 * COST: this reads the small data_health.* result tables, not the raw
 * warehouse, so each refresh scans kilobytes. The expensive step is
 * sql/10_issues.sql, which runs once per refresh as a BigQuery scheduled query.
 */

var PROJECT_ID = 'omega-dahlia-347111';

// The summary tab APPENDS one dated row per rule per run, so the trend builds
// up on its own. The queue tab is REPLACED each run — it is a worklist, not history.
var SHEET_SUMMARY = 'funding_health_summary';

// Only the per-check summary is exported. The dashboard reports counts and their
// trend; the records themselves are worked in the Dealroom app, which each check
// links to directly — so shipping a truncated copy of them into Sheets added a
// second, staler source of truth for no gain.

var SUMMARY_SQL =
  "SELECT " +
  "  FORMAT_DATE('%Y-%m-%d', CURRENT_DATE()) AS run_date, " +
  "  rule_id, severity, issue_count, companies_affected, impact_usd_total, " +
  "  IFNULL(CAST(no_longer_flagged AS STRING), '') AS no_longer_flagged, " +
  "  IFNULL(CAST(newly_flagged AS STRING), '') AS newly_flagged, " +
  "  IFNULL(CAST(persisting AS STRING), '') AS persisting " +
  "FROM data_health.summary " +
  "ORDER BY issue_count DESC";

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

/**
 * STAGE A — recompute the checks in BigQuery.
 *
 * Calls the data_health.rebuild() stored procedure, created by running
 * sql/30_rebuild_procedure.sql once in the BigQuery console. The SQL lives in
 * BigQuery, not in this file, so there is only one definition of the rules.
 */
function rebuildDataHealthTables() {
  runStatement('CALL data_health.rebuild()');
  Logger.log('BigQuery tables rebuilt.');
}

/**
 * DAILY ENTRY POINT — point the trigger at this, not at refreshFundingHealth.
 *
 * Recomputes the checks, then copies the results into the Sheet. Doing both in
 * one function guarantees the order: the Sheet can never be refreshed from
 * tables that a separate schedule has not rebuilt yet.
 */
function dailyDataHealthRun() {
  rebuildDataHealthTables();
  refreshFundingHealth();
}

/**
 * Execute a statement that returns no rows (DDL, DML, CALL).
 *
 * Kept separate from runQuery because scripts and DDL come back without a
 * schema, which the row-reading path would choke on.
 */
function runStatement(sql) {
  var job = BigQuery.Jobs.query(
    { query: sql, useLegacySql: false, timeoutMs: 300000 }, PROJECT_ID);
  var jobId = job.jobReference.jobId;
  var waited = 0;
  while (!job.jobComplete) {
    Utilities.sleep(3000);
    waited += 3000;
    if (waited > 540000) throw new Error('BigQuery job ' + jobId + ' timed out');
    job = BigQuery.Jobs.getQueryResults(PROJECT_ID, jobId);
  }
  if (job.errors && job.errors.length) {
    throw new Error('BigQuery error: ' + JSON.stringify(job.errors[0]));
  }
  return jobId;
}

/** Copies the current BigQuery results into the Sheet (stage B). */
function refreshFundingHealth() {
  var summary = runQuery(SUMMARY_SQL);
  appendRows(SHEET_SUMMARY, summary);   // history: one dated row per check per run
  Logger.log('Data health refreshed: %s summary rows', summary.rows.length);
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
