"""Data model for the funding snapshot and helpers to load it.

Pure data + loading only — no framework or network imports — so the model can be
used from tests, scripts and the web app alike.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path

DATA_FILE = Path(__file__).resolve().parent.parent / "data" / "funding_snapshot.json"

BIG_ROUND_THRESHOLD_USD = 10_000_000


@dataclass(frozen=True)
class Round:
    date: str  # "YYYY-MM"
    round_type: str | None
    amount_usd: int
    valuation_usd: int | None
    has_lead: bool
    # Dealroom's literal verification status for this round, as shown on the
    # profile ("Series A / Unverified"). None means we do not know it — which is
    # NOT the same as unverified, so unknown rounds are never flagged.
    verified: bool | None = None

    @property
    def is_big(self) -> bool:
        return self.amount_usd >= BIG_ROUND_THRESHOLD_USD

    @property
    def is_unverified(self) -> bool:
        """True only when the round is explicitly marked unverified."""
        return self.verified is False

    @property
    def has_type(self) -> bool:
        return bool(self.round_type) and self.round_type.strip().upper() != "NOT SET"


@dataclass(frozen=True)
class Company:
    id: str
    name: str
    hq_location: str | None
    country: str | None
    employees: int | None
    total_funding_usd: int
    latest_valuation_usd: int | None
    status: str
    growth_stage: str
    industry: str
    dealroom_url: str
    rounds: list[Round] = field(default_factory=list)

    @cached_property
    def rounds_by_date(self) -> list[Round]:
        """Rounds oldest-first; within a month the later-stage round comes first
        so the sequence check treats it as the anchor for that month."""
        from .checks import stage_tier  # local import to avoid a cycle

        return sorted(self.rounds, key=lambda r: (r.date, -(stage_tier(r) or 0)))

    @property
    def has_location(self) -> bool:
        return bool(self.hq_location and self.hq_location.strip())

    @property
    def biggest_round(self) -> Round | None:
        return max(self.rounds, key=lambda r: r.amount_usd, default=None)


@dataclass(frozen=True)
class Snapshot:
    source: str
    pulled_at: str
    companies: list[Company]


def _round_from_dict(d: dict) -> Round:
    return Round(
        date=d["date"],
        round_type=d.get("round_type"),
        amount_usd=d.get("amount_usd", 0),
        valuation_usd=d.get("valuation_usd"),
        has_lead=d.get("has_lead", False),
        verified=d.get("verified"),
    )


def _company_from_dict(d: dict) -> Company:
    return Company(
        id=d["id"],
        name=d["name"],
        hq_location=d.get("hq_location"),
        country=d.get("country"),
        employees=d.get("employees"),
        total_funding_usd=d.get("total_funding_usd", 0),
        latest_valuation_usd=d.get("latest_valuation_usd"),
        status=d.get("status", ""),
        growth_stage=d.get("growth_stage", ""),
        industry=d.get("industry", ""),
        dealroom_url=d.get("dealroom_url", ""),
        rounds=[_round_from_dict(r) for r in d.get("rounds", [])],
    )


def load_snapshot(path: Path | str = DATA_FILE) -> Snapshot:
    data = json.loads(Path(path).read_text())
    return Snapshot(
        source=data.get("source", ""),
        pulled_at=data.get("pulled_at", ""),
        companies=[_company_from_dict(c) for c in data.get("companies", [])],
    )
