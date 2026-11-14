"""End-to-end pipeline:  extract -> verify -> resolve -> reason -> plan.

    LLM proposes (extract)  ->  code disposes (verify gate)  ->  humans review only what the gate flags
    ->  deterministic engines turn verified terms into dates, dollars and a sequenced plan.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from .corpus import Corpus, CorpusItem
from .engine.calendar import DeadlineEvent, build_calendar
from .engine.model import ContractRecord, PlanParams
from .engine.planner import Plan, plan as make_plan
from .engine.risks import RiskFlag, assess_risks
from .extract.base import Extractor
from .resolve.vendors import ERResult, resolve_vendors
from .schema import CRITICAL_FIELDS, FIELDS, Extraction, Terms, terms_from_values
from .verify.gate import DocAssessment, GateConfig, assess

ReviewMode = Literal["none", "quarantine", "oracle", "gold"]


@dataclass
class DocResult:
    item: CorpusItem
    extraction: Extraction
    assessment: DocAssessment
    terms: Terms  # what the engines will use
    reviewed: list[str] = field(default_factory=list)  # fields a (simulated) human corrected


@dataclass
class RunResult:
    docs: list[DocResult]
    records: list[ContractRecord]
    er: ERResult
    calendar: list[DeadlineEvent]
    risks: list[RiskFlag]
    plan: Plan
    params: PlanParams
    extractor: str
    review: str
    excluded: list[str] = field(default_factory=list)  # docs the engines could not use (unreadable / missing category)

    def by_id(self) -> dict[str, DocResult]:
        return {d.item.doc_id: d for d in self.docs}


def _merge_oracle(terms: Terms, gold: Terms, flagged: list[str]) -> tuple[Terms, list[str]]:
    vals = terms.model_dump(mode="json")
    g = gold.model_dump(mode="json")
    fixed = []
    for f in flagged:
        if vals.get(f) != g.get(f):
            fixed.append(f)
        vals[f] = g.get(f)
    return Terms.model_validate(vals), fixed


def run_pipeline(corpus: Corpus, extractor: Extractor | None, gate: GateConfig | None = None,
                 params: PlanParams | None = None, review: ReviewMode = "none", use_milp: bool = True,
                 cached: dict[str, Extraction] | None = None) -> RunResult:
    params = params or PlanParams()
    gate = gate or GateConfig()
    docs: list[DocResult] = []
    for it in corpus.items:
        if review == "gold":
            assert it.gold is not None
            vals = {k: v for k, v in it.gold.model_dump(mode="json").items()}
            ext = Extraction(doc_id=it.doc_id, extractor="gold")
            from .schema import FieldExtraction
            for k, v in vals.items():
                ext.fields[k] = FieldExtraction(value=v, quotes=[], confidence=1.0)
            asm = assess(it.text, ext, GateConfig(grounding=False, corroboration=False, invariants=False, cues=False,
                                                  supersession=False, confidence=False))
            docs.append(DocResult(it, ext, asm, it.gold, []))
            continue
        assert extractor is not None
        ext = (cached or {}).get(it.doc_id) or extractor.extract(it.doc_id, it.text)
        asm = assess(it.text, ext, gate)
        terms = terms_from_values({k: v.value for k, v in ext.fields.items()})
        reviewed: list[str] = []
        if review == "oracle" and it.gold is not None:
            flagged = [n for n, a in asm.fields.items() if a.status == "review"]
            terms, reviewed = _merge_oracle(terms, it.gold, flagged)
        docs.append(DocResult(it, ext, asm, terms, reviewed))

    # vendor resolution on the names the pipeline actually holds
    er = resolve_vendors([(d.item.doc_id, d.terms.vendor_party or d.item.doc_id, d.terms.service_category) for d in docs])
    records: list[ContractRecord] = []
    excluded: list[str] = []
    for d in docs:
        t = d.terms
        if review == "quarantine" and not d.assessment.accepted:
            excluded.append(d.item.doc_id)  # conservative: only fully verified contracts reach the planner
            continue
        if t.effective_date is None or not t.initial_term_months or t.pricing is None or not t.service_category:
            excluded.append(d.item.doc_id)
            continue
        still_flagged = [] if review in ("oracle", "gold") else d.assessment.review_fields
        records.append(ContractRecord(
            cid=d.item.doc_id, entity=d.item.entity, terms=t, units=d.item.units,
            vendor_cluster=er.cluster_of[d.item.doc_id], vendor_display=er.display[er.cluster_of[d.item.doc_id]],
            provisional=bool(still_flagged), review_fields=still_flagged))
    cal = build_calendar(records, params)
    risks = assess_risks(records, params)
    pl = make_plan(records, params, use_milp=use_milp)
    return RunResult(docs, records, er, cal, risks, pl, params, extractor.name if extractor else "gold", review, excluded)


__all__ = ["run_pipeline", "RunResult", "DocResult", "CRITICAL_FIELDS", "FIELDS"]
