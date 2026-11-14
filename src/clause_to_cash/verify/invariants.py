"""Cross-field invariants over an extracted ``Terms`` – catches errors no single quote can reveal."""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from ..schema import Terms
from ..taxonomy import CATEGORY_BY_KEY


@dataclass(frozen=True)
class Violation:
    fields: tuple[str, ...]
    code: str
    severity: str  # "error" -> forces review of the named fields; "warn" -> informational
    message: str


def check_invariants(t: Terms) -> list[Violation]:  # noqa: C901
    v: list[Violation] = []

    def err(fields: tuple[str, ...], code: str, msg: str, sev: str = "error") -> None:
        v.append(Violation(fields, code, sev, msg))

    if t.effective_date is None:
        err(("effective_date",), "MISSING_EFFECTIVE_DATE", "effective date not found")
    elif not (dt.date(1995, 1, 1) <= t.effective_date <= dt.date(2030, 12, 31)):
        err(("effective_date",), "IMPLAUSIBLE_DATE", f"effective date {t.effective_date} outside 1995-2030")

    if t.initial_term_months is None:
        err(("initial_term_months",), "MISSING_TERM", "initial term not found")
    elif not (1 <= t.initial_term_months <= 120):
        err(("initial_term_months",), "IMPLAUSIBLE_TERM", f"term {t.initial_term_months} months")

    if t.auto_renew:
        if not t.renewal_term_months:
            err(("renewal_term_months", "auto_renew"), "AUTO_RENEW_NO_TERM", "auto-renew without renewal length")
        if t.nonrenewal_notice is None:
            err(("nonrenewal_notice", "auto_renew"), "AUTO_RENEW_NO_NOTICE", "auto-renew without non-renewal notice period")
    if t.nonrenewal_notice is not None:
        n = t.nonrenewal_notice
        cap = 365 if n.unit == "days" else 12
        if not (1 <= n.amount <= cap):
            err(("nonrenewal_notice",), "IMPLAUSIBLE_NOTICE", f"notice {n.amount} {n.unit}")
        if t.initial_term_months:
            days = n.amount if n.unit == "days" else n.amount * 30
            if days >= t.initial_term_months * 30:
                err(("nonrenewal_notice", "initial_term_months"), "NOTICE_EXCEEDS_TERM", "notice period >= initial term")

    if t.termination_for_convenience:
        if t.tfc_notice_days is None:
            err(("tfc_notice_days", "termination_for_convenience"), "TFC_NO_NOTICE", "TfC permitted but no notice period")
    elif t.termination_for_convenience is False:
        if t.tfc_notice_days is not None or t.early_termination_fee_pct is not None:
            err(("termination_for_convenience", "tfc_notice_days", "early_termination_fee_pct"), "TFC_INCONSISTENT",
                "TfC not permitted but notice/fee present")
    if t.early_termination_fee_pct is not None and not (0 <= t.early_termination_fee_pct <= 100):
        err(("early_termination_fee_pct",), "IMPLAUSIBLE_ETF", f"ETF {t.early_termination_fee_pct}%")

    if t.min_annual_commit_usd is not None and not (0 < t.min_annual_commit_usd <= 1e9):
        err(("min_annual_commit_usd",), "IMPLAUSIBLE_COMMIT", f"commit {t.min_annual_commit_usd}")
    if t.annual_escalator_pct is not None and not (0 <= t.annual_escalator_pct <= 25):
        err(("annual_escalator_pct",), "IMPLAUSIBLE_ESCALATOR", f"escalator {t.annual_escalator_pct}%")

    p = t.pricing
    if p is None:
        err(("pricing",), "MISSING_PRICING", "pricing schedule not found")
    else:
        tiers = p.tiers
        if not tiers:
            err(("pricing",), "NO_TIERS", "empty tier list")
        else:
            if tiers[0].lower != 0:
                err(("pricing",), "TIER_START", "first tier does not start at 0")
            for a, b in zip(tiers, tiers[1:]):
                if a.upper is None or abs(a.upper - b.lower) > 1e-9:
                    err(("pricing",), "TIER_GAP", f"tier boundary {a.upper} != next lower {b.lower}")
                    break
            if tiers[-1].upper is not None:
                err(("pricing",), "TIER_OPEN_END", "last tier is bounded")
            if any(x.unit_price <= 0 for x in tiers):
                err(("pricing",), "TIER_PRICE", "non-positive unit price")
            if any(b.unit_price > a.unit_price + 1e-12 for a, b in zip(tiers, tiers[1:])):
                err(("pricing",), "TIER_NOT_DISCOUNTED", "unit price rises with volume (swapped tiers?)")
        cat = CATEGORY_BY_KEY.get(t.service_category or "")
        if cat and p.unit and p.unit != cat.unit:
            err(("pricing", "service_category"), "UNIT_CATEGORY_MISMATCH",
                f"unit '{p.unit}' unusual for {cat.key} (expected '{cat.unit}')", "warn")
    if t.service_category is not None and t.service_category not in CATEGORY_BY_KEY:
        err(("service_category",), "UNKNOWN_CATEGORY", f"unknown category {t.service_category!r}")
    return v
