from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping, Sequence

FIXTURE_ROOT = Path(__file__).resolve().parent / "fixtures"


@dataclass(frozen=True)
class GoldenFixture:
    """Represents a single ingestion artifact used during evaluation."""

    name: str
    filename: str
    description: str
    source_type: str = "json"
    metadata: Mapping[str, object] = field(default_factory=dict)

    @property
    def absolute_path(self) -> Path:
        return FIXTURE_ROOT / self.filename


@dataclass(frozen=True)
class GoldenQuery:
    """Canonical query + expectation in the golden set."""

    query_id: str
    text: str
    query_type: str  # identifier | natural | not_found
    expected_behavior: str  # alias_exact | alias_fallback | hybrid | not_found
    target_entities: Sequence[str] = field(default_factory=tuple)
    target_aliases: Sequence[str] = field(default_factory=tuple)
    target_fixtures: Sequence[str] = field(default_factory=tuple)
    notes: str = ""


@dataclass(frozen=True)
class GoldenSet:
    """Collection of fixtures and queries scoped to a business/industry."""

    slug: str
    industry: str
    business_slug: str
    fixtures: Sequence[GoldenFixture]
    queries: Sequence[GoldenQuery]
    default_top_k: int = 3


TRAVEL_FIXTURE = GoldenFixture(
    name="atlas_travel_catalog",
    filename="travel_catalog.json",
    description="Travel packages with slugs, SKUs, nested alias metadata.",
)

TRAVEL_QUERIES: tuple[GoldenQuery, ...] = (
    GoldenQuery(
        query_id="travel_identifier_siwa",
        text="siwa_ax_9901",
        query_type="identifier",
        expected_behavior="alias_exact",
        target_entities=("Siwa Oasis Escape",),
        target_aliases=("siwa_ax_9901", "trip-ax-9901"),
        notes="Underscore identifier should short-circuit alias path.",
    ),
    GoldenQuery(
        query_id="travel_identifier_luxor_dash",
        text="LUX-AX-77",
        query_type="identifier",
        expected_behavior="alias_exact",
        target_entities=("Luxor Sunrise Cruise",),
        target_aliases=("lux-ax-77", "lux_ax_77"),
    ),
    GoldenQuery(
        query_id="travel_natural_price",
        text="What is the adult price for the Luxor sunrise cruise?",
        query_type="natural",
        expected_behavior="hybrid",
        target_entities=("Luxor Sunrise Cruise",),
    ),
    GoldenQuery(
        query_id="travel_not_found",
        text="Need info on cairo hyperloop trip sku hyper-999",
        query_type="not_found",
        expected_behavior="not_found",
        notes="Explicit missing identifier should trigger fallback messaging.",
    ),
)

TRAVEL_SET = GoldenSet(
    slug="travel",
    industry="travel",
    business_slug="eval-travel",
    fixtures=(TRAVEL_FIXTURE,),
    queries=TRAVEL_QUERIES,
)

INSURANCE_FIXTURE = GoldenFixture(
    name="atlas_insurance_policies",
    filename="insurance_policies.json",
    description="Insurance policies nested under a JSON object with mixed-case aliases.",
)

INSURANCE_QUERIES: tuple[GoldenQuery, ...] = (
    GoldenQuery(
        query_id="insurance_identifier_atlas",
        text="ATLAS-CL-01",
        query_type="identifier",
        expected_behavior="alias_exact",
        target_entities=("Atlas Shield Classic",),
        target_aliases=("atlas-cl-01", "atlas_cl01"),
    ),
    GoldenQuery(
        query_id="insurance_identifier_delta_alias",
        text="dg-prem",
        query_type="identifier",
        expected_behavior="alias_exact",
        target_entities=("Delta Guard Premium",),
        target_aliases=("dg-prem", "delta_guard-prem"),
    ),
    GoldenQuery(
        query_id="insurance_natural_baggage",
        text="Which policy covers baggage up to five thousand?",
        query_type="natural",
        expected_behavior="hybrid",
        target_entities=("Delta Guard Premium",),
    ),
    GoldenQuery(
        query_id="insurance_not_found",
        text="policy nb-sec-44",
        query_type="not_found",
        expected_behavior="not_found",
        notes="Non-existent policy should remain a miss without hallucination.",
    ),
)

INSURANCE_SET = GoldenSet(
    slug="insurance",
    industry="insurance",
    business_slug="eval-insurance",
    fixtures=(INSURANCE_FIXTURE,),
    queries=INSURANCE_QUERIES,
)

CARDS_FIXTURE = GoldenFixture(
    name="nebula_cards_catalog",
    filename="commerce_catalog.json",
    description="Card catalog with short slugs, camelCase identifiers, and nested policies.",
)

CARDS_QUERIES: tuple[GoldenQuery, ...] = (
    GoldenQuery(
        query_id="cards_identifier_nb_metal",
        text="NB_METAL",
        query_type="identifier",
        expected_behavior="alias_exact",
        target_entities=("Nebula Card Metal",),
        target_aliases=("nb_metal", "nb_met-01", "nb_met01"),
    ),
    GoldenQuery(
        query_id="cards_identifier_orion_edge",
        text="edge-44",
        query_type="identifier",
        expected_behavior="alias_exact",
        target_entities=("Orion Business Edge",),
        target_aliases=("edge-44", "orion_edge"),
    ),
    GoldenQuery(
        query_id="cards_natural_apr",
        text="What APR does the Nebula lite card charge?",
        query_type="natural",
        expected_behavior="hybrid",
        target_entities=("Nebula Card Lite",),
    ),
    GoldenQuery(
        query_id="cards_not_found",
        text="Need cashback for Comet Ultra tier?",
        query_type="not_found",
        expected_behavior="not_found",
        notes="Intentional miss to validate fallback accuracy.",
    ),
)

CARDS_SET = GoldenSet(
    slug="cards",
    industry="financial_services",
    business_slug="eval-cards",
    fixtures=(CARDS_FIXTURE,),
    queries=CARDS_QUERIES,
)

JOBS_FIXTURE = GoldenFixture(
    name="jobs_sheet",
    filename="jobs_sheet.csv",
    description="Job listings table with mixed identifiers and company names.",
    source_type="csv",
)

JOBS_QUERIES: tuple[GoldenQuery, ...] = (
    GoldenQuery(
        query_id="jobs_company_michael_page",
        text="Michael Page job opportunities",
        query_type="natural",
        expected_behavior="hybrid",
        target_entities=("Michael Page",),
    ),
    GoldenQuery(
        query_id="jobs_company_helio",
        text="roles at Helio Health Clinic",
        query_type="natural",
        expected_behavior="hybrid",
        target_entities=("Helio Health Clinic",),
    ),
    GoldenQuery(
        query_id="jobs_identifier_primary",
        text="JOB-001",
        query_type="identifier",
        expected_behavior="alias_exact",
        target_entities=("Michael Page",),
        target_aliases=("job-001", "JOB-001"),
    ),
    GoldenQuery(
        query_id="jobs_not_found",
        text="open roles for Lunar Labs",
        query_type="not_found",
        expected_behavior="not_found",
        notes="Intentional miss to validate fallback flow on table-heavy tenants.",
    ),
)

JOBS_SET = GoldenSet(
    slug="jobs",
    industry="recruiting",
    business_slug="eval-jobs",
    fixtures=(JOBS_FIXTURE,),
    queries=JOBS_QUERIES,
)

FEES_PDF_FIXTURE = GoldenFixture(
    name="fees_credit_cards_pdf",
    filename="fees_credit_cards_eng_185.pdf",
    description="Fees and Charges Credit Cards Eng_185 (PDF)",
    source_type="pdf",
)

FEES_QUERIES: tuple[GoldenQuery, ...] = (
    GoldenQuery(
        query_id="fees_credit_cards_list_all_issuance",
        text="list me all credit cards and their issuance fees",
        query_type="natural",
        expected_behavior="hybrid",
        target_fixtures=("fees_credit_cards_pdf",),
        notes="Regression target: enumeration should retrieve all issuance-fee table groups.",
    ),
    GoldenQuery(
        query_id="fees_credit_cards_general",
        text="what are the credit card fees?",
        query_type="natural",
        expected_behavior="hybrid",
        target_fixtures=("fees_credit_cards_pdf",),
        notes="KNOWN_FAILURE: title-only chunk surfaces instead of fee rows.",
    ),
    GoldenQuery(
        query_id="fees_credit_cards_issuance",
        text="issuance and renewal fees for credit cards",
        query_type="natural",
        expected_behavior="hybrid",
        target_fixtures=("fees_credit_cards_pdf",),
    ),
    GoldenQuery(
        query_id="fees_credit_cards_replacement",
        text="replacement fees for credit cards",
        query_type="natural",
        expected_behavior="hybrid",
        target_fixtures=("fees_credit_cards_pdf",),
    ),
    GoldenQuery(
        query_id="fees_credit_cards_supplementary",
        text="supplementary card fees",
        query_type="natural",
        expected_behavior="hybrid",
        target_fixtures=("fees_credit_cards_pdf",),
    ),
    GoldenQuery(
        query_id="fees_credit_cards_grace_period",
        text="credit card grace period",
        query_type="natural",
        expected_behavior="hybrid",
        target_fixtures=("fees_credit_cards_pdf",),
    ),
    GoldenQuery(
        query_id="fees_credit_cards_interest",
        text="credit card interest rate",
        query_type="natural",
        expected_behavior="hybrid",
        target_fixtures=("fees_credit_cards_pdf",),
    ),
    GoldenQuery(
        query_id="fees_credit_cards_cash_domestic",
        text="cash withdrawal fees from domestic ATMs or POSs",
        query_type="natural",
        expected_behavior="hybrid",
        target_fixtures=("fees_credit_cards_pdf",),
    ),
    GoldenQuery(
        query_id="fees_credit_cards_cash_international",
        text="cash withdrawal fees from international ATMs",
        query_type="natural",
        expected_behavior="hybrid",
        target_fixtures=("fees_credit_cards_pdf",),
    ),
    GoldenQuery(
        query_id="fees_credit_cards_fx_markup",
        text="foreign exchange mark-up fee",
        query_type="natural",
        expected_behavior="hybrid",
        target_fixtures=("fees_credit_cards_pdf",),
    ),
    GoldenQuery(
        query_id="fees_credit_cards_ewallet",
        text="e-wallet loading fees",
        query_type="natural",
        expected_behavior="hybrid",
        target_fixtures=("fees_credit_cards_pdf",),
    ),
    GoldenQuery(
        query_id="fees_credit_cards_not_found_crypto",
        text="crypto trading fees on credit cards",
        query_type="not_found",
        expected_behavior="not_found",
        notes="Intentional miss to validate fallback handling.",
    ),
    GoldenQuery(
        query_id="fees_credit_cards_not_found_mortgage",
        text="mortgage application fee",
        query_type="not_found",
        expected_behavior="not_found",
        notes="Intentional miss to validate fallback handling.",
    ),
)

FEES_SET = GoldenSet(
    slug="fees-credit-cards",
    industry="financial_services",
    business_slug="eval-fees-credit-cards",
    fixtures=(FEES_PDF_FIXTURE,),
    queries=FEES_QUERIES,
)

ACCOUNT_FEES_PDF_FIXTURE = GoldenFixture(
    name="cib_account_pdf",
    filename="cib_account_en.pdf",
    description="CIB-Account-EN account fees and charges PDF",
    source_type="pdf",
)

ACCOUNT_FEES_QUERIES: tuple[GoldenQuery, ...] = (
    GoldenQuery(
        query_id="account_fees_opening_all",
        text="list me all account opening fees",
        query_type="natural",
        expected_behavior="hybrid",
        target_fixtures=("cib_account_pdf",),
        notes="Regression target: fresh-thread account opening fee enumeration.",
    ),
    GoldenQuery(
        query_id="account_fees_current_opening",
        text="current account opening fees",
        query_type="natural",
        expected_behavior="hybrid",
        target_fixtures=("cib_account_pdf",),
        notes="Regression target: account query should not be crowded out by card-fee documents.",
    ),
)

ACCOUNT_FEES_SET = GoldenSet(
    slug="account-fees",
    industry="financial_services",
    business_slug="eval-account-fees",
    fixtures=(ACCOUNT_FEES_PDF_FIXTURE,),
    queries=ACCOUNT_FEES_QUERIES,
)


GOLDEN_SETS: dict[str, GoldenSet] = {
    TRAVEL_SET.slug: TRAVEL_SET,
    INSURANCE_SET.slug: INSURANCE_SET,
    CARDS_SET.slug: CARDS_SET,
    JOBS_SET.slug: JOBS_SET,
    FEES_SET.slug: FEES_SET,
    ACCOUNT_FEES_SET.slug: ACCOUNT_FEES_SET,
}


__all__ = [
    "GoldenFixture",
    "GoldenQuery",
    "GoldenSet",
    "GOLDEN_SETS",
]
