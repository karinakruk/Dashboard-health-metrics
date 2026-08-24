"""Unit tests for the six data-health checks, using small hand-built fixtures.

These are deliberately independent of the shipped snapshot so the rules are
pinned by construction, not by whatever the live data happens to contain.
"""

from app.checks import (
    check_missing_location,
    check_big_unverified,
    check_high_funding_few_employees,
    check_missing_round_type,
    run_checks,
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


def rnd(date, rtype, amount, valuation=None, lead=False, verified=None) -> Round:
    return Round(date=date, round_type=rtype, amount_usd=amount,
                 valuation_usd=valuation, has_lead=lead, verified=verified)


# --- check 1: big rounds not verified --------------------------------------- #

def test_big_unverified_flags_explicitly_unverified_round():
    c = company(rounds=[rnd("2021-01", "SERIES B", 50_000_000, verified=False)])
    assert [i.company_id for i in check_big_unverified([c])] == ["x"]


def test_big_unverified_passes_when_verified():
    c = company(rounds=[rnd("2021-01", "SERIES B", 50_000_000, verified=True)])
    assert list(check_big_unverified([c])) == []


def test_unknown_verification_is_not_treated_as_unverified():
    """None means "we don't know", which must never be reported as a problem."""
    c = company(rounds=[rnd("2021-01", "SERIES B", 50_000_000, verified=None)])
    assert list(check_big_unverified([c])) == []


def test_big_unverified_ignores_small_rounds():
    c = company(rounds=[rnd("2021-01", "SEED", 2_000_000, verified=False)])
    assert list(check_big_unverified([c])) == []


def test_big_unverified_has_no_instrument_carve_out():
    """An unverified $500M debt round is still an unverified big round."""
    c = company(rounds=[
        rnd("2021-02", "DEBT", 500_000_000, verified=False),
        rnd("2021-03", "ACQUISITION", 15_800_000_000, verified=False),
    ])
    assert len(list(check_big_unverified([c]))) == 2


def test_lead_and_valuation_no_longer_affect_verification():
    """Verification is read, not inferred: a lead investor doesn't verify a round."""
    c = company(rounds=[
        rnd("2021-01", "SERIES B", 50_000_000, valuation=9e9, lead=True, verified=False),
    ])
    assert len(list(check_big_unverified([c]))) == 1


# --- check 2: missing round type -------------------------------------------- #

def test_missing_round_type_flags_null_and_not_set():
    c = company(rounds=[
        rnd("2020-01", None, 10_000_000),
        rnd("2020-02", "NOT SET", 10_000_000),
        rnd("2020-03", "SERIES A", 10_000_000),
    ])
    assert len(list(check_missing_round_type([c]))) == 2


# --- check 5: big round, no location ---------------------------------------- #

def test_big_round_no_location_flags_when_location_missing():
    c = company(hq_location=None, rounds=[rnd("2020-01", "SERIES B", 50_000_000, lead=True)])
    assert [i.company_id for i in check_missing_location([c])] == ["x"]


def test_big_round_with_location_is_clean():
    c = company(hq_location="Paris, France", rounds=[rnd("2020-01", "SERIES B", 50_000_000)])
    assert list(check_missing_location([c])) == []


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
    # The curated snapshot makes every check fire, except the city split, which
    # needs structured city data the snapshot does not carry.
    silent = {"missing_city"}
    for r in report.results:
        if r.meta.id in silent:
            assert r.count == 0, f"{r.meta.id} unexpectedly fired locally"
        else:
            assert r.count > 0, f"{r.meta.id} did not fire on the snapshot"
    assert 0 <= report.health_score <= 100


# --- SQL / Python rule parity ------------------------------------------------ #

def test_registered_rules_match_the_sql():
    """Every rule the SQL can emit must be registered in ALL_CHECKS.

    load_export() builds its report by iterating ALL_CHECKS, so a rule present
    in the warehouse but missing here would be silently discarded.
    """
    import re
    from pathlib import Path
    from app.checks import ALL_CHECKS

    sql = (Path(__file__).resolve().parent.parent / "sql" / "10_issues.sql").read_text()
    # Rule ids appear as the first projected literal of each check's SELECT.
    sql_rules = set(re.findall(r"^\s*'([a-z_]+)'(?:\s+AS rule_id)?,", sql, re.M))
    registered = {meta.id for meta, _ in ALL_CHECKS}
    assert sql_rules == registered, (
        f"only in SQL: {sql_rules - registered} | only in Python: {registered - sql_rules}"
    )
