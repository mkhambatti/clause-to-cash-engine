"""The verification gate: decides, per field and per document, auto-accept vs human review.

    LLM proposes  ->  code disposes.

Nothing an extractor returns is trusted.  A field is auto-accepted only if every enabled check
passes; each check can be switched off individually, which is how the layer-ablation study in
``evals`` measures what every check is worth.

Checks
------
grounding      the quoted evidence exists verbatim in the source document
corroboration  the quoted words actually support the claimed value (independent code path)
invariants     cross-field consistency (tiers contiguous, auto-renew has a notice period, ...)
cues           claims of absence ("no minimum commitment") need explicit negative evidence, or
               the document must contain no vocabulary of that clause at all
supersession   a value cited only from the base agreement is suspect if a later amendment
               discusses that same topic
confidence     extractor self-reported confidence above a floor
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..schema import CRITICAL_FIELDS, FIELD_LIST, FIELDS, Extraction, terms_from_values
from ..textutil import locate
from .corroborate import Corr, corroborate, corroborate_absence
from .cues import AMENDMENT_CUES, amendment_start, has_cue
from .invariants import Violation, check_invariants

SOFT_FIELDS = {"customer_party", "vendor_party", "service_category", "governing_law"}


@dataclass(frozen=True)
class GateConfig:
    conf_threshold: float = 0.70
    grounding: bool = True
    corroboration: bool = True
    invariants: bool = True
    cues: bool = True
    supersession: bool = True
    confidence: bool = True
    canary: bool = True  # doc-level: missing mandatory facts => distrust every 'absent' answer in the doc


@dataclass
class FieldAssessment:
    name: str
    value: Any
    status: str  # accept | review
    reasons: list[str] = field(default_factory=list)
    spans: list[tuple[int, int]] = field(default_factory=list)  # located evidence in the source doc
    corroboration: str = Corr.UNKNOWN.value
    confidence: float = 0.0


@dataclass
class DocAssessment:
    doc_id: str
    fields: dict[str, FieldAssessment]
    violations: list[Violation]

    @property
    def accepted(self) -> bool:
        return all(self.fields[f].status == "accept" for f in CRITICAL_FIELDS if f in self.fields)

    @property
    def review_fields(self) -> list[str]:
        return [n for n, a in self.fields.items() if a.status == "review" and FIELDS[n].critical]


def is_absence_claim(name: str, value: Any) -> bool:
    spec = FIELDS[name]
    if value is None:
        return True
    if spec.kind == "bool" and value is False:
        return True
    return spec.kind == "enum" and value == "silent"


def assess(text: str, ext: Extraction, cfg: GateConfig | None = None) -> DocAssessment:  # noqa: C901
    cfg = cfg or GateConfig()
    terms = terms_from_values({k: v.value for k, v in ext.fields.items()})
    violations = check_invariants(terms) if cfg.invariants else []
    inv_fields: dict[str, list[str]] = {}
    for v in violations:
        if v.severity == "error":
            for f in v.fields:
                inv_fields.setdefault(f, []).append(v.code)
    a_start = amendment_start(text) if cfg.supersession else None
    # Canary: an extractor that cannot find the mandatory facts (date, term, pricing) has demonstrably not
    # understood the document, so its answers of absence ("no termination right", "silent") are worthless too.
    unreadable = cfg.canary and cfg.invariants and any(v.code.startswith("MISSING_") for v in violations)

    out: dict[str, FieldAssessment] = {}
    for spec in FIELD_LIST:
        name = spec.name
        fe = ext.get(name)
        reasons: list[str] = []
        spans: list[tuple[int, int]] = []
        corr = Corr.UNKNOWN
        absence = is_absence_claim(name, fe.value)

        grounded_all = True
        for q in fe.quotes:
            loc = locate(text, q)
            if loc:
                spans.append(loc)
            else:
                grounded_all = False
        if fe.quotes and cfg.grounding and not grounded_all:
            reasons.append("UNGROUNDED")

        if fe.quotes and cfg.corroboration:
            corr = corroborate(name, fe.value, fe.quotes) if fe.value is not None else corroborate_absence(name, fe.quotes)
            if corr == Corr.CONTRADICTS:
                reasons.append("CONTRADICTED")

        if absence:
            explicit = bool(fe.quotes) and grounded_all and corr == Corr.SUPPORTS
            if cfg.cues and not explicit and has_cue(name, text):
                reasons.append("CUE_MISSED")
            if fe.value is None and not spec.nullable:
                reasons.append("NO_VALUE")
        else:
            if cfg.grounding and not fe.quotes:
                reasons.append("NO_EVIDENCE")
            elif cfg.corroboration and corr == Corr.UNKNOWN and name not in SOFT_FIELDS and fe.quotes:
                reasons.append("UNCORROBORATED")
            if cfg.cues and fe.value is not None and not has_cue(name, text) and name in {
                    "min_annual_commit_usd", "annual_escalator_pct", "early_termination_fee_pct"}:
                reasons.append("UNSUPPORTED_POSITIVE")

        if cfg.invariants and name in inv_fields:
            reasons.extend(f"INVARIANT:{c}" for c in inv_fields[name])

        if cfg.supersession and a_start is not None and name in AMENDMENT_CUES and spans:
            if all(s < a_start for s, _ in spans) and AMENDMENT_CUES[name].search(text[a_start:]):
                reasons.append("SUPERSEDED_RISK")

        if unreadable:
            reasons.append("DOC_UNREADABLE")

        if cfg.confidence and fe.confidence < cfg.conf_threshold:
            reasons.append("LOW_CONFIDENCE")

        out[name] = FieldAssessment(name, fe.value, "review" if reasons else "accept", sorted(set(reasons)),
                                    spans, corr.value, fe.confidence)
    return DocAssessment(ext.doc_id, out, violations)
