import copy

import pytest

from clause_to_cash.schema import CRITICAL_FIELDS, Duration, Extraction, FieldExtraction, Pricing, Terms, Tier
from clause_to_cash.verify.faults import build_trials, config, run_trials, escapes
from clause_to_cash.verify.gate import GateConfig, assess
from clause_to_cash.verify.invariants import check_invariants
from clause_to_cash.evals.metrics import score_fields


def _clean_docs(corpus, extractions, n=None):
    out = []
    for it in corpus.items:
        e = extractions[it.doc_id]
        if all(score_fields(e, it.gold)[f] for f in CRITICAL_FIELDS):
            out.append((it, e))
    return out[:n] if n else out


def test_seen_style_docs_have_no_silent_errors(corpus, extractions):
    wrong_accepted = 0
    accepted_docs = 0
    for it in corpus.items:
        e = extractions[it.doc_id]
        a = assess(it.text, e)
        sc = score_fields(e, it.gold)
        for f in CRITICAL_FIELDS:
            if a.fields[f].status == "accept" and not sc[f]:
                wrong_accepted += 1
        accepted_docs += a.accepted
    assert wrong_accepted == 0
    assert accepted_docs >= 55  # the gate is not simply rejecting everything


def test_canary_blocks_unreadable_documents(corpus):
    it = next(i for i in corpus.items if i.style == 3)
    empty = Extraction(doc_id=it.doc_id, extractor="t")
    a = assess(it.text, empty)
    assert not a.accepted
    assert all("DOC_UNREADABLE" in a.fields[f].reasons for f in CRITICAL_FIELDS)
    # an extractor that says "no termination right / silent" on an unreadable doc must not be believed
    lying = Extraction(doc_id=it.doc_id, extractor="t", fields={
        "termination_for_convenience": FieldExtraction(value=False, quotes=[], confidence=0.95),
        "change_of_control": FieldExtraction(value="silent", quotes=[], confidence=0.95)})
    a2 = assess(it.text, lying)
    assert a2.fields["termination_for_convenience"].status == "review"
    assert a2.fields["change_of_control"].status == "review"


def test_each_layer_catches_its_fault(corpus, extractions):
    docs = _clean_docs(corpus, extractions)
    it, ext = next((i, e) for i, e in docs if i.gold.termination_for_convenience and i.gold.min_annual_commit_usd)
    base = assess(it.text, ext)
    assert base.accepted

    # grounding: invented quote
    e = ext.model_copy(deep=True)
    e.fields["initial_term_months"] = FieldExtraction(value=60, quotes=["The initial term shall be sixty (60) months."], confidence=0.95)
    assert "UNGROUNDED" in assess(it.text, e).fields["initial_term_months"].reasons

    # corroboration: real quote, wrong number
    e = ext.model_copy(deep=True)
    e.fields["min_annual_commit_usd"] = FieldExtraction(value=ext.fields["min_annual_commit_usd"].value * 10,
                                                        quotes=ext.fields["min_annual_commit_usd"].quotes, confidence=0.95)
    assert "CONTRADICTED" in assess(it.text, e).fields["min_annual_commit_usd"].reasons

    # cue guard: claims TfC does not exist although the document talks about it
    e = ext.model_copy(deep=True)
    e.fields["termination_for_convenience"] = FieldExtraction(value=False, quotes=[], confidence=0.95)
    assert "CUE_MISSED" in assess(it.text, e).fields["termination_for_convenience"].reasons

    # confidence floor
    e = ext.model_copy(deep=True)
    e.fields["effective_date"].confidence = 0.4
    assert "LOW_CONFIDENCE" in assess(it.text, e).fields["effective_date"].reasons
    assert assess(it.text, e, GateConfig(confidence=False)).fields["effective_date"].status == "accept"


def test_supersession_flags_base_only_evidence(corpus, extractions):
    from clause_to_cash.verify.faults import field_faults
    import random
    rng = random.Random(0)
    hits = 0
    for it, ext in _clean_docs(corpus, extractions):
        for f in ("initial_term_months", "nonrenewal_notice", "pricing"):
            for name, fe in field_faults(it, ext, f, rng):
                if name != "STALE_BASE_VALUE":
                    continue
                e = ext.model_copy(deep=True)
                e.fields[f] = fe
                assert assess(it.text, e).fields[f].status == "review"
                assert assess(it.text, e, GateConfig(supersession=False, corroboration=False, cues=False, invariants=False,
                                                    confidence=False, grounding=False)).fields[f].status == "accept"
                hits += 1
    assert hits >= 3


def test_invariants():
    ok = Terms(effective_date="2024-01-01", initial_term_months=36, auto_renew=True, renewal_term_months=12,
               nonrenewal_notice=Duration(amount=90, unit="days"),
               pricing=Pricing(unit="u", tier_mode="graduated", tiers=[Tier(lower=0, upper=10, unit_price=2), Tier(lower=10, upper=None, unit_price=1)]),
               service_category="cdn_delivery")
    assert [v.code for v in check_invariants(ok) if v.severity == "error"] == []
    bad = ok.model_copy(update={"nonrenewal_notice": None})
    assert "AUTO_RENEW_NO_NOTICE" in [v.code for v in check_invariants(bad)]
    gap = copy.deepcopy(ok)
    gap.pricing.tiers[1].lower = 11
    assert "TIER_GAP" in [v.code for v in check_invariants(gap)]
    swapped = ok.model_copy(update={"pricing": Pricing(unit="u", tier_mode="graduated", tiers=[
        Tier(lower=0, upper=10, unit_price=1), Tier(lower=10, upper=None, unit_price=2)])})
    assert "TIER_NOT_DISCOUNTED" in [v.code for v in check_invariants(swapped)]
    assert "NOTICE_EXCEEDS_TERM" in [v.code for v in check_invariants(ok.model_copy(update={
        "initial_term_months": 3, "nonrenewal_notice": Duration(amount=4, unit="months")}))]


def test_fault_injection_gate_beats_confidence_only(corpus, extractions):
    docs = _clean_docs(corpus, extractions, n=30)
    texts = {i.doc_id: i.text for i, _ in docs}
    trials = build_trials(docs, per_doc=25)
    assert len(trials) > 400
    full = run_trials(trials, texts, config())
    conf = run_trials(trials, texts, config(only="confidence"))
    caught = sum(v[0] for v in full.values()) / sum(v[1] for v in full.values())
    assert caught > 0.995
    assert sum(v[0] for v in conf.values()) == 0  # injected faults are confident, so a confidence floor alone catches none
    assert set(full) >= {"WRONG_VALUE", "FALSE_ABSENCE", "FABRICATED_QUOTE", "PHANTOM_CLAUSE"}


def test_every_layer_is_load_bearing(corpus, extractions):
    """Leave-one-out: removing grounding / corroboration / cues / supersession must let some fault through."""
    docs = _clean_docs(corpus, extractions)
    texts = {i.doc_id: i.text for i, _ in docs}
    trials = build_trials(docs, per_doc=40)
    base = len(escapes(trials, texts, config()))
    for layer in ("grounding", "corroboration", "cues", "supersession"):
        assert len(escapes(trials, texts, config(without=layer))) > base, layer
