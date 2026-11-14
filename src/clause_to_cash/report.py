"""Build the self-contained HTML dashboard (``clause-to-cash demo``).

Two scenarios are precomputed so the page can show what the human-in-the-loop is worth:

* ``before``  - only fully verified contracts reach the planner (review queue still open)
* ``after``   - a simulated reviewer has resolved every flagged field (oracle = ground truth; synthetic corpus only)

The page is one file: data embedded as JSON, vanilla JS, no network access, light/dark aware.
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any, Optional

from .corpus import Corpus
from .engine.model import QUARTER_DAYS
from .pipeline import RunResult, run_pipeline
from .pricing import annual_cost
from .schema import CRITICAL_FIELDS, FIELD_LIST, FIELDS
from .taxonomy import CATEGORY_BY_KEY
from .textutil import fold

TEMPLATE = Path(__file__).parent / "web" / "dashboard.html"


def fmt_value(name: str, v: Any) -> str:
    if v is None:
        return "—"
    if isinstance(v, bool):
        return "yes" if v else "no"
    if name == "nonrenewal_notice" and isinstance(v, dict):
        return f"{v['amount']} {v['unit']}"
    if name == "pricing" and isinstance(v, dict):
        tiers = v.get("tiers", [])
        parts = []
        for t in tiers:
            hi = "∞" if t.get("upper") is None else f"{t['upper']:,.0f}"
            parts.append(f"{t['lower']:,.0f}–{hi}: ${t['unit_price']:,.4g}")
        return f"{v.get('tier_mode', '?')} per {v.get('unit', '?')} · " + " | ".join(parts)
    if name == "min_annual_commit_usd":
        return f"${v:,.0f}"
    if name in ("early_termination_fee_pct", "annual_escalator_pct"):
        return f"{v:g}%"
    if name in ("initial_term_months", "renewal_term_months"):
        return f"{v} months"
    if name == "tfc_notice_days":
        return f"{v} days"
    return str(v)


def quarter_label(q: int, asof: dt.date) -> str:
    d = asof + dt.timedelta(days=int(q * QUARTER_DAYS))
    return d.strftime("%b %Y")


def _target_share(q: int) -> float:
    """Skydance's stated phasing (30% in year one, 70% through year two, 100% by year three), piecewise-linear from today."""
    if q <= 3:
        return 0.30 * (q + 1) / 4
    if q <= 7:
        return 0.30 + 0.40 * (q - 3) / 4
    return min(1.0, 0.70 + 0.30 * (q - 7) / 4)


def scenario(run: RunResult) -> dict[str, Any]:
    p, asof = run.params, run.params.asof
    recs = {r.cid: r for r in run.records}
    spend = sum(annual_cost(r.terms, r.units, asof) for r in run.records)
    plan = run.plan
    final = plan.final_run_rate
    cal = []
    for e in run.calendar:
        cal.append({"cid": e.cid, "entity": e.entity, "vendor": e.vendor, "category": CATEGORY_BY_KEY[e.category].label if e.category in CATEGORY_BY_KEY else e.category,
                    "kind": e.kind, "due": e.due.isoformat(), "internal_due": e.internal_due.isoformat() if e.internal_due else None,
                    "days": e.days, "annual_cost": round(e.annual_cost), "at_stake": round(e.at_stake), "note": e.note, "provisional": e.provisional})
    next90 = sum(e.at_stake for e in run.calendar if e.kind == "notice_deadline" and e.days <= 90)
    n90 = sum(1 for e in run.calendar if e.kind == "notice_deadline" and e.days <= 90)
    chosen = []
    for o in plan.chosen:
        sv = recs[o.survivor]
        chosen.append({
            "oid": o.oid, "kind": o.kind, "category": CATEGORY_BY_KEY[o.category].label, "survivor": o.survivor,
            "survivor_vendor": sv.vendor_display, "members": [{"cid": c, "vendor": recs[c].vendor_display, "entity": recs[c].entity} for c in o.members],
            "q": o.switch_q, "when": quarter_label(o.switch_q, asof), "baseline": round(o.annual_baseline), "pooled": round(o.annual_pooled),
            "saving": round(o.annual_saving), "one_time": round(o.one_time), "npv": round(o.npv), "steps": o.steps, "provisional": o.provisional})
    qn = p.horizon_quarters
    return {
        "kpi": {
            "contracts": len(run.records), "excluded": len(run.excluded), "spend": round(spend), "run_rate": round(final),
            "run_rate_pct": round(100 * final / spend, 1) if spend else 0.0, "npv": round(plan.npv), "one_time": round(plan.one_time),
            "ideal_npv": round(plan.ideal_npv), "ideal_run_rate": round(plan.ideal_run_rate), "solver": plan.solver, "optimal": plan.optimal,
            "options": plan.n_options, "actions": len(plan.chosen), "y1_share": round(100 * plan.pct_by(3), 1), "y2_share": round(100 * plan.pct_by(7), 1),
            "next90_at_stake": round(next90), "next90_n": n90,
        },
        "series": {"labels": [quarter_label(q, asof) for q in range(qn)], "plan": [round(x) for x in plan.run_rate_by_q],
                   "ideal": [round(x) for x in plan.ideal_run_rate_by_q], "target": [round(final * _target_share(q)) for q in range(qn)]},
        "calendar": cal, "plan": chosen,
        "risks": [{"cid": r.cid, "vendor": recs[r.cid].vendor_display if r.cid in recs else "", "code": r.code, "severity": r.severity,
                   "message": r.message, "exposure": round(r.exposure_usd)} for r in sorted(run.risks, key=lambda r: ({"high": 0, "medium": 1, "low": 2}[r.severity], -r.exposure_usd))],
    }


def doc_payload(d: Any, reviewed: list[str]) -> dict[str, Any]:
    text = d.item.text
    ftext = fold(text)
    assert len(ftext) == len(text), "fold() must be length-preserving for span offsets"
    fields = []
    for spec in FIELD_LIST:
        a = d.assessment.fields[spec.name]
        spans = [[s, e] for s, e in a.spans]
        fields.append({"name": spec.name, "critical": spec.critical, "value": fmt_value(spec.name, a.value), "status": a.status,
                       "reasons": a.reasons, "conf": round(a.confidence, 2), "spans": spans, "corrected": spec.name in reviewed})
    return {"id": d.item.doc_id, "entity": d.item.entity, "style": d.item.style, "text": text,
            "accepted": d.assessment.accepted, "flagged": d.assessment.review_fields, "fields": fields,
            "vendor": d.terms.vendor_party or "", "category": CATEGORY_BY_KEY[d.terms.service_category].label
            if d.terms.service_category in CATEGORY_BY_KEY else "unknown"}


def build_payload(before: RunResult, after: RunResult, results: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    has_gold_flag = after is not before
    reviewed = {d.item.doc_id: d.reviewed for d in after.docs}
    n_fields = len(before.docs) * len(CRITICAL_FIELDS)
    flagged = sum(len(d.assessment.review_fields) for d in before.docs)
    return {
        "meta": {"asof": before.params.asof.isoformat(), "closing": before.params.closing.isoformat(), "extractor": before.extractor,
                 "docs": len(before.docs), "has_gold": has_gold_flag, "docs_accepted": sum(1 for d in before.docs if d.assessment.accepted),
                 "fields_flagged": flagged, "fields_total": n_fields, "synthetic": all(d.item.gold is not None for d in before.docs),
                 "corrected_fields": sum(len(v) for v in reviewed.values())},
        "before": scenario(before), "after": scenario(after),
        "docs": [doc_payload(d, reviewed.get(d.item.doc_id, [])) for d in before.docs],
        "field_labels": {f.name: f.description.split(".")[0] for f in FIELD_LIST},
        "results": results,
    }


def render_dashboard(corpus: Corpus, extractor: Any, out: Path, results: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    before = run_pipeline(corpus, extractor, review="quarantine")
    cached = {d.item.doc_id: d.extraction for d in before.docs}
    has_gold = all(it.gold is not None for it in corpus.items)
    after = run_pipeline(corpus, extractor, review="oracle", cached=cached) if has_gold else before  # no simulated reviewer without ground truth
    payload = build_payload(before, after, results)
    blob = json.dumps(payload, separators=(",", ":")).replace("</", "<\\/")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(TEMPLATE.read_text().replace("/*__DATA__*/null", blob))
    return payload


__all__ = ["render_dashboard", "build_payload", "scenario", "fmt_value", "FIELDS"]
