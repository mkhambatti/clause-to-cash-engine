"""Reproduce the worked example on the project page: one contract, three confident-but-wrong extractions.

    PYTHONPATH=src python scripts/worked_example.py [A-0015]

Starts from the rules extractor's (correct) reading of the contract, corrupts one field at a time with the same
fault catalogue the evaluation uses, and prints what the verification gate decides. No network, no API key.
"""
import random
import sys
from pathlib import Path

from clause_to_cash.corpus import load_corpus
from clause_to_cash.extract.rules import RuleExtractor
from clause_to_cash.schema import FieldExtraction
from clause_to_cash.verify.faults import field_faults
from clause_to_cash.verify.gate import assess

root = Path(__file__).resolve().parents[1]
doc_id = sys.argv[1] if len(sys.argv) > 1 else "A-0015"
corpus = load_corpus(root / "data" / "corpus")
item = next(i for i in corpus.items if i.doc_id == doc_id)
clean = RuleExtractor().extract(item.doc_id, item.text)
base = assess(item.text, clean)
print(f"{doc_id}: clean extraction -> {'accepted' if base.accepted else 'review ' + str(base.review_fields)}\n")


def show(label: str, field: str, fe: FieldExtraction) -> None:
    ext = clean.model_copy(deep=True)
    ext.fields[field] = fe
    a = assess(item.text, ext).fields[field]
    quote = fe.quotes[0] if fe.quotes else "(none)"
    print(f"{label}\n  field      {field}\n  proposed   {fe.value!r}   confidence {fe.confidence}\n  quote      {quote}")
    print(f"  gate       {a.status}  {a.reasons}\n")


rng = random.Random(0)
faults = {(k, f): fe for f in ("initial_term_months", "change_of_control", "nonrenewal_notice")
          for k, fe in field_faults(item, clean, f, rng)}
show("1. invented evidence", "initial_term_months", faults[("FABRICATED_QUOTE", "initial_term_months")])
show("2. the silent miss", "change_of_control", faults[("FALSE_ABSENCE", "change_of_control")])
real = clean.get("nonrenewal_notice")
show("3. unit flipped (real quote kept)", "nonrenewal_notice",
     FieldExtraction(value={"amount": 90, "unit": "months"}, quotes=list(real.quotes), confidence=0.95))
