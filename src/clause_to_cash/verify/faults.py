"""Fault injection for the verification gate ("mutation testing for guardrails").

Question answered: *when an extractor is wrong in a realistic way, does the gate notice?*

Method.  Take extractions that are fully correct and carry genuine, grounded quotes (the rules
baseline on documents it handles).  Corrupt exactly ONE critical field per trial in one of eight
ways that mirror documented LLM failure modes, give the corrupted field a high self-reported
confidence (LLMs are not humble when wrong), and ask whether the gate routes that field to review.
A fault that is *not* caught is an error that would flow silently into the savings plan.

Every trial is deterministic (seeded), so the table in docs/EVALUATION.md is reproducible.
"""
from __future__ import annotations

import copy
import datetime as dt
import random
from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Callable, Optional

from ..corpus import CorpusItem
from ..schema import CRITICAL_FIELDS, FIELDS, AFFILIATE_VALUES, CONTROL_VALUES, Extraction, FieldExtraction
from ..taxonomy import CATEGORIES
from ..textutil import locate
from .cues import amendment_start
from .gate import GateConfig, assess, is_absence_claim

FAULTS: dict[str, str] = {
    "WRONG_VALUE": "value misread (number, date, enum, boolean flipped); the genuine quote is kept",
    "WRONG_CLAUSE": "value misread and 'supported' by a real quote copied from a different clause",
    "FABRICATED_QUOTE": "value misread and 'supported' by an invented quote that appears nowhere in the document",
    "NO_EVIDENCE": "value misread and no evidence offered at all",
    "FALSE_ABSENCE": "a clause that exists is reported as absent / silent / false (the silent miss)",
    "PHANTOM_CLAUSE": "a clause that does not exist is reported as present, citing an unrelated real quote",
    "STALE_BASE_VALUE": "value taken from the base agreement although a later amendment overrides it",
    "BROKEN_STRUCTURE": "internally impossible extraction (pricing tiers gapped, notice > term), quotes kept",
}


def _present(name: str, v: Any) -> bool:
    return not is_absence_claim(name, v)


def _perturb(name: str, v: Any, rng: random.Random) -> Any:  # noqa: C901
    spec = FIELDS[name]
    if name == "effective_date":
        d = dt.date.fromisoformat(v)
        if d.day <= 12 and d.day != d.month:
            return dt.date(d.year, d.day, d.month).isoformat()  # classic day/month swap
        return dt.date(d.year - 1, d.month, min(d.day, 28)).isoformat()
    if name == "service_category":
        return rng.choice([c.key for c in CATEGORIES if c.key != v])
    if name == "initial_term_months":
        return int(v) + 12
    if name == "nonrenewal_notice":
        return {"amount": v["amount"], "unit": "days" if v["unit"] == "months" else "months"}  # unit confusion
    if name == "tfc_notice_days":
        return int(v) * 2
    if name == "early_termination_fee_pct":
        return float(v) / 2 if v > 25 else float(v) * 2
    if name == "min_annual_commit_usd":
        return float(v) * 10
    if name == "annual_escalator_pct":
        return float(v) + 2.0
    if name == "pricing":
        p = copy.deepcopy(v)
        if rng.random() < 0.5:
            p["tier_mode"] = "all_units" if p["tier_mode"] == "graduated" else "graduated"
        else:
            p["tiers"][0]["unit_price"] = round(p["tiers"][0]["unit_price"] * 1.1, 6)
        return p
    if spec.kind == "bool":
        return not v
    if name == "affiliate_purchasing":
        return {"allowed": "prohibited", "prohibited": "allowed", "consent_required": "allowed", "silent": "allowed"}[v]
    if name == "change_of_control":
        return {"free": "vendor_termination_right", "consent_required": "free",
                "vendor_termination_right": "free", "silent": "free"}[v]
    raise ValueError(name)


def _absent_value(name: str) -> Any:
    spec = FIELDS[name]
    if spec.kind == "bool":
        return False
    if spec.kind == "enum":
        return "silent"
    return None


def _phantom_value(name: str) -> Any:
    return {"min_annual_commit_usd": 1_000_000.0, "early_termination_fee_pct": 50.0, "annual_escalator_pct": 3.0,
            "termination_for_convenience": True, "exclusive_requirements": True, "nonrenewal_notice": {"amount": 90, "unit": "days"},
            "affiliate_purchasing": "allowed", "change_of_control": "free"}.get(name, True)


def _fabricated_quote(name: str, v: Any) -> str:
    """A confident, plausible sentence that is simply not in the document."""
    if name == "effective_date":
        return f"This Agreement is effective as of {v}."
    if name == "initial_term_months":
        return f"The initial term of this Agreement shall be {v} months from the Effective Date."
    if name == "nonrenewal_notice":
        return f"Either party may prevent renewal by giving {v['amount']} {v['unit']} written notice."
    if name == "min_annual_commit_usd":
        return f"Customer shall purchase at least ${v:,.0f} of Services each contract year."
    if name == "early_termination_fee_pct":
        return f"Customer shall pay an early termination fee equal to {v:g}% of remaining fees."
    return f"The parties agree that {name.replace('_', ' ')} is {v}."


@dataclass
class Trial:
    fault: str
    doc_id: str
    field: str
    extraction: Extraction


def _clone(ext: Extraction) -> Extraction:
    return ext.model_copy(deep=True)


def field_faults(it: CorpusItem, ext: Extraction, f: str, rng: random.Random) -> list[tuple[str, FieldExtraction]]:  # noqa: C901
    """Every applicable corruption of field ``f`` of a clean extraction ``ext`` (deterministic given ``rng``)."""
    assert it.gold is not None
    g = it.gold.model_dump(mode="json")
    fe = ext.get(f)
    val = g.get(f)
    conf = 0.95
    out: list[tuple[str, FieldExtraction]] = []
    others = {k: ext.get(k).quotes for k in CRITICAL_FIELDS if ext.get(k).quotes}
    cand_other = [q for k, qs in others.items() if k != f for q in qs]
    if f != "service_category" and _present(f, val) and fe.quotes:
        try:
            bad = _perturb(f, val, rng)
        except Exception:
            bad = None
        if bad is not None and bad != val:
            out.append(("WRONG_VALUE", FieldExtraction(value=bad, quotes=list(fe.quotes), confidence=conf)))
            if cand_other:
                out.append(("WRONG_CLAUSE", FieldExtraction(value=bad, quotes=[rng.choice(cand_other)], confidence=conf)))
            if f in ("effective_date", "initial_term_months", "nonrenewal_notice", "min_annual_commit_usd", "early_termination_fee_pct"):
                out.append(("FABRICATED_QUOTE", FieldExtraction(value=bad, quotes=[_fabricated_quote(f, bad)], confidence=conf)))
            out.append(("NO_EVIDENCE", FieldExtraction(value=bad, quotes=[], confidence=conf)))
        if _absent_value(f) != val and f not in ("effective_date", "initial_term_months", "pricing"):
            out.append(("FALSE_ABSENCE", FieldExtraction(value=_absent_value(f), quotes=[], confidence=0.9)))
    if f != "service_category" and not _present(f, val) and cand_other and f in (
            "min_annual_commit_usd", "early_termination_fee_pct", "termination_for_convenience", "exclusive_requirements",
            "nonrenewal_notice", "affiliate_purchasing", "change_of_control"):
        out.append(("PHANTOM_CLAUSE", FieldExtraction(value=_phantom_value(f), quotes=[rng.choice(cand_other)], confidence=conf)))
    a_start = amendment_start(it.text)
    if it.base_gold is not None and a_start is not None and f in ("initial_term_months", "nonrenewal_notice", "pricing"):
        b = it.base_gold.model_dump(mode="json")
        if b.get(f) != g.get(f) and b.get(f) is not None:
            base_q = [q for q in fe.quotes if (loc := locate(it.text, q)) and loc[0] < a_start]
            if base_q:
                out.append(("STALE_BASE_VALUE", FieldExtraction(value=b[f], quotes=base_q, confidence=conf)))
    if f == "pricing" and val and fe.quotes and len(val["tiers"]) >= 2:
        p2 = copy.deepcopy(val)
        p2["tiers"][1]["lower"] = p2["tiers"][1]["lower"] + 1.0  # gap between tier 1 upper and tier 2 lower
        out.append(("BROKEN_STRUCTURE", FieldExtraction(value=p2, quotes=list(fe.quotes), confidence=conf)))
    if f == "nonrenewal_notice" and val and fe.quotes:
        out.append(("BROKEN_STRUCTURE", FieldExtraction(value={"amount": 400, "unit": "days"}, quotes=list(fe.quotes), confidence=conf)))
    return out


def build_trials(items: list[tuple[CorpusItem, Extraction]], seed: int = 0, per_doc: int = 40) -> list[Trial]:
    """Deterministically derive fault trials from *clean* extractions (all critical fields correct)."""
    rng = random.Random(seed)
    out: list[Trial] = []
    for it, ext in items:
        for f in CRITICAL_FIELDS:
            for fault, fe in field_faults(it, ext, f, rng):
                e = _clone(ext)
                e.fields[f] = fe
                out.append(Trial(fault, it.doc_id, f, e))
    by_doc: dict[str, list[Trial]] = defaultdict(list)
    for t in out:
        by_doc[t.doc_id].append(t)
    capped: list[Trial] = []
    for doc, ts in by_doc.items():
        if len(ts) > per_doc:
            ts = rng.sample(ts, per_doc)
        capped += ts
    return capped


class NoisyExtractor:
    """Simulates an imperfect (LLM-like) extractor: a clean base extractor whose critical fields are corrupted
    with probability ``rate`` each, using the same fault catalogue, always with high self-reported confidence.

    Only used in evaluation (needs ground truth to know what a 'clean' answer looks like)."""

    def __init__(self, base: Any, items: dict[str, CorpusItem], rate: float, seed: int = 0) -> None:
        self.base, self.items, self.rate, self.seed = base, items, rate, seed
        self.name = f"noisy({getattr(base, 'name', 'base')},{rate:g})"
        self.injected: list[tuple[str, str, str]] = []  # (doc_id, field, fault)

    def extract(self, doc_id: str, text: str) -> Extraction:
        ext = self.base.extract(doc_id, text)
        it = self.items[doc_id]
        if it.gold is None:
            return ext
        from ..evals.metrics import score_fields
        sc = score_fields(ext, it.gold)
        if not all(sc[f] for f in CRITICAL_FIELDS):
            return ext  # base already wrong here; do not stack faults
        rng = random.Random(f"{self.seed}:{doc_id}")
        out = _clone(ext)
        for f in CRITICAL_FIELDS:
            if rng.random() >= self.rate:
                continue
            cands = field_faults(it, ext, f, rng)
            if cands:
                fault, fe = rng.choice(cands)
                out.fields[f] = fe
                self.injected.append((doc_id, f, fault))
        return out


GateFactory = Callable[[], GateConfig]

LAYERS = ("grounding", "corroboration", "invariants", "cues", "supersession", "confidence")


def all_off() -> dict[str, bool]:
    return {k: False for k in LAYERS}


def config(only: Optional[str] = None, without: Optional[str] = None) -> GateConfig:
    kw = {k: True for k in LAYERS}
    if only:
        kw = all_off()
        kw[only] = True
    if without:
        kw[without] = False
    return GateConfig(**kw)


def run_trials(trials: list[Trial], texts: dict[str, str], cfg: GateConfig) -> dict[str, tuple[int, int]]:
    """fault -> (caught, total) for one gate configuration."""
    res: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for t in trials:
        a = assess(texts[t.doc_id], t.extraction, cfg)
        caught = a.fields[t.field].status == "review"
        res[t.fault][0] += int(caught)
        res[t.fault][1] += 1
    return {k: (v[0], v[1]) for k, v in res.items()}


def escapes(trials: list[Trial], texts: dict[str, str], cfg: GateConfig) -> list[Trial]:
    return [t for t in trials if assess(texts[t.doc_id], t.extraction, cfg).fields[t.field].status == "accept"]
