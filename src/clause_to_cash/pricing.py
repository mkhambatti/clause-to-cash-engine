"""Tiered price arithmetic.

``graduated``  – tax-bracket style: each unit is priced at the rate of the tier it falls in.
                 Cost is continuous and non-decreasing in volume.
``all_units``  – the tier reached by *total* volume prices *every* unit.  Cost can DROP when
                 volume crosses a threshold (a known pricing cliff) – the planner must not
                 assume monotonicity, and tests pin this behaviour down.
"""
from __future__ import annotations

import datetime as dt

from .schema import Pricing, Terms


def cost(p: Pricing, units: float) -> float:
    """Cost of buying ``units`` in one year under pricing schedule ``p`` (before commitments)."""
    if units <= 0 or not p.tiers:
        return 0.0
    tiers = sorted(p.tiers, key=lambda t: t.lower)
    if p.tier_mode == "graduated":
        total = 0.0
        for t in tiers:
            hi = units if t.upper is None else min(units, t.upper)
            if hi > t.lower:
                total += (hi - t.lower) * t.unit_price
        return total
    # all_units: tier containing total volume prices all units
    chosen = tiers[0]
    for t in tiers:
        if units >= t.lower:
            chosen = t
    return units * chosen.unit_price


def escalation_multiplier(terms: Terms, asof: dt.date) -> float:
    """Price index at ``asof`` given a fixed annual escalator applied on each anniversary."""
    if not terms.annual_escalator_pct or terms.effective_date is None or asof <= terms.effective_date:
        return 1.0
    years = int((asof - terms.effective_date).days // 365.25)
    return (1.0 + terms.annual_escalator_pct / 100.0) ** years


def annual_cost(terms: Terms, units: float, asof: dt.date | None = None, floor_commit: bool = True) -> float:
    """Annual cost of ``units`` including escalation and the take-or-pay floor."""
    if terms.pricing is None:
        return 0.0
    mult = escalation_multiplier(terms, asof) if asof else 1.0
    c = cost(terms.pricing, units) * mult
    if floor_commit and terms.min_annual_commit_usd:
        c = max(c, terms.min_annual_commit_usd)
    return c


def effective_rate(p: Pricing, units: float) -> float:
    return cost(p, units) / units if units > 0 else 0.0
