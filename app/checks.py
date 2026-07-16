"""The six funding data-health checks.

Each check is a pure function over the loaded companies and yields :class:`Issue`
records. ``ALL_CHECKS`` ties each function to its display metadata, and
``run_checks`` runs them all and rolls the results into a :class:`Report`.

The checks (as specified by the data team):

1. Big rounds (>=$10M) that are not verified.
2. Rounds without a round type (should not happen).
3. Round sequence out of order — an earlier-stage round after a later-stage one.
4. A late-stage round with no earlier early-stage round (possible duplicate
   profile, or early rounds we missed).
5. Big rounds on profiles without a location (so the amount can flow into the
   ecosystem's value).
6. Companies with high funding/valuation but fewer than 10 employees.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field

from .models import BIG_ROUND_THRESHOLD_USD, Company, Round

# --------------------------------------------------------------------------- #
# Stage ladder
# --------------------------------------------------------------------------- #
# Tiers group round types into ordered funding stages. Types that are not a
# fundraising *stage* (debt, grants, secondaries, M&A, ...) return None and are
# ignored by the sequence checks.
TIER_EARLY = 1  # pre-seed / seed / early VC / Series A
TIER_MID = 2  # Series B / Series C
TIER_LATE = 3  # Series D+ / late VC / growth equity
TIER_PUBLIC = 4  # IPO and post-IPO instruments

_STAGE_TIERS: dict[str, int] = {
    "PRE-SEED": TIER_EARLY,
    "PRE SEED": TIER_EARLY,
    "ANGEL": TIER_EARLY,
    "MICRO-SEED": TIER_EARLY,
    "SEED": TIER_EARLY,
    "SEED EXTENSION": TIER_EARLY,
    "EARLY VC": TIER_EARLY,
    "SERIES A": TIER_EARLY,
    "SERIES B": TIER_MID,
    "SERIES C": TIER_MID,
    "SERIES D": TIER_LATE,
    "SERIES E": TIER_LATE,
    "SERIES F": TIER_LATE,
    "SERIES G": TIER_LATE,
    "SERIES H": TIER_LATE,
    "LATE VC": TIER_LATE,
    "GROWTH EQUITY VC": TIER_LATE,
    "GROWTH EQUITY NON VC": TIER_LATE,
    "IPO": TIER_PUBLIC,
    "POST IPO DEBT": TIER_PUBLIC,
    "POST IPO EQUITY": TIER_PUBLIC,
    "POST IPO CONVERTIBLE": TIER_PUBLIC,
    "POST IPO SECONDARY": TIER_PUBLIC,
}


def stage_tier(round_: Round) -> int | None:
    """Return the funding-stage tier of a round, or None if it is not a stage."""
    if not round_.round_type:
        return None
    return _STAGE_TIERS.get(round_.round_type.strip().upper())


# --------------------------------------------------------------------------- #
# Issue + check metadata
# --------------------------------------------------------------------------- #

# Severity -> status-palette role (see dashboard.py).
CRITICAL = "critical"
SERIOUS = "serious"
WARNING = "warning"


@dataclass(frozen=True)
class Issue:
    check_id: str
    company_id: str
    company_name: str
    detail: str
    round_date: str | None = None
    round_type: str | None = None
    amount_usd: int | None = None


@dataclass(frozen=True)
class CheckMeta:
    id: str
    title: str
    question: str
    severity: str


# --------------------------------------------------------------------------- #
# The checks
# --------------------------------------------------------------------------- #


# Instruments that legitimately lack a lead investor / priced valuation, so a
# "no lead and no valuation" signal is not a verification gap for them.
NON_PRICED_TYPES = {
    "DEBT",
    "POST IPO DEBT",
    "POST IPO CONVERTIBLE",
    "GRANT",
    "SUPPORT PROGRAM",
    "BANKRUPTCY",
    "ACQUISITION",
    "MERGER",
}


def _is_priced(round_: Round) -> bool:
    rtype = (round_.round_type or "").strip().upper()
    return rtype not in NON_PRICED_TYPES


def check_big_unverified(companies: Iterable[Company]) -> Iterator[Issue]:
    for c in companies:
        for r in c.rounds:
            if r.is_big and _is_priced(r) and not r.is_verified:
                yield Issue(
                    check_id="big_unverified",
                    company_id=c.id,
                    company_name=c.name,
                    detail="Big round is unverified (no lead investor and no valuation).",
                    round_date=r.date,
                    round_type=r.round_type,
                    amount_usd=r.amount_usd,
                )


def check_missing_round_type(companies: Iterable[Company]) -> Iterator[Issue]:
    for c in companies:
        for r in c.rounds:
            if not r.has_type:
                yield Issue(
                    check_id="missing_round_type",
                    company_id=c.id,
                    company_name=c.name,
                    detail="Round has no round type set.",
                    round_date=r.date,
                    round_type=r.round_type or "—",
                    amount_usd=r.amount_usd,
                )


def check_sequence_out_of_order(companies: Iterable[Company]) -> Iterator[Issue]:
    for c in companies:
        highest_tier_so_far = 0
        for r in c.rounds_by_date:
            tier = stage_tier(r)
            if tier is None:
                continue
            if tier < highest_tier_so_far:
                yield Issue(
                    check_id="sequence_out_of_order",
                    company_id=c.id,
                    company_name=c.name,
                    detail=(
                        f"{r.round_type} recorded after a later-stage round "
                        "already took place."
                    ),
                    round_date=r.date,
                    round_type=r.round_type,
                    amount_usd=r.amount_usd,
                )
            highest_tier_so_far = max(highest_tier_so_far, tier)


def check_late_without_early(companies: Iterable[Company]) -> Iterator[Issue]:
    for c in companies:
        tiers = {stage_tier(r) for r in c.rounds}
        tiers.discard(None)
        has_late = any(t >= TIER_LATE for t in tiers)
        has_early = TIER_EARLY in tiers
        if has_late and not has_early:
            yield Issue(
                check_id="late_without_early",
                company_id=c.id,
                company_name=c.name,
                detail=(
                    "Has late-stage rounds but no early-stage round on record — "
                    "possible duplicate profile or missing early rounds."
                ),
            )


def check_big_round_no_location(companies: Iterable[Company]) -> Iterator[Issue]:
    for c in companies:
        if c.has_location:
            continue
        big = [r for r in c.rounds if r.is_big]
        if not big:
            continue
        largest = max(big, key=lambda r: r.amount_usd)
        yield Issue(
            check_id="big_round_no_location",
            company_id=c.id,
            company_name=c.name,
            detail=(
                f"{len(big)} big round(s) but no location set — the amount can't "
                "flow into an ecosystem's value."
            ),
            round_date=largest.date,
            round_type=largest.round_type,
            amount_usd=largest.amount_usd,
        )


# "High" thresholds for the funding-vs-headcount sanity check.
HIGH_FUNDING_USD = 25_000_000
HIGH_VALUATION_USD = 100_000_000
EMPLOYEE_FLOOR = 10


def check_high_funding_few_employees(companies: Iterable[Company]) -> Iterator[Issue]:
    for c in companies:
        if c.employees is None or c.employees >= EMPLOYEE_FLOOR:
            continue
        high_funding = c.total_funding_usd >= HIGH_FUNDING_USD
        high_valuation = (c.latest_valuation_usd or 0) >= HIGH_VALUATION_USD
        if not (high_funding or high_valuation):
            continue
        yield Issue(
            check_id="high_funding_few_employees",
            company_id=c.id,
            company_name=c.name,
            detail=(
                f"{c.employees} employees but "
                f"${c.total_funding_usd / 1e9:.1f}B total funding — headcount "
                "likely missing or stale."
            ),
            amount_usd=c.total_funding_usd,
        )


ALL_CHECKS: list[tuple[CheckMeta, object]] = [
    (
        CheckMeta(
            "big_unverified",
            "Big rounds not verified",
            "Rounds ≥ $10M that lack verification (no lead investor, no valuation).",
            SERIOUS,
        ),
        check_big_unverified,
    ),
    (
        CheckMeta(
            "missing_round_type",
            "Rounds without a round type",
            "Funding rounds with no round type set — should not happen.",
            CRITICAL,
        ),
        check_missing_round_type,
    ),
    (
        CheckMeta(
            "sequence_out_of_order",
            "Rounds out of stage order",
            "An earlier-stage round recorded after a later-stage round.",
            WARNING,
        ),
        check_sequence_out_of_order,
    ),
    (
        CheckMeta(
            "late_without_early",
            "Late stage with no early stage",
            "A late-stage round with no early-stage round on record.",
            WARNING,
        ),
        check_late_without_early,
    ),
    (
        CheckMeta(
            "big_round_no_location",
            "Big rounds without a location",
            "Big rounds on profiles with no location set.",
            SERIOUS,
        ),
        check_big_round_no_location,
    ),
    (
        CheckMeta(
            "high_funding_few_employees",
            "High funding, few employees",
            "High funding or valuation but fewer than 10 employees.",
            SERIOUS,
        ),
        check_high_funding_few_employees,
    ),
]

_SEVERITY_WEIGHT = {CRITICAL: 3, SERIOUS: 2, WARNING: 1}


@dataclass
class CheckResult:
    meta: CheckMeta
    issues: list[Issue] = field(default_factory=list)

    @property
    def count(self) -> int:
        return len(self.issues)


@dataclass
class Report:
    results: list[CheckResult]
    total_companies: int

    @property
    def total_issues(self) -> int:
        return sum(r.count for r in self.results)

    @property
    def flagged_company_ids(self) -> set[str]:
        return {i.company_id for r in self.results for i in r.issues}

    @property
    def clean_companies(self) -> int:
        return self.total_companies - len(self.flagged_company_ids)

    @property
    def health_score(self) -> int:
        """Weighted 0-100 score: starts at 100, each issue subtracts a
        severity-weighted amount, normalised by company count."""
        if not self.total_companies:
            return 100
        penalty = sum(
            _SEVERITY_WEIGHT[r.meta.severity] * r.count for r in self.results
        )
        # Scale so a company averaging one serious issue lands around 80.
        score = 100 - (5 * penalty / self.total_companies)
        return max(0, round(score))

    def result(self, check_id: str) -> CheckResult:
        return next(r for r in self.results if r.meta.id == check_id)


def run_checks(companies: list[Company]) -> Report:
    companies = list(companies)
    results = [
        CheckResult(meta=meta, issues=list(fn(companies)))  # type: ignore[operator]
        for meta, fn in ALL_CHECKS
    ]
    return Report(results=results, total_companies=len(companies))
