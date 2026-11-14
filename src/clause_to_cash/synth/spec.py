"""Ground-truth specification of one synthetic agreement, and its effective terms."""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Optional

from ..schema import Duration, Pricing, Terms
from ..taxonomy import Category


@dataclass
class Amend:
    kind: str  # extend | reprice | notice
    date: dt.date
    months: int = 0
    pricing: Optional[Pricing] = None
    notice: Optional[Duration] = None


@dataclass
class ContractSpec:
    cid: str
    entity: str  # "A" (acquirer) or "B" (acquired)
    style: int  # 0..2 seen drafting styles, 3 held-out
    cat: Category
    vendor_id: str  # canonical vendor identity (ground truth for entity resolution)
    vendor_name: str  # surface form as written in this document
    customer_name: str
    eff: dt.date
    term_months: int
    auto_renew: bool
    renewal_months: int
    notice: Optional[Duration]
    tfc: bool
    tfc_days: Optional[int]
    etf_pct: Optional[float]
    min_commit: Optional[float]
    pricing: Pricing
    escalator: Optional[float]
    affiliate: str
    coc: str
    exclusive: bool
    mfn: bool
    law: str
    units: float  # trailing-12-month volume
    amendments: list[Amend] = field(default_factory=list)
    explicit_negatives: dict[str, bool] = field(default_factory=dict)  # which "none" clauses are written out
    payment_days: int = 45
    state: str = "Delaware"


def effective_terms(s: ContractSpec) -> Terms:
    """Apply amendments in date order -> the terms a careful lawyer would report today."""
    term = s.term_months
    pricing = s.pricing
    notice = s.notice
    for a in sorted(s.amendments, key=lambda x: x.date):
        if a.kind == "extend":
            term += a.months
        elif a.kind == "reprice" and a.pricing is not None:
            pricing = a.pricing
        elif a.kind == "notice" and a.notice is not None and s.auto_renew:
            notice = a.notice
    return Terms(
        customer_party=s.customer_name,
        vendor_party=s.vendor_name,
        service_category=s.cat.key,
        effective_date=s.eff,
        initial_term_months=term,
        auto_renew=s.auto_renew,
        renewal_term_months=s.renewal_months if s.auto_renew else None,
        nonrenewal_notice=notice if s.auto_renew else None,
        termination_for_convenience=s.tfc,
        tfc_notice_days=s.tfc_days if s.tfc else None,
        early_termination_fee_pct=s.etf_pct if s.tfc else None,
        min_annual_commit_usd=s.min_commit,
        pricing=pricing,
        annual_escalator_pct=s.escalator,
        affiliate_purchasing=s.affiliate,  # type: ignore[arg-type]
        change_of_control=s.coc,  # type: ignore[arg-type]
        exclusive_requirements=s.exclusive,
        mfn=s.mfn,
        governing_law=s.law,
    )
