import tempfile
from pathlib import Path

from clause_to_cash.corpus import load_corpus, save_corpus
from clause_to_cash.evals.metrics import score_fields, values_equal
from clause_to_cash.schema import CRITICAL_FIELDS, FIELD_LIST
from clause_to_cash.synth.generate import generate
from clause_to_cash.textutil import locate


def test_generation_is_deterministic():
    a, b = generate(11), generate(11)
    assert [i.sha256 for i in a.items] == [i.sha256 for i in b.items]
    assert [i.sha256 for i in generate(12).items] != [i.sha256 for i in a.items]


def test_corpus_shape(corpus):
    assert len(corpus.items) >= 70
    assert {i.style for i in corpus.items} == {0, 1, 2, 3}
    assert {i.entity for i in corpus.items} == {"A", "B"}
    assert any(i.base_gold != i.gold for i in corpus.items), "some contracts must be amended"


def test_gold_spans_exist_in_text(corpus):
    for it in corpus.items:
        for name, spans in it.spans.items():
            for a, b in spans:
                assert 0 <= a < b <= len(it.text), (it.doc_id, name)


def test_roundtrip_to_disk(corpus):
    with tempfile.TemporaryDirectory() as d:
        save_corpus(corpus, Path(d))
        back = load_corpus(Path(d))
    assert [i.sha256 for i in back.items] == [i.sha256 for i in corpus.items]
    assert back.items[0].gold == corpus.items[0].gold and back.items[0].base_gold == corpus.items[0].base_gold


def test_locate_tolerates_hyphenation_and_whitespace():
    doc = "The fee is five million-\nmessages per  month, billed monthly."
    assert locate(doc, "five million-messages per month") is not None
    assert locate(doc, "ten million messages") is None


def test_rules_baseline_on_seen_styles_is_accurate(corpus, extractions):
    ok = tot = 0
    for it in corpus.items:
        if it.style == 3:
            continue
        sc = score_fields(extractions[it.doc_id], it.gold)
        for f in CRITICAL_FIELDS:
            tot += 1
            ok += sc[f]
    assert ok / tot > 0.99


def test_rules_baseline_does_not_pretend_on_held_out_style(corpus, extractions):
    """The held-out style must stay genuinely hard for the baseline, or the held-out split means nothing."""
    ok = tot = 0
    for it in corpus.items:
        if it.style != 3:
            continue
        sc = score_fields(extractions[it.doc_id], it.gold)
        for f in CRITICAL_FIELDS:
            tot += 1
            ok += sc[f]
    assert ok / tot < 0.6


def test_every_extracted_quote_is_in_the_document(corpus, extractions):
    for it in corpus.items:
        for f in FIELD_LIST:
            for q in extractions[it.doc_id].get(f.name).quotes:
                assert locate(it.text, q), (it.doc_id, f.name, q[:60])


def test_values_equal_semantics():
    assert values_equal("early_termination_fee_pct", 50, 50.0)
    assert not values_equal("nonrenewal_notice", {"amount": 3, "unit": "months"}, {"amount": 90, "unit": "days"})
    assert values_equal("service_category", None, None) and not values_equal("service_category", "a", None)


def test_shipped_corpus_matches_generator():
    """data/corpus must be exactly what `generate --seed 7` produces (no hand edits, reproducible)."""
    shipped = load_corpus(Path(__file__).resolve().parents[1] / "data" / "corpus")
    fresh = generate(7)
    assert [(i.doc_id, i.sha256) for i in shipped.items] == [(i.doc_id, i.sha256) for i in fresh.items]
    assert all(a.gold == b.gold and a.base_gold == b.base_gold for a, b in zip(shipped.items, fresh.items))
