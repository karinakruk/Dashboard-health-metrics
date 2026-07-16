"""Unit tests for the six data-health checks, using small hand-built fixtures.

These are deliberately independent of the shipped snapshot so the rules are
pinned by construction, not by whatever the live data happens to contain.
"""

from app.checks import (
    check_big_round_no_location,
    check_big_unverified,
    check_high_funding_few_employees,
    check_late_without_early,
    check_missing_round_type,
    check_sequence_out_of_order,
    run_checks,
    stage_tier,
    TIER_EARLY,
    TIER_LATE,
)
from app.models import Company, Round, load_snapshot


def company(**kw) -> Company:
    defaults = dict(
        id="x",
        name="X",
        hq_location="Berlin, Germany",
        country="Germany",
        employees=100,
        total_funding_usd=0,
        latest_valuation_usd=None,
        status="operational",
        growth_stage="late stage",
        industry="fintech",
        dealroom_url="",
        rounds=[],
    )
    defaults.update(kw)
    return Company(**defaults)


def rnd(date, rtype, amount, valuation=None, lead=False) -> Round:
    return Round(date=date, round_type=rtype, amount_usd=amount,
                 valuation_usd=valuation, has_lead=lead)


# --- stage classification --------------------------------------------------- #

def test_stage_tiers():
    assert stage_tier(rnd("2020-01", "SEED", 0)) == TIER_EARLY
    assert stage_tier(rnd("2020-01", "SERIES A", 0)) == TIER_EARLY
    assert stage_tier(rnd("2020-01", "LATE VC", 0)) == TIER_LATE
    assert stage_tier(rnd("2020-01", "DEBT", 0)) is None  # not a stage
    assert stage_tier(rnd("2020-01", None, 0)) is None


# --- check 1: big rounds not verified --------------------------------------- #

def test_big_unverified_flags_big_round_without_lead_or_valuation():
    c = company(rounds=[rnd("2021-01", "SERIES B", 50_000_000)])
    assert [i.company_id for i in check_big_unverified([c])] == ["x"]


def test_big_unverified_passes_when_valuation_present():
    c = company(rounds=[rnd("2021-01", "SERIES B", 50_000_000, valuation=200_000_000)])
    assert list(check_big_unverified([c])) == []


def test_big_unverified_ignores_small_rounds_and_debt():
    c = company(rounds=[
        rnd("2021-01", "SEED", 2_000_000),          # below $10M threshold
        rnd("2021-02", "DEBT", 500_000_000),         # non-priced instrument
    ])
    assert list(check_big_unverified([c])) == []


# --- check 2: missing round type -------------------------------------------- #

def test_missing_round_type_flags_null_and_not_set():
    c = company(rounds=[
        rnd("2020-01", None, 10_000_000),
        rnd("2020-02", "NOT SET", 10_000_000),
        rnd("2020-03", "SERIES A", 10_000_000),
    ])
    assert len(list(check_missing_round_type([c]))) == 2


# --- check 3: sequence out of order ----------------------------------------- #

def test_sequence_out_of_order_flags_regression():
    c = company(rounds=[
        rnd("2020-01", "SERIES C", 40_000_000),      # mid/late
        rnd("2021-01", "SERIES A", 10_000_000),      # earlier stage, later date
    ])
    issues = list(check_sequence_out_of_order([c]))
    assert len(issues) == 1 and issues[0].round_type == "SERIES A"


def test_sequence_in_order_is_clean():
    c = company(rounds=[
        rnd("2019-01", "SEED", 1_000_000),
        rnd("2020-01", "SERIES A", 10_000_000),
        rnd("2021-01", "SERIES B", 30_000_000),
    ])
    assert list(check_sequence_out_of_order([c])) == []


# --- check 4: late stage with no early stage -------------------------------- #

def test_late_without_early_flags_company():
    c = company(rounds=[rnd("2020-01", "LATE VC", 100_000_000, valuation=1)])
    assert [i.company_id for i in check_late_without_early([c])] == ["x"]


def test_late_with_early_is_clean():
    c = company(rounds=[
        rnd("2018-01", "SEED", 1_000_000),
        rnd("2020-01", "LATE VC", 100_000_000, valuation=1),
    ])
    assert list(check_late_without_early([c])) == []


# --- check 5: big round, no location ---------------------------------------- #

def test_big_round_no_location_flags_when_location_missing():
    c = company(hq_location=None, rounds=[rnd("2020-01", "SERIES B", 50_000_000, lead=True)])
    assert [i.company_id for i in check_big_round_no_location([c])] == ["x"]


def test_big_round_with_location_is_clean():
    c = company(hq_location="Paris, France", rounds=[rnd("2020-01", "SERIES B", 50_000_000)])
    assert list(check_big_round_no_location([c])) == []


# --- check 6: high funding, few employees ----------------------------------- #

def test_high_funding_few_employees_flags():
    c = company(employees=5, total_funding_usd=500_000_000)
    assert [i.company_id for i in check_high_funding_few_employees([c])] == ["x"]


def test_high_funding_many_employees_is_clean():
    c = company(employees=5000, total_funding_usd=500_000_000)
    assert list(check_high_funding_few_employees([c])) == []


def test_unknown_employees_not_flagged():
    c = company(employees=None, total_funding_usd=500_000_000)
    assert list(check_high_funding_few_employees([c])) == []


# --- end to end over the shipped snapshot ----------------------------------- #

def test_snapshot_runs_and_every_check_fires():
    report = run_checks(load_snapshot().companies)
    assert report.total_companies == 9
    assert report.total_issues > 0
    # The curated real snapshot is built so every check has at least one hit.
    assert all(r.count > 0 for r in report.results)
    assert 0 <= report.health_score <= 100
