"""Deterministic contract date arithmetic.

Conventions (documented because they decide whether a notice is on time):

* A term of *N months* starting on ``effective`` ends on the day before the N-month
  anniversary (``2024-03-01`` + 36 months -> ends ``2027-02-28``), inclusive.
* "Notice at least N days/months prior to the end of the then-current term" must be
  *given* no later than ``end - N``.  Month arithmetic is calendar-based with end-of-
  month clamping.  The contractual deadline is rolled back to the previous business
  day (weekends only); an *internal* deadline subtracts an additional safety buffer.
"""
from __future__ import annotations

import calendar
import datetime as dt
from dataclasses import dataclass
from typing import Optional

from .schema import Terms


def add_months(d: dt.date, n: int) -> dt.date:
    y = d.year + (d.month - 1 + n) // 12
    m = (d.month - 1 + n) % 12 + 1
    return dt.date(y, m, min(d.day, calendar.monthrange(y, m)[1]))


def term_end(start: dt.date, months: int) -> dt.date:
    return add_months(start, months) - dt.timedelta(days=1)


def sub_duration(d: dt.date, amount: int, unit: str) -> dt.date:
    return d - dt.timedelta(days=amount) if unit == "days" else add_months(d, -amount)


def prev_business_day(d: dt.date) -> dt.date:
    while d.weekday() >= 5:
        d -= dt.timedelta(days=1)
    return d


@dataclass(frozen=True)
class Period:
    index: int  # 0 = initial term, n = nth renewal
    start: dt.date
    end: dt.date  # inclusive


def current_period(terms: Terms, asof: dt.date) -> Optional[Period]:
    """The term period containing ``asof`` (rolling auto-renewals forward); ``None`` if expired."""
    if terms.effective_date is None or not terms.initial_term_months:
        return None
    start = terms.effective_date
    end = term_end(start, terms.initial_term_months)
    idx = 0
    if start > asof:
        return Period(0, start, end)
    while end < asof:
        if not (terms.auto_renew and terms.renewal_term_months):
            return None
        start = end + dt.timedelta(days=1)
        end = term_end(start, terms.renewal_term_months)
        idx += 1
        if idx > 400:  # pathological input guard
            return None
    return Period(idx, start, end)


def next_period(terms: Terms, p: Period) -> Optional[Period]:
    if not (terms.auto_renew and terms.renewal_term_months):
        return None
    start = p.end + dt.timedelta(days=1)
    return Period(p.index + 1, start, term_end(start, terms.renewal_term_months))


def notice_deadline(terms: Terms, p: Period) -> Optional[dt.date]:
    """Last day on which non-renewal notice can be given for period ``p``."""
    n = terms.nonrenewal_notice
    if n is None:
        return None
    return prev_business_day(sub_duration(p.end, n.amount, n.unit))


@dataclass(frozen=True)
class Lifecycle:
    status: str  # open | locked | fixed_term | expired | unknown
    period: Optional[Period]
    notice_deadline: Optional[dt.date]  # for the period whose deadline is still actionable (or next one)
    internal_deadline: Optional[dt.date]
    days_to_deadline: Optional[int]
    natural_exit: Optional[dt.date]  # earliest date the contract ends with zero penalty
    lock_in_months: int  # months you are committed to if the deadline is missed / already missed


def lifecycle(terms: Terms, asof: dt.date, buffer_days: int = 14) -> Lifecycle:
    p = current_period(terms, asof)
    if p is None:
        if terms.effective_date is None or not terms.initial_term_months:
            return Lifecycle("unknown", None, None, None, None, None, 0)
        return Lifecycle("expired", None, None, None, None, None, 0)
    if not terms.auto_renew:
        return Lifecycle("fixed_term", p, None, None, None, p.end, 0)
    dl = notice_deadline(terms, p)
    if dl is None:
        return Lifecycle("unknown", p, None, None, None, None, terms.renewal_term_months or 0)
    if asof <= dl:
        internal = prev_business_day(dl - dt.timedelta(days=buffer_days))
        return Lifecycle("open", p, dl, internal, (dl - asof).days, p.end, terms.renewal_term_months or 0)
    # deadline for the current period has passed -> locked into the next period
    nxt = next_period(terms, p)
    assert nxt is not None
    dl2 = notice_deadline(terms, nxt)
    internal2 = prev_business_day(dl2 - dt.timedelta(days=buffer_days)) if dl2 else None
    return Lifecycle("locked", p, dl2, internal2, (dl2 - asof).days if dl2 else None, nxt.end,
                     terms.renewal_term_months or 0)
