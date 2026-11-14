"""Seeded generator for a two-company synthetic vendor-contract corpus with ground truth.

The universe: two legacy companies ("A" acquirer, "B" acquired) buy from overlapping sets of
fictional vendors across 30 service categories.  Contracts vary in term, renewal mechanics,
termination rights, commitments, tiered pricing (graduated vs all-units), affiliate rights,
change-of-control language, exclusivity and MFN, and ~1/3 carry amendments that change the
effective terms.  Four drafting styles (0-2 seen, 3 held-out) plus distractor clauses make
extraction non-trivial.  Everything is fictional; any resemblance to real vendors is coincidental.
"""
from __future__ import annotations

import datetime as dt
import random
from dataclasses import dataclass

from ..corpus import ASOF, Corpus, CorpusItem, sha
from ..pricing import cost
from ..schema import Duration, Pricing, Tier
from ..taxonomy import CATEGORIES, VENDOR_PREFIXES, Category
from dataclasses import replace as dc_replace

from .render import fprice, parse_price, render
from .spec import Amend, ContractSpec, effective_terms

CUSTOMERS = {
    "A": ["Meridian Pictures Corporation", "Meridian Streaming LLC", "Meridian Studios, Inc."],
    "B": ["Halyard Broadcasting Group, Inc.", "Halyard Digital Ventures LLC", "Halyard Networks Corporation"],
}
SUFFIXES = [", Inc.", " Inc.", ", LLC", " LLC", " Corporation", " Corp.", ", Ltd.", " Limited"]
LAWS = ["New York", "California", "Delaware", "England and Wales"]


@dataclass
class Vendor:
    vid: str
    base: str
    mult: float
    d1: float
    d2: float


def _round_price(x: float) -> float:
    return parse_price(fprice(x))


def _nice(x: float) -> int:
    if x < 100:
        return max(1, int(round(x)))
    mag = 10 ** (len(str(int(x))) - 2)
    return int(round(x / mag) * mag)


def make_pricing(cat: Category, v: Vendor, mode: str, base_units: float, factor: float = 1.0) -> Pricing:
    t1, t2 = _nice(0.45 * base_units), _nice(1.35 * base_units)
    if t2 <= t1:
        t2 = t1 * 2
    p1 = _round_price(cat.unit_price * v.mult * factor)
    p2 = _round_price(p1 * (1 - v.d1))
    p3 = _round_price(p2 * (1 - v.d2))
    return Pricing(unit=cat.unit, tier_mode=mode,  # type: ignore[arg-type]
                   tiers=[Tier(lower=0, upper=t1, unit_price=p1), Tier(lower=t1, upper=t2, unit_price=p2),
                          Tier(lower=t2, upper=None, unit_price=p3)])


def _vendors_for(rng: random.Random, cat: Category) -> list[Vendor]:
    prefixes = rng.sample(VENDOR_PREFIXES, 3)
    vs = [Vendor(f"V-{cat.key}-{i}", f"{p} {cat.noun}", rng.uniform(0.92, 1.10), rng.uniform(0.03, 0.08),
                 rng.uniform(0.03, 0.08)) for i, p in enumerate(prefixes)]
    if rng.random() < 0.34:  # decoy: same prefix, different company (hard negative for entity resolution)
        first = cat.noun.split()[0]
        vs.append(Vendor(f"V-{cat.key}-decoy", f"{prefixes[0]} {first} Analytics", rng.uniform(0.94, 1.10),
                         rng.uniform(0.03, 0.08), rng.uniform(0.03, 0.08)))
    return vs


def _surface(rng: random.Random, base: str, cat: Category) -> str:
    name = base
    if rng.random() < 0.10 and len(cat.noun.split()) > 1 and not base.endswith("Analytics"):
        name = " ".join(base.split()[:-1])  # short form: "Kestrel Edge"
    return name + rng.choice(SUFFIXES)


def _sample_dates(rng: random.Random, term: int, auto_renew: bool) -> dt.date:
    for _ in range(50):
        eff = dt.date(2020, 6, 1) + dt.timedelta(days=rng.randrange(0, 2200))
        if eff > dt.date(2026, 7, 31):
            continue
        end = eff + dt.timedelta(days=int(term * 30.4))
        if auto_renew or end >= ASOF - dt.timedelta(days=60) or rng.random() < 0.03:
            return eff
    return dt.date(2024, 1, 15)


def sample_spec(rng: random.Random, cid: str, entity: str, cat: Category, v: Vendor, scale: float,
                split: float, style: int) -> ContractSpec:
    mode = rng.choice(["graduated", "all_units"])
    base_units = cat.spend_a / (cat.unit_price * 0.93)
    pricing = make_pricing(cat, v, mode, base_units)
    term = rng.choices([12, 24, 36, 48, 60], [0.1, 0.25, 0.35, 0.15, 0.15])[0]
    auto = rng.random() < 0.72
    renewal = rng.choice([12, 12, 12, 24]) if auto else 0
    if rng.random() < 0.27:
        notice = Duration(amount=rng.choice([1, 2, 3, 6]), unit="months")
    else:
        notice = Duration(amount=rng.choice([30, 60, 90, 90, 120, 180]), unit="days")
    if not auto:
        notice_eff = None
    else:
        notice_eff = notice
    tfc = rng.random() < 0.36
    tfc_days = rng.choice([30, 60, 90, 180]) if tfc else None
    etf = float(rng.choice([25, 50, 50, 75, 100])) if tfc else None
    expected_units = base_units * scale * split * rng.uniform(0.9, 1.1)
    commit = None
    if rng.random() < 0.6:
        frac = rng.choice([0.6, 0.7, 0.8, 0.9])
        commit = float(max(10000, round(cost(pricing, expected_units) * frac, -4)))
    units = expected_units * rng.uniform(0.65, 1.2)
    esc = rng.choice([2.0, 3.0, 3.0, 3.5, 5.0]) if rng.random() < 0.5 else None
    affiliate = rng.choices(["allowed", "consent_required", "prohibited", "silent"], [0.35, 0.25, 0.15, 0.25])[0]
    coc = rng.choices(["free", "consent_required", "vendor_termination_right", "silent"], [0.30, 0.35, 0.15, 0.20])[0]
    excl = rng.random() < 0.12
    mfn = rng.random() < 0.25
    eff = _sample_dates(rng, term, auto)
    spec = ContractSpec(
        cid=cid, entity=entity, style=style, cat=cat, vendor_id=v.vid, vendor_name=_surface(rng, v.base, cat),
        customer_name=rng.choice(CUSTOMERS[entity]), eff=eff, term_months=term, auto_renew=auto,
        renewal_months=renewal, notice=notice_eff, tfc=tfc, tfc_days=tfc_days, etf_pct=etf, min_commit=commit,
        pricing=pricing, escalator=esc, affiliate=affiliate, coc=coc, exclusive=excl, mfn=mfn,
        law=rng.choice(LAWS), units=units, payment_days=rng.choice([30, 45, 60]),
        state=rng.choice(["Delaware", "New York", "California"]),
        explicit_negatives={k: rng.random() < 0.7 for k in ("tfc", "commit", "escalator", "exclusive", "mfn")})
    # amendments
    if eff <= dt.date(2025, 5, 1) and rng.random() < 0.40:
        kinds = ["extend", "reprice", "notice"] if auto else ["extend", "reprice"]
        for kind in rng.sample(kinds, k=1 if rng.random() < 0.7 else min(2, len(kinds))):
            lo = eff + dt.timedelta(days=180)
            hi = min(eff + dt.timedelta(days=int(term * 30.4 * 0.8)), ASOF - dt.timedelta(days=30))
            if hi <= lo:
                continue
            when = lo + dt.timedelta(days=rng.randrange(0, (hi - lo).days + 1))
            if kind == "extend":
                spec.amendments.append(Amend("extend", when, months=rng.choice([6, 12, 12, 24])))
            elif kind == "reprice":
                spec.amendments.append(Amend("reprice", when, pricing=make_pricing(cat, v, mode, base_units, rng.uniform(0.86, 0.97))))
            else:
                if rng.random() < 0.3:
                    nn = Duration(amount=rng.choice([1, 2, 3]), unit="months")
                else:
                    nn = Duration(amount=rng.choice([30, 45, 60, 90]), unit="days")
                spec.amendments.append(Amend("notice", when, notice=nn))
    return spec


def generate(seed: int = 7, style_weights: tuple[float, ...] = (0.25, 0.25, 0.25, 0.25)) -> Corpus:
    rng = random.Random(seed)
    items: list[CorpusItem] = []
    counters = {"A": 0, "B": 0}
    scale_b = rng.uniform(0.6, 1.0)
    for cat in CATEGORIES:
        vendors = _vendors_for(rng, cat)
        main = [v for v in vendors if not v.vid.endswith("decoy")]
        used_a: list[Vendor] = []
        for entity in ("A", "B"):
            n = rng.choices([0, 1, 2], [0.05, 0.50, 0.45])[0]
            scale = 1.0 if entity == "A" else scale_b * rng.uniform(0.9, 1.1)
            for k in range(n):
                if entity == "B" and used_a and rng.random() < 0.5:
                    v = rng.choice(used_a)
                else:
                    pool = vendors if rng.random() < 0.15 else main
                    v = rng.choice(pool)
                if entity == "A":
                    used_a.append(v)
                counters[entity] += 1
                cid = f"{entity}-{counters[entity]:04d}"
                split = 1.0 if n == 1 else (0.6 if k == 0 else 0.4)
                style = rng.choices([0, 1, 2, 3], style_weights)[0]
                spec = sample_spec(rng, cid, entity, cat, v, scale, split, style)
                text, spans = render(spec, rng)
                items.append(CorpusItem(cid, entity, style, text, sha(text), spec.units, effective_terms(spec), spans, v.vid,
                                    effective_terms(dc_replace(spec, amendments=[]))))
    items.sort(key=lambda i: (i.entity, i.doc_id))
    meta = {"seed": seed, "asof": ASOF.isoformat(), "n_contracts": len(items), "synthetic": True,
            "note": "All vendors, customers and contracts are fictional."}
    return Corpus(items, meta)
