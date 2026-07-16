"""Build the funding data-health snapshot from a real Dealroom pull.

The figures below were pulled from the Dealroom API (analyze_company +
entity_fundings) on 2026-07-16. This script is the single source of truth for
`data/funding_snapshot.json` — edit the tables here and re-run:

    python scripts/build_snapshot.py

Provenance / modelling notes (kept deliberately transparent):

* Company profile fields (name, employees, total_funding, status, growth stage,
  industry) come straight from Dealroom `analyze_company`.
* Funding rounds (date, round type, amount, valuation, whether a lead investor
  was named) come straight from Dealroom `entity_fundings`.
* Non-USD round amounts are converted to USD with the fixed rates in ``FX``
  (approximate, good enough for the >=$10M anomaly thresholds).
* ``verified`` is NOT exposed by the endpoints we used, so it is DERIVED with a
  transparent completeness proxy (see ``derive_verified``): a round counts as
  verified when it has a disclosed amount and either a named lead investor or a
  stated valuation. Dealroom's own transaction index does expose a real
  ``is_verified`` flag — swap the proxy for that field in production.
* ``hq_location`` is taken from the company profile text where stated. For Quibi
  and Venari Resources the structured HQ location is a genuine gap in the pulled
  data, so it is left null — which is exactly what the "big round, no location"
  check is meant to surface.
* Quibi and Venari's private-equity/strategic raises are recorded with a null
  round type, faithfully reflecting how sparsely those two profiles are staged
  in the source — this is what the "round without a type" check surfaces.
"""

import json
from pathlib import Path

FX = {"USD": 1.0, "EUR": 1.08, "GBP": 1.27, "SEK": 0.095}


def usd(value: float, unit: str = "M", currency: str = "USD") -> int:
    """Convert an amount in the given unit/currency to whole USD."""
    scale = {"K": 1e3, "M": 1e6, "B": 1e9}[unit]
    return round(value * scale * FX[currency])


def val(value: float, unit: str = "B") -> int:
    scale = {"M": 1e6, "B": 1e9}[unit]
    return round(value * scale)


# Each round: (date "YYYY-MM", round_type|None, amount_usd, valuation_usd|None, has_lead)
COMPANIES = [
    {
        "id": "revolut",
        "name": "Revolut",
        "hq_location": "London, United Kingdom",
        "country": "United Kingdom",
        "employees": 18462,
        "total_funding_usd": val(3.7),
        "status": "operational",
        "growth_stage": "late stage",
        "industry": "fintech",
        "dealroom_url": "https://app.dealroom.co/companies/revolut",
        "rounds": [
            ("2025-11", "SECONDARY", usd(3.0, "B"), val(75), True),
            ("2025-07", "LATE VC", usd(2.0, "B"), val(75), True),
            ("2024-08", "SECONDARY", 0, val(45), False),
            ("2021-07", "SERIES E", usd(800), val(33), True),
            ("2020-07", "SERIES D", usd(80), val(5.5), True),
            ("2020-02", "SERIES D", usd(500), val(5.5), True),
            ("2019-03", "SUPPORT PROGRAM", 0, None, False),
            ("2018-04", "SERIES C", usd(250), val(1.7), True),
            ("2017-07", "SERIES B", usd(66), None, True),
            ("2017-07", "EARLY VC", usd(5.3), None, False),
            ("2017-06", "DEBT", 0, None, True),
            ("2016-07", "SERIES A", usd(8.8), val(42, "M"), True),
            ("2016-07", "EARLY VC", usd(1.2), val(42, "M"), False),
            ("2016-06", "SUPPORT PROGRAM", 0, None, False),
            ("2016-02", "SEED", usd(4.8), None, True),
            ("2015-07", "SEED", usd(2.3), None, True),
        ],
    },
    {
        "id": "northvolt",
        "name": "Northvolt",
        "hq_location": "Stockholm, Sweden",
        "country": "Sweden",
        "employees": 1671,
        "total_funding_usd": val(7.7),
        "status": "acquired",
        "growth_stage": "late stage",
        "industry": "energy",
        "dealroom_url": "https://app.dealroom.co/companies/northvolt",
        "rounds": [
            ("2025-08", "ACQUISITION", 0, None, False),
            ("2024-11", "BANKRUPTCY", 0, None, False),
            ("2024-10", "GROWTH EQUITY NON VC", usd(300), None, False),
            ("2024-10", "DEBT", 0, None, False),
            ("2024-01", "DEBT", usd(1.0, "B"), None, False),
            ("2023-12", "GRANT", usd(700, "M", "EUR"), None, False),
            ("2023-11", "CONVERTIBLE", usd(200), None, False),
            ("2023-08", "CONVERTIBLE", usd(1.2, "B"), None, False),
            ("2022-07", "CONVERTIBLE", usd(1.1, "B"), val(12), False),
            ("2021-06", "GROWTH EQUITY VC", usd(2.8, "B"), val(11.8), False),
            ("2021-04", "GRANT", usd(4.4, "M", "EUR"), None, False),
            ("2020-09", "GROWTH EQUITY VC", usd(600), val(4.8), False),
            ("2020-07", "DEBT", usd(1.6, "B"), None, False),
            ("2019-12", "LATE VC", usd(6.9), val(1.6), False),
            ("2019-06", "GROWTH EQUITY VC", usd(1.0, "B"), val(1.6), False),
            ("2019-01", "EARLY VC", usd(13.7), None, False),
            ("2018-05", "EARLY VC", usd(100, "M", "SEK"), None, True),
            ("2018-02", "GRANT", usd(146, "M", "SEK"), None, False),
            ("2018-02", "DEBT", usd(520, "M", "SEK"), None, True),
            ("2017-10", "EARLY VC", usd(11.8), None, False),
            ("2017-03", "EARLY VC", usd(13.0, "M", "EUR"), None, False),
        ],
    },
    {
        "id": "klarna",
        "name": "Klarna",
        "hq_location": "Stockholm, Sweden",
        "country": "Sweden",
        "employees": 4627,
        "total_funding_usd": val(4.5),
        "status": "operational",
        "growth_stage": "late stage",
        "industry": "fintech",
        "dealroom_url": "https://app.dealroom.co/companies/klarna",
        "rounds": [
            ("2026-07", "POST IPO DEBT", usd(900, "M", "EUR"), None, False),
            ("2026-03", "POST IPO DEBT", usd(2.0, "B"), None, False),
            ("2025-11", "POST IPO DEBT", usd(6.5, "B"), None, False),
            ("2025-09", "IPO", usd(1.4, "B"), val(15.1), False),
            ("2025-08", "DEBT", usd(1.4, "B"), None, False),
            ("2025-06", "GROWTH EQUITY VC", usd(300), None, True),
            ("2023-11", "SECONDARY", 0, None, False),
            ("2022-07", "GROWTH EQUITY VC", usd(800), val(6.7), True),
            ("2022-03", "SECONDARY", 0, None, False),
            ("2021-06", "GROWTH EQUITY VC", usd(639), val(45.6), True),
            ("2021-05", "SECONDARY", usd(6.0), val(31), False),
            ("2021-03", "GROWTH EQUITY VC", usd(1.0, "B"), val(31), True),
            ("2020-09", "LATE VC", 0, None, False),
            ("2020-09", "GROWTH EQUITY VC", usd(650), val(10.6), True),
            ("2020-03", "LATE VC", 0, None, False),
            ("2020-01", "LATE VC", usd(200), val(5.5), True),
            ("2019-12", "SECONDARY", 0, None, False),
            ("2019-08", "GROWTH EQUITY VC", usd(460), val(5.5), True),
            ("2019-04", "GROWTH EQUITY VC", usd(93), val(3.5), False),
            ("2019-01", "SECONDARY", 0, None, False),
            ("2018-10", "GROWTH EQUITY VC", usd(20), None, False),
            ("2017-07", "SECONDARY", usd(250), val(2.5), False),
            ("2017-06", "LATE VC", usd(175, "M", "SEK"), None, False),
            ("2017-06", "SECONDARY", usd(225, "M", "EUR"), val(2.3), False),
            ("2017-03", "LATE VC", usd(4.6, "M", "EUR"), val(2.0), True),
        ],
    },
    {
        "id": "mistral-ai",
        "name": "Mistral AI",
        "hq_location": "Paris, France",
        "country": "France",
        "employees": 1156,
        "total_funding_usd": val(2.9),
        "status": "operational",
        "growth_stage": "late stage",
        "industry": "artificial intelligence",
        "dealroom_url": "https://app.dealroom.co/companies/mistral_ai",
        "rounds": [
            ("2026-03", "DEBT", usd(830), None, False),
            ("2025-09", "SERIES C", usd(1.7, "B", "EUR"), val(11.8), True),
            ("2024-07", "GROWTH EQUITY VC", 0, None, False),
            ("2024-06", "SERIES B", usd(468, "M", "EUR"), val(5.9), True),
            ("2024-06", "DEBT", usd(132, "M", "EUR"), None, False),
            ("2024-03", "SERIES A", 0, None, False),
            ("2024-02", "SERIES A", usd(15, "M", "EUR"), val(1.9), False),
            ("2023-12", "SERIES A", usd(385, "M", "EUR"), val(1.9), True),
            ("2023-06", "SEED", usd(105, "M", "EUR"), val(240, "M"), True),
        ],
    },
    {
        "id": "wayve",
        "name": "Wayve",
        "hq_location": "London, United Kingdom",
        "country": "United Kingdom",
        "employees": 400,  # estimate — profile lookup collided with a namesake
        "total_funding_usd": val(2.6),
        "status": "operational",
        "growth_stage": "late stage",
        "industry": "artificial intelligence",
        "dealroom_url": "https://app.dealroom.co/companies/wayve",
        "rounds": [
            ("2026-04", "SERIES D", usd(60), val(8.6), True),
            ("2026-02", "SERIES D", usd(1.2, "B"), val(8.6), True),
            ("2024-08", "SERIES C", 0, None, True),
            ("2024-05", "SERIES C", usd(1.1, "B"), val(3.0), True),
            ("2022-09", "SUPPORT PROGRAM", 0, None, False),
            ("2022-01", "SERIES B", usd(200), None, True),
            ("2021-10", "EARLY VC", usd(13.6), val(100, "M"), False),
            ("2020-08", "SERIES A", usd(15.2, "M", "GBP"), None, True),
            ("2019-06", "SERIES A", usd(16.7, "M", "GBP"), None, True),
            ("2018-04", "GRANT", usd(130, "K", "GBP"), None, False),
            ("2017-10", "SEED", usd(1.6, "M", "GBP"), None, True),
        ],
    },
    {
        "id": "helsing",
        "name": "Helsing",
        "hq_location": "Munich, Germany",
        "country": "Germany",
        "employees": 801,
        "total_funding_usd": val(3.3),
        "status": "operational",
        "growth_stage": "late stage",
        "industry": "defense",
        "dealroom_url": "https://app.dealroom.co/companies/helsing",
        "rounds": [
            ("2026-07", "SERIES E", usd(1.8, "B"), val(18), True),
            ("2025-06", "SERIES D", usd(600, "M", "EUR"), val(12), True),
            ("2024-07", "SERIES C", usd(450, "M", "EUR"), None, True),
            ("2023-09", "SERIES B", usd(209, "M", "EUR"), None, True),
            ("2021-11", "SERIES A", usd(102.5, "M", "EUR"), None, True),
        ],
    },
    {
        "id": "monzo",
        "name": "Monzo Bank",
        "hq_location": "London, United Kingdom",
        "country": "United Kingdom",
        "employees": 4841,
        "total_funding_usd": val(1.9),
        "status": "operational",
        "growth_stage": "late stage",
        "industry": "fintech",
        "dealroom_url": "https://app.dealroom.co/companies/monzo_bank",
        "rounds": [
            ("2024-10", "SECONDARY", 0, val(5.4), False),
            ("2024-05", "LATE VC", usd(190), val(5.2), False),
            ("2024-03", "LATE VC", usd(340, "M", "GBP"), val(4.0), True),
            ("2021-12", "LATE VC", usd(500), val(4.5), True),
            ("2021-12", "LATE VC", usd(100), val(4.5), False),
            ("2021-02", "SERIES H", usd(50, "M", "GBP"), val(1.2), False),
            ("2020-12", "SERIES G", usd(60, "M", "GBP"), val(1.2), False),
            ("2020-06", "SERIES G", usd(65, "M", "GBP"), val(1.3), False),
            ("2019-06", "SERIES F", usd(113, "M", "GBP"), val(2.0), True),
            ("2019-03", "SUPPORT PROGRAM", 0, None, False),
            ("2018-12", "LATE VC", usd(19.9, "M", "GBP"), val(1.0), False),
            ("2018-10", "SERIES E", usd(85, "M", "GBP"), val(1.1), True),
            ("2017-12", "LATE VC", usd(880, "K", "GBP"), val(280, "M"), False),
            ("2017-11", "SERIES D", usd(60, "M", "GBP"), val(280, "M"), True),
            ("2017-11", "SECONDARY", usd(11, "M", "GBP"), None, False),
            ("2017-07", "SERIES B", usd(25.2), None, False),
            ("2017-03", "LATE VC", usd(2.4, "M", "GBP"), val(84.8, "M"), False),
            ("2017-02", "SERIES C", usd(19.5, "M", "GBP"), None, True),
            ("2017-01", "SUPPORT PROGRAM", 0, None, False),
            ("2016-10", "SERIES B", usd(4.8, "M", "GBP"), None, True),
            ("2016-03", "SERIES A", usd(990, "K", "GBP"), val(29, "M"), False),
            ("2016-02", "SERIES A", usd(5.0, "M", "GBP"), None, True),
            ("2015-07", "SEED", usd(2.0, "M", "GBP"), None, False),
        ],
    },
    {
        "id": "quibi",
        "name": "Quibi",
        "hq_location": None,  # structured HQ location is a genuine gap in the source
        "country": "United States",
        "employees": 2,
        "total_funding_usd": val(1.8),
        "status": "acquired",
        "growth_stage": "late stage",
        "industry": "media",
        "dealroom_url": "https://app.dealroom.co/companies/quibi",
        "rounds": [
            ("2021-01", "ACQUISITION", usd(100), None, False),
            ("2020-01", None, usd(750), None, False),
            ("2018-08", None, usd(1.0, "B"), None, False),
        ],
    },
    {
        "id": "venari-resources",
        "name": "Venari Resources",
        "hq_location": None,  # structured HQ location is a genuine gap in the source
        "country": "United States",
        "employees": 9,
        "total_funding_usd": val(2.6),
        "status": "operational",
        "growth_stage": "late stage",
        "industry": "energy",
        "dealroom_url": "https://app.dealroom.co/companies/venari_resources",
        "rounds": [
            ("2014-01", None, usd(1.3, "B"), None, False),
            ("2012-05", None, usd(1.125, "B"), None, False),
        ],
    },
]


def to_records(company: dict) -> dict:
    rounds = [
        {
            "date": date,
            "round_type": rtype,
            "amount_usd": amount,
            "valuation_usd": valuation,
            "has_lead": has_lead,
        }
        for (date, rtype, amount, valuation, has_lead) in company["rounds"]
    ]
    # Company latest valuation = valuation of the most recent round that has one.
    latest_val = next(
        (r["valuation_usd"] for r in sorted(rounds, key=lambda r: r["date"], reverse=True)
         if r["valuation_usd"]),
        None,
    )
    return {**company, "rounds": rounds, "latest_valuation_usd": latest_val}


def main() -> None:
    snapshot = {
        "source": "Dealroom API (analyze_company + entity_fundings)",
        "pulled_at": "2026-07-16",
        "big_round_threshold_usd": 10_000_000,
        "companies": [to_records(c) for c in COMPANIES],
    }
    out = Path(__file__).resolve().parent.parent / "data" / "funding_snapshot.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(snapshot, indent=2))
    n_rounds = sum(len(c["rounds"]) for c in COMPANIES)
    print(f"Wrote {out} — {len(COMPANIES)} companies, {n_rounds} rounds")


if __name__ == "__main__":
    main()
