"""Query layer over a pipeline run - used by the MCP server (agent-facing) and the CLI.

Design rule for anything an *agent* can call: never answer a question whose inputs have not been verified.
If a field that the answer depends on is still in the review queue, the tool returns ``safe_to_act: false``
with the reasons instead of a confident number.
"""
from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any, Optional

from .corpus import Corpus, load_corpus
from .dates import lifecycle
from .engine.model import PlanParams
from .extract.base import Extractor
from .extract.rules import RuleExtractor
from .pipeline import DocResult, RunResult, run_pipeline
from .pricing import annual_cost
from .report import fmt_value
from .schema import CRITICAL_FIELDS
from .synth.generate import generate
from .textutil import locate

NOTICE_FIELDS = ("effective_date", "initial_term_months", "auto_renew", "nonrenewal_notice")


def default_corpus(path: Optional[str] = None) -> Corpus:
    if path:
        return load_corpus(Path(path))
    shipped = Path(__file__).resolve().parents[2] / "data" / "corpus"
    return load_corpus(shipped) if (shipped / "manifest.json").exists() else generate(7)


class Workspace:
    def __init__(self, corpus: Corpus, extractor: Optional[Extractor] = None, review: str = "quarantine",
                 params: Optional[PlanParams] = None) -> None:
        self.corpus = corpus
        self.extractor = extractor or RuleExtractor()
        self.params = params or PlanParams()
        # 'quarantine' keeps unverified contracts out of the plan; every query still reports them as needing review
        self.run: RunResult = run_pipeline(corpus, self.extractor, params=self.params, review=review)  # type: ignore[arg-type]
        self.docs = self.run.by_id()
        self.records = {r.cid: r for r in self.run.records}

    # ---- helpers -----------------------------------------------------------------------------------------------------------
    def _doc(self, cid: str) -> DocResult:
        if cid not in self.docs:
            raise KeyError(f"unknown contract {cid!r}")
        return self.docs[cid]

    def _gate(self, d: DocResult, fields: tuple[str, ...]) -> dict[str, Any]:
        bad = {f: d.assessment.fields[f].reasons for f in fields if d.assessment.fields[f].status != "accept"}
        return {"safe_to_act": not bad, "needs_review": bad}

    # ---- tools ---------------------------------------------------------------------------------------------------------------
    def list_deadlines(self, within_days: int = 90, min_at_stake: float = 0) -> dict[str, Any]:
        ev = [e for e in self.run.calendar if e.days <= within_days and e.at_stake >= min_at_stake]
        return {"as_of": self.params.asof.isoformat(), "count": len(ev),
                "total_at_stake_usd": round(sum(e.at_stake for e in ev)),
                "events": [{"contract": e.cid, "vendor": e.vendor, "kind": e.kind, "due": e.due.isoformat(),
                            "internal_due": e.internal_due.isoformat() if e.internal_due else None, "days_left": e.days,
                            "at_stake_usd": round(e.at_stake), "note": e.note} for e in ev],
                "excluded_unverified_contracts": self.run.excluded}

    def get_contract(self, contract_id: str) -> dict[str, Any]:
        d = self._doc(contract_id)
        out_fields = {}
        for name, a in d.assessment.fields.items():
            ev = [d.item.text[s:e] for s, e in a.spans][:2]
            out_fields[name] = {"value": fmt_value(name, a.value), "status": a.status, "reasons": a.reasons, "evidence": ev}
        return {"contract": contract_id, "entity": d.item.entity, "verified": d.assessment.accepted,
                "fields_needing_review": d.assessment.review_fields, "fields": out_fields}

    def check_notice(self, contract_id: str, send_on: Optional[str] = None) -> dict[str, Any]:
        d = self._doc(contract_id)
        gate = self._gate(d, NOTICE_FIELDS)
        if not gate["safe_to_act"]:
            return {"contract": contract_id, **gate, "verdict": "REFUSED",
                    "message": "Notice-relevant fields are not verified; a human must review them before any date is relied on."}
        lc = lifecycle(d.terms, self.params.asof, self.params.buffer_days)
        when = dt.date.fromisoformat(send_on) if send_on else self.params.asof
        if lc.status not in ("open", "locked") or lc.notice_deadline is None:
            return {"contract": contract_id, **gate, "verdict": "NOT_APPLICABLE", "lifecycle": lc.status,
                    "message": "No auto-renewal notice applies (fixed term, expired, or unknown)."}
        timely = when <= lc.notice_deadline
        dl, ws_ = lc.notice_deadline.isoformat(), when.isoformat()
        if lc.status == "open":
            msg = (f"Notice sent {ws_} meets the {dl} deadline for the current term; the contract will not renew."
                   if timely else
                   f"Notice sent {ws_} misses the {dl} deadline for the current term; the contract renews for another term.")
        else:
            msg = (f"The deadline for the current term has passed (the contract is locked in). Notice sent {ws_} is in time for the NEXT term (deadline {dl})."
                   if timely else
                   f"The deadline for the current term has passed (the contract is locked in), and {ws_} also misses the next deadline, {dl}.")
        return {"contract": contract_id, **gate, "lifecycle": lc.status, "notice_deadline": dl,
                "internal_deadline": lc.internal_deadline.isoformat() if lc.internal_deadline else None,
                "send_on": ws_, "verdict": "TIMELY" if timely else "TOO_LATE", "message": msg,
                "annual_cost_usd": round(annual_cost(d.terms, d.item.units, self.params.asof))}

    def review_queue(self, limit: int = 25) -> dict[str, Any]:
        items = []
        for d in self.run.docs:
            if d.assessment.accepted:
                continue
            items.append({"contract": d.item.doc_id, "fields": {f: d.assessment.fields[f].reasons for f in d.assessment.review_fields}})
        return {"count": len(items), "items": items[:limit]}

    def savings_plan(self, limit: int = 15) -> dict[str, Any]:
        pl = self.run.plan
        return {"solver": pl.solver, "optimal": pl.optimal, "run_rate_usd": round(pl.final_run_rate), "npv_usd": round(pl.npv),
                "one_time_usd": round(pl.one_time), "contracts_in_plan": len(self.run.records),
                "unverified_contracts_excluded": len(self.run.excluded),
                "actions": [{"when_quarter": o.switch_q, "category": o.category, "consolidate_onto": o.survivor,
                             "contracts": o.members, "annual_saving_usd": round(o.annual_saving), "one_time_usd": round(o.one_time),
                             "npv_usd": round(o.npv), "steps": o.steps} for o in pl.chosen[:limit]]}


def evidence_ok(d: DocResult) -> bool:  # used by tests
    return all(locate(d.item.text, q) for fe in d.extraction.fields.values() for q in fe.quotes) if d.extraction else True


__all__ = ["Workspace", "default_corpus", "CRITICAL_FIELDS"]
