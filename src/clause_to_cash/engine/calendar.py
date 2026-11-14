"""Decision calendar: every date a contract forces a decision, with the dollars attached.

The point of the product: a missed non-renewal notice silently re-commits the company to another
full renewal term.  The calendar turns clause text into *dates + dollars + a safety margin*.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from ..dates import lifecycle
from ..pricing import annual_cost
from .model import ContractRecord, PlanParams


@dataclass
class DeadlineEvent:
    cid: str
    entity: str
    vendor: str
    category: str
    kind: str  # notice_deadline | locked_in | term_end
    due: dt.date
    internal_due: dt.date | None
    days: int
    annual_cost: float
    at_stake: float  # dollars committed if the deadline is missed (or already committed, for locked_in)
    note: str
    provisional: bool = False


def annual_run_rate(r: ContractRecord, asof: dt.date) -> float:
    return annual_cost(r.terms, r.units, asof)


def build_calendar(records: list[ContractRecord], p: PlanParams, horizon_days: int = 365) -> list[DeadlineEvent]:
    out: list[DeadlineEvent] = []
    for r in records:
        t = r.terms
        lc = lifecycle(t, p.asof, p.buffer_days)
        cost = annual_run_rate(r, p.asof)
        vend = r.vendor_display or (t.vendor_party or "")
        if lc.status == "open" and lc.notice_deadline:
            renew_m = t.renewal_term_months or 12
            out.append(DeadlineEvent(
                r.cid, r.entity, vend, r.category, "notice_deadline", lc.notice_deadline, lc.internal_deadline,
                (lc.notice_deadline - p.asof).days, cost, cost * renew_m / 12,
                f"Auto-renews for {renew_m} months after {lc.period.end if lc.period else ''}; "
                f"notice of non-renewal due {lc.notice_deadline}", r.provisional))
        elif lc.status == "locked" and lc.period:
            remaining = max(0, (lc.period.end - p.asof).days)
            out.append(DeadlineEvent(
                r.cid, r.entity, vend, r.category, "locked_in", lc.natural_exit or lc.period.end, lc.internal_deadline,
                (lc.notice_deadline - p.asof).days if lc.notice_deadline else remaining,
                cost, cost * remaining / 365.25,
                f"Notice deadline for the current term already passed - committed through {lc.period.end}; "
                f"next notice deadline {lc.notice_deadline}", r.provisional))
        elif lc.status == "fixed_term" and lc.natural_exit:
            out.append(DeadlineEvent(
                r.cid, r.entity, vend, r.category, "term_end", lc.natural_exit, None,
                (lc.natural_exit - p.asof).days, cost, 0.0,
                "Fixed term, no auto-renewal: expires without action; renegotiate or re-bid before then", r.provisional))
    out = [e for e in out if e.days <= horizon_days]
    out.sort(key=lambda e: (e.due, -e.at_stake))
    return out
