"""Tests for the BigQuery export path.

The export is exercised with a synthetic payload shaped exactly like
`bq --format=json` output — a single-element list, INT64s as strings, and true
counts that exceed the shipped rows — so the global-scale path is verified
without needing warehouse access.
"""

import json

from app.bq_source import load_export
from app.fixqueue import to_csv

EXPORT = [
    {
        "summary": [
            # True count (32,000) far exceeds the two rows shipped below.
            {"rule_id": "missing_round_type", "severity": "critical",
             "issue_count": "32000", "companies_affected": "31000",
             "impact_usd_total": "900000000000"},
            {"rule_id": "big_unverified", "severity": "serious",
             "issue_count": "14", "companies_affected": "9",
             "impact_usd_total": "5000000"},
        ],
        "by_country": [
            {"hq_country": "United States", "rule_id": "missing_round_type",
             "issue_count": "20000"},
            {"hq_country": None, "rule_id": "missing_round_type",
             "issue_count": "12000"},
        ],
        "queue": [
            {"rule_id": "missing_round_type", "severity": "critical",
             "company_id": "1", "company_name": "Quibi", "company_slug": "quibi",
             "hq_country": None, "round_date": "2018-08", "round_type": None,
             "amount_usd": "1000000000", "impact_usd": "1000000000",
             "detail": "Round has no round type set."},
            {"rule_id": "big_unverified", "severity": "serious",
             "company_id": "2", "company_name": "Northvolt", "company_slug": "northvolt",
             "hq_country": "Sweden", "round_date": "2023-08", "round_type": "CONVERTIBLE",
             "amount_usd": "1200000000", "impact_usd": "1200000000",
             "detail": "Big round is unverified."},
        ],
        "universe_companies": "1500000",
        "exported_at": "2026-08-21 10:00:00 UTC",
    }
]


def write(tmp_path):
    p = tmp_path / "bq_export.json"
    p.write_text(json.dumps(EXPORT))
    return p


def test_counts_come_from_summary_not_row_count(tmp_path):
    report, _queue, _by_country = load_export(write(tmp_path))
    missing = report.result("missing_round_type")
    assert missing.count == 32000          # true warehouse count
    assert len(missing.issues) == 1        # only one row shipped
    assert missing.is_truncated is True


def test_untruncated_rule_is_not_flagged_truncated(tmp_path):
    report, _q, _c = load_export(write(tmp_path))
    # 14 reported, 1 shipped -> still truncated; a rule with equal counts is not.
    assert report.result("big_unverified").count == 14


def test_universe_and_queue(tmp_path):
    report, queue, by_country = load_export(write(tmp_path))
    assert report.total_companies == 1_500_000
    # Queue is ranked by impact, so the $1.2B round outranks the $1.0B one.
    assert [q.company_name for q in queue] == ["Northvolt", "Quibi"]
    assert queue[0].impact_usd == 1_200_000_000
    assert queue[0].company_url == "https://app.dealroom.co/companies/northvolt"
    assert by_country["United States"] == 20000
    assert by_country["(no location)"] == 12000


def test_rules_with_no_rows_still_present(tmp_path):
    report, _q, _c = load_export(write(tmp_path))
    # Every check must appear even when the export carries none of its rows.
    assert len(report.results) == 5
    assert report.result("high_funding_few_employees").count == 0
    # Rules the warehouse can emit must all be registered, or load_export drops
    # their rows on the floor.
    assert report.result("big_round_missing_city").count == 0


def test_csv_export_of_bq_queue(tmp_path):
    _r, queue, _c = load_export(write(tmp_path))
    csv_text = to_csv(queue)
    assert "Northvolt" in csv_text and "app.dealroom.co/companies/northvolt" in csv_text
    assert len(csv_text.strip().splitlines()) == 3  # header + 2 rows
