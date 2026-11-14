"""Consolidation options: *what would it be worth to pool this volume, and when could we legally do it?*

For every service category (and every vendor within a category) holding two or more contracts we
enumerate options ``(survivor contract, switch quarter)``: all volume in the cluster moves onto the
survivor's paper at quarter ``t``.  Each option is priced exactly from the extracted terms:

* savings   = current cost of all member contracts  -  survivor's schedule applied to the POOLED volume
              (graduated vs all-units tiers, escalators and take-or-pay floors all honoured)
* one-time  = early-termination fees (if exiting before natural expiry via termination-for-convenience)
              + switching cost (category-specific, tiny when volume stays with the same vendor)
* earliest  = the latest of: every member's earliest legal exit (notice windows, locked-in renewals,
              exclusivity), the survivor's affiliate-purchasing / change-of-control lead time, and the
              category's migration lead time

Nothing here is a model guess: every number traces to a clause with evidence.
"""
from __future__ import annotations

import datetime as dt
import math
from dataclasses import dataclass, field

from ..dates import lifecycle
from ..pricing import annual_cost
from ..taxonomy import CATEGORY_BY_KEY
from .model import QUARTER_DAYS, ContractRecord, PlanParams


@dataclass
class Option:
    oid: str
    kind: str  # consolidate (vendor switch) | paper (same vendor, re-paper & pool)
    cluster: str
    category: str
    survivor: str
    members: list[str]
    switch_q: int
    annual_baseline: float
    annual_pooled: float
    annual_saving: float
    exit_fees: float
    switching_cost: float
    npv: float
    load: float
    stream: bool
    steps: list[str] = field(default_factory=list)
    provisional: bool = False
    pooled_units: float = 0.0

    @property
    def one_time(self) -> float:
        return self.exit_fees + self.switching_cost


def q_of(d: dt.date, asof: dt.date) -> int:
    return max(0, math.ceil((d - asof).days / QUARTER_DAYS))


@dataclass
class _Exit:
    q_nat: int
    q_tfc: int | None
    natural_exit: dt.date | None
    annual: float
    etf: float
    status: str
    notice_deadline: dt.date | None


def _exit_info(r: ContractRecord, p: PlanParams) -> _Exit | None:
    lc = lifecycle(r.terms, p.asof, p.buffer_days)
    if lc.status == "unknown":
        return None
    annual = annual_cost(r.terms, r.units, p.asof)
    if lc.status == "expired" or lc.natural_exit is None:
        return _Exit(0, None, None, annual, 0.0, lc.status, None)
    q_nat = q_of(lc.natural_exit + dt.timedelta(days=1), p.asof)
    q_tfc = None
    if r.terms.termination_for_convenience and r.terms.tfc_notice_days is not None:
        q_tfc = q_of(p.asof + dt.timedelta(days=r.terms.tfc_notice_days), p.asof)
    cur_deadline = lc.notice_deadline if lc.status == "open" else None
    return _Exit(q_nat, q_tfc, lc.natural_exit, annual, (r.terms.early_termination_fee_pct or 0.0) / 100.0, lc.status, cur_deadline)


def _exit_cost(e: _Exit, t: int, p: PlanParams) -> float | None:
    if t >= e.q_nat:
        return 0.0
    if e.q_tfc is not None and t >= e.q_tfc and e.natural_exit is not None:
        switch_date = p.asof + dt.timedelta(days=t * QUARTER_DAYS)
        remaining = max(0, (e.natural_exit - switch_date).days)
        return e.etf * e.annual * remaining / 365.25
    return None


def _npv(saving: float, one_time: float, t: int, p: PlanParams) -> float:
    r, q_total = p.discount_rate, p.horizon_quarters
    gain = sum((saving / 4.0) * (1 + r) ** (-(q + 1) / 4.0) for q in range(t, q_total))
    return gain - one_time * (1 + r) ** (-t / 4.0)


def _price_option(cluster: str, cat_key: str, members: list[ContractRecord], s: ContractRecord, t: int, p: PlanParams,
                  exits: dict, ideal: bool = False) -> Option | None:
    """Price ONE option exactly: pool every member's volume onto survivor ``s``'s paper at quarter ``t``.

    Returns None if the option is not legally/commercially feasible at ``t`` or does not create value.
    Used both to enumerate options and to re-price a plan under different (e.g. ground-truth) terms.
    """
    cat = CATEGORY_BY_KEY.get(cat_key)
    if cat is None or len(members) < 2:
        return None
    t_s = s.terms
    triggered = s.entity == p.acquired_entity
    if triggered and t_s.change_of_control == "vendor_termination_right":
        return None  # the vendor can walk on the closing change of control - never consolidate onto it
    others = [m for m in members if m.cid != s.cid]
    if any(exits.get(m.cid) is None for m in members):
        return None
    legal_q, steps = 0, []
    cross = [m for m in others if m.entity != s.entity]
    if cross:
        aff = t_s.affiliate_purchasing
        if aff == "consent_required":
            legal_q = max(legal_q, 1)
            steps.append(f"Obtain {s.vendor_display or 'vendor'} consent for affiliate purchasing under {s.cid}")
        elif aff in ("prohibited", "silent", None):
            legal_q = max(legal_q, 2)
            steps.append(f"Negotiate an amendment to {s.cid} granting affiliate purchasing and volume aggregation "
                         f"({'prohibited' if aff == 'prohibited' else 'silent'} today)")
    if triggered and t_s.change_of_control == "consent_required":
        legal_q = max(legal_q, 1)
        steps.append(f"Obtain change-of-control consent on {s.cid} (acquired entity)")
    same_vendor_all = all(m.vendor_cluster == s.vendor_cluster for m in others)
    mig_q = 0 if same_vendor_all else cat.mig_q
    pooled_units = sum(m.units for m in members)
    baseline = sum(exits[m.cid].annual for m in members)
    pooled = annual_cost(t_s, pooled_units, p.asof)
    saving = baseline - pooled
    if saving <= 0:
        return None
    if not ideal:
        q_min = max(max(min(exits[m.cid].q_nat, exits[m.cid].q_tfc if exits[m.cid].q_tfc is not None else 10 ** 6)
                        for m in others), legal_q)
        if t < max(q_min, mig_q):
            return None
    fees = 0.0
    if not ideal:
        for m in others:
            c = _exit_cost(exits[m.cid], t, p)
            if c is None:
                return None
            fees += c
    sw = 0.0
    load = 0.0
    for m in others:
        f = p.same_vendor_switch_factor if m.vendor_cluster == s.vendor_cluster else 1.0
        sw += cat.switch_pct * exits[m.cid].annual * f
        load += p.paper_load if m.vendor_cluster == s.vendor_cluster else 1.0
    npv = _npv(saving, fees + sw, t, p)
    if npv <= 0:
        return None
    ostep = list(steps)
    for m in others:
        e = exits[m.cid]
        if t >= e.q_nat:
            if e.status == "open" and e.notice_deadline:
                ostep.append(f"Send non-renewal notice for {m.cid} by {e.notice_deadline}")
            elif e.status == "locked":
                ostep.append(f"{m.cid} is locked in until {e.natural_exit}; give notice before the next deadline")
        else:
            ostep.append(f"Exercise termination-for-convenience on {m.cid} (notice {m.terms.tfc_notice_days}d, fee ${_exit_cost(e, t, p) or 0:,.0f})")
    kind = "paper" if len({m.vendor_cluster for m in members}) == 1 else "consolidate"
    return Option(
        oid=f"{cluster}|{s.cid}|q{t}", kind=kind, cluster=cluster, category=cat_key, survivor=s.cid,
        members=[m.cid for m in members], switch_q=t, annual_baseline=baseline, annual_pooled=pooled,
        annual_saving=saving, exit_fees=fees, switching_cost=sw, npv=npv, load=min(load, p.capacity_per_quarter),
        stream=cat.stream, steps=ostep, provisional=any(m.provisional for m in members), pooled_units=pooled_units)


def _cluster_options(cluster: str, cat_key: str, members: list[ContractRecord], p: PlanParams, ideal: bool) -> list[Option]:
    if CATEGORY_BY_KEY.get(cat_key) is None or len(members) < 2:
        return []
    units = {m.terms.pricing.unit for m in members if m.terms.pricing}
    if len(units) != 1:  # non-comparable specs: never pool across different units
        return []
    exits = {m.cid: _exit_info(m, p) for m in members}
    if any(v is None for v in exits.values()):
        return []
    out: list[Option] = []
    for s in members:
        for t in range(p.horizon_quarters):
            o = _price_option(cluster, cat_key, members, s, t, p, exits, ideal)
            if o is not None:
                out.append(o)
    return out


def reprice_option(o: Option, records: dict[str, ContractRecord], p: PlanParams) -> Option | None:
    """Re-evaluate a previously chosen option against another set of records (e.g. ground truth).

    None means the action is infeasible or value-destroying under those records - an *invalid action*.
    """
    members = [records[c] for c in o.members if c in records]
    if len(members) != len(o.members) or o.survivor not in records:
        return None
    units = {m.terms.pricing.unit for m in members if m.terms.pricing}
    if len(units) != 1:
        return None
    exits = {m.cid: _exit_info(m, p) for m in members}
    return _price_option(o.cluster, o.category, members, records[o.survivor], o.switch_q, p, exits)


def generate_options(records: list[ContractRecord], p: PlanParams, ideal: bool = False) -> list[Option]:
    by_cat: dict[str, list[ContractRecord]] = {}
    for r in records:
        if r.terms.pricing is None or r.units <= 0 or not r.category:
            continue
        by_cat.setdefault(r.category, []).append(r)
    options: list[Option] = []
    for cat, rs in sorted(by_cat.items()):
        options += _cluster_options(cat, cat, rs, p, ideal)
        by_v: dict[str, list[ContractRecord]] = {}
        for r in rs:
            by_v.setdefault(r.vendor_cluster, []).append(r)
        for v, vs in sorted(by_v.items()):
            if 2 <= len(vs) < len(rs):
                options += _cluster_options(f"{cat}/{v}", cat, vs, p, ideal)
    return options
