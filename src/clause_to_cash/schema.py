"""Core data model.

Two layers:

* ``Terms`` – the *effective* (post-amendment) commercial terms of one vendor
  agreement, as plain typed values.  This is what the decision engines consume.
* ``Extraction`` – what an extractor *claims*: for every field a value, the
  verbatim quote(s) it relied on, and a self-reported confidence.  Nothing an
  extractor says is trusted until ``verify`` has checked it against the source.

``FIELDS`` is the single registry that drives the LLM tool schema, the prompt,
the evaluation comparators and the verification layer, so they cannot drift.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

AffiliateRight = Literal["allowed", "consent_required", "prohibited", "silent"]
ControlChange = Literal["free", "consent_required", "vendor_termination_right", "silent"]
TierMode = Literal["graduated", "all_units"]

AFFILIATE_VALUES = ("allowed", "consent_required", "prohibited", "silent")
CONTROL_VALUES = ("free", "consent_required", "vendor_termination_right", "silent")
TIER_MODES = ("graduated", "all_units")


class Duration(BaseModel):
    """A notice/term period exactly as written (months are NOT converted to days)."""

    model_config = ConfigDict(frozen=True)
    amount: int
    unit: Literal["days", "months"]

    def __str__(self) -> str:  # pragma: no cover - cosmetic
        return f"{self.amount} {self.unit}"


class Tier(BaseModel):
    lower: float  # inclusive
    upper: Optional[float] = None  # exclusive; None = unbounded
    unit_price: float


class Pricing(BaseModel):
    unit: str
    tier_mode: TierMode
    tiers: list[Tier]
    currency: str = "USD"


class Terms(BaseModel):
    """Effective commercial terms.  ``None`` means 'not stated / not extracted'."""

    customer_party: Optional[str] = None
    vendor_party: Optional[str] = None
    service_category: Optional[str] = None
    effective_date: Optional[dt.date] = None
    initial_term_months: Optional[int] = None
    auto_renew: Optional[bool] = None
    renewal_term_months: Optional[int] = None
    nonrenewal_notice: Optional[Duration] = None
    termination_for_convenience: Optional[bool] = None
    tfc_notice_days: Optional[int] = None
    early_termination_fee_pct: Optional[float] = None
    min_annual_commit_usd: Optional[float] = None
    pricing: Optional[Pricing] = None
    annual_escalator_pct: Optional[float] = None
    affiliate_purchasing: Optional[AffiliateRight] = None
    change_of_control: Optional[ControlChange] = None
    exclusive_requirements: Optional[bool] = None
    mfn: Optional[bool] = None
    governing_law: Optional[str] = None


# --------------------------------------------------------------------------- #
# Field registry
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class FieldSpec:
    name: str
    kind: str  # str | date | int | float | bool | enum | duration | pricing
    description: str
    critical: bool  # feeds a money/date decision downstream
    nullable: bool = True
    enum: tuple[str, ...] | None = None
    amendable: bool = False  # can be changed by a later amendment


def _f(*a: Any, **k: Any) -> FieldSpec:
    return FieldSpec(*a, **k)


FIELD_LIST: list[FieldSpec] = [
    _f("customer_party", "str", "Legal name of the customer (the buyer of services) as written in the agreement.", False),
    _f("vendor_party", "str", "Legal name of the vendor (the seller of services) exactly as written in the agreement.", False),
    _f("service_category", "enum", "Kind of service purchased (pick the closest category key).", True, nullable=False),
    _f("effective_date", "date", "Effective Date of the agreement, ISO format YYYY-MM-DD.", True),
    _f("initial_term_months", "int", "Length of the initial term in whole months (convert years to months). "
       "Must reflect any amendment that extends the term (base term + extension).", True, amendable=True),
    _f("auto_renew", "bool", "True if the agreement renews automatically after the initial term unless a party "
       "gives notice; False if it expires or needs a signed renewal.", True, nullable=False, amendable=True),
    _f("renewal_term_months", "int", "Length of each automatic renewal term in whole months; null if no auto-renewal.", False),
    _f("nonrenewal_notice", "duration", "Advance notice a party must give to prevent auto-renewal, as written: "
       "{amount, unit: 'days'|'months'}. Do NOT convert months to days. Reflect amendments. Null if none.", True, amendable=True),
    _f("termination_for_convenience", "bool", "True if CUSTOMER may terminate before the term ends without cause.", True, nullable=False),
    _f("tfc_notice_days", "int", "Days of notice required for termination for convenience; null if not permitted.", False),
    _f("early_termination_fee_pct", "float", "Percent (0-100) of remaining fees/commitment owed on termination for "
       "convenience; null if none or not permitted.", True),
    _f("min_annual_commit_usd", "float", "Annual minimum purchase / take-or-pay commitment in USD; null if none.", True),
    _f("pricing", "pricing", "Unit pricing schedule: {unit, tier_mode, tiers:[{lower, upper|null, unit_price}]}. "
       "tier_mode is 'graduated' if each unit is priced at the rate of the tier it falls in, or 'all_units' if the "
       "tier reached by TOTAL volume sets the price of every unit. Reflect amendments that replace the schedule.", True, amendable=True),
    _f("annual_escalator_pct", "float", "Fixed annual percentage price increase; null if fees are fixed.", False),
    _f("affiliate_purchasing", "enum", "May the customer's Affiliates/sister companies purchase under this agreement at these "
       "rates? allowed | consent_required (needs vendor approval) | prohibited | silent (not addressed).", True,
       nullable=False, enum=AFFILIATE_VALUES),
    _f("change_of_control", "enum", "Effect of a merger/change of control or assignment on the CUSTOMER: free (assignment "
       "to a successor allowed without consent) | consent_required | vendor_termination_right (vendor may terminate) | "
       "silent (not addressed).", True, nullable=False, enum=CONTROL_VALUES),
    _f("exclusive_requirements", "bool", "True if customer must buy all/most requirements of this service exclusively from the vendor.", True, nullable=False),
    _f("mfn", "bool", "True if the vendor gives a most-favoured-customer / no-less-favourable-pricing commitment.", False, nullable=False),
    _f("governing_law", "str", "Governing law jurisdiction, e.g. 'New York'.", False),
]
FIELDS: dict[str, FieldSpec] = {f.name: f for f in FIELD_LIST}
CRITICAL_FIELDS: list[str] = [f.name for f in FIELD_LIST if f.critical]


# --------------------------------------------------------------------------- #
# Extraction envelope
# --------------------------------------------------------------------------- #


class FieldExtraction(BaseModel):
    value: Any = None  # JSON-compatible; dates as ISO strings, objects as dicts
    quotes: list[str] = Field(default_factory=list)  # verbatim evidence
    confidence: float = 0.0  # extractor self-report, 0..1


class Extraction(BaseModel):
    doc_id: str
    extractor: str
    fields: dict[str, FieldExtraction] = Field(default_factory=dict)
    input_tokens: int = 0
    output_tokens: int = 0
    latency_s: float = 0.0
    error: Optional[str] = None

    def get(self, name: str) -> FieldExtraction:
        return self.fields.get(name, FieldExtraction())


def terms_from_values(values: dict[str, Any]) -> Terms:
    """Coerce a ``{field: json_value}`` dict into ``Terms`` (invalid values -> None)."""
    clean: dict[str, Any] = {}
    for name, v in values.items():
        if name not in FIELDS or v is None:
            continue
        try:
            Terms.model_validate({name: v})
        except Exception:
            continue
        clean[name] = v
    return Terms.model_validate(clean)


def terms_to_values(t: Terms) -> dict[str, Any]:
    """Inverse of ``terms_from_values`` (JSON-compatible)."""
    return t.model_dump(mode="json")
