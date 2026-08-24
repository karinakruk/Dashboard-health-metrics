"""The funding data-health checks.

Each check is a pure function over the loaded companies and yields :class:`Issue`
records. ``ALL_CHECKS`` ties each function to its display metadata, and
``run_checks`` runs them all and rolls the results into a :class:`Report`.

The checks (as specified by the data team):

1. Big rounds (>=$10M) that are not verified.
2. Rounds without a round type (should not happen).
3. Big rounds on profiles without a location (country and city are both
   required). The warehouse path splits this into "city missing" vs "country
   and city missing"; this harness only implements the latter, because the
   local snapshot has no structured city field.
4. Companies with high funding/valuation but fewer than 10 employees.
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


def check_big_unverified(companies: Iterable[Company]) -> Iterator[Issue]:
    """Big rounds carrying Dealroom's literal "Unverified" status.

    This reads the verification flag as recorded — it does not infer it. Every
    instrument counts: an unverified $15B acquisition is still an unverified big
    round, so there is no round-type carve-out.
    """
    for c in companies:
        for r in c.rounds:
            if r.is_big and r.is_unverified:
                yield Issue(
                    check_id="big_unverified",
                    company_id=c.id,
                    company_name=c.name,
                    detail="Round is marked Unverified in Dealroom.",
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


def check_big_round_missing_city(companies: Iterable[Company]) -> Iterator[Issue]:
    """Big rounds where the country is known but the city is not.

    The warehouse splits the location gap in two, because a missing city on a
    known country is a quick fix while missing both needs research. This local
    harness cannot reproduce the split — the snapshot has no structured city
    field — so it yields nothing and the rule shows zero locally.

    It is still registered in ``ALL_CHECKS``: the BigQuery reader builds its
    report by iterating that list, so a rule missing from it would have its rows
    silently dropped rather than displayed.
    """
    return iter(())


def check_big_round_missing_location(companies: Iterable[Company]) -> Iterator[Issue]:
    for c in companies:
        if c.has_location:
            continue
        big = [r for r in c.rounds if r.is_big]
        if not big:
            continue
        largest = max(big, key=lambda r: r.amount_usd)
        yield Issue(
            check_id="big_round_missing_location",
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


# Thresholds for the funding-vs-headcount check. Aligned with the app filter the
# dashboard links to, so the count and the linked search agree: employees <= 10
# (the app's buckets are {1, 2-10}) and funding only, with no valuation branch.
HIGH_FUNDING_USD = 25_000_000
EMPLOYEE_CEILING = 10


def check_high_funding_few_employees(companies: Iterable[Company]) -> Iterator[Issue]:
    for c in companies:
        if c.employees is None or c.employees > EMPLOYEE_CEILING:
            continue
        if c.total_funding_usd < HIGH_FUNDING_USD:
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
            "Rounds ≥ $10M carrying Dealroom's Unverified status.",
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
            "big_round_missing_city",
            "City missing",
            "Big rounds where the country is known but the city is not.",
            WARNING,
        ),
        check_big_round_missing_city,
    ),
    (
        CheckMeta(
            "big_round_missing_location",
            "Big rounds without a location",
            "Big rounds on profiles with no country or city.",
            SERIOUS,
        ),
        check_big_round_missing_location,
    ),
    (
        CheckMeta(
            "high_funding_few_employees",
            "High funding, few employees",
            "Total funding >= $25M but 10 or fewer employees.",
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
    # At global scope a rule can flag far more rows than we ship to the browser.
    # `total_count` is the true number from the warehouse; `issues` may hold only
    # the top slice by impact. None means "issues is the complete set".
    total_count: int | None = None

    @property
    def count(self) -> int:
        """The true number of issues, which may exceed the rows displayed."""
        return self.total_count if self.total_count is not None else len(self.issues)

    @property
    def is_truncated(self) -> bool:
        return self.total_count is not None and self.total_count > len(self.issues)


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
