"""Broad, high-recall *cue lexicons* – the document-level guard against silent misses.

An extractor that answers "no minimum commitment" is making a claim about absence, which no
quote can prove.  The only defence is an independent, deliberately over-sensitive check:
*if the document contains vocabulary that normally accompanies a clause, an answer of
"none/false/silent" is not trusted without explicit negative evidence*.

These lexicons are generic legal-industry vocabulary.  They were written BEFORE the held-out
drafting style (style 3) of the synthetic corpus existed and have not been edited since.
"""
from __future__ import annotations

import re

_FLAGS = re.I

CUES: dict[str, re.Pattern[str]] = {k: re.compile(v, _FLAGS) for k, v in {
    "auto_renew": r"renew|evergreen|successive (?:term|period)|automatically extend",
    "nonrenewal_notice": r"renew|evergreen|non-?renewal",
    "termination_for_convenience": r"convenience|without cause|early terminat|terminat\w+ (?:this agreement )?(?:early|at any time)|cancel",
    "early_termination_fee_pct": r"early terminat|termination (?:fee|charge)|cancellation (?:fee|charge)|\bETF\b",
    "min_annual_commit_usd": r"minimum|commit|take-or-pay|shortfall|at least \$|not less than \$",
    "annual_escalator_pct": r"escalat|annual increase|increases? by|uplift|price adjustment",
    "affiliate_purchasing": r"affiliat|subsidiar|group compan|related compan",
    "change_of_control": r"change (?:of|in) control|assign|merger|successor|acquisition|acquir",
    "exclusive_requirements": r"exclusiv(?:e|ely)(?! remed)|sole (?:source|supplier)|all of (?:its|the customer's) requirements|only from",
    "mfn": r"favou?red|favou?rable|other customers|similarly situated|lowest price",
}.items()}

# A later amendment is only a threat to a field if it talks about that field.
AMENDMENT_HEADING = re.compile(
    r"(?im)^\s*(?:[a-z]+\s+)?(?:amendment|side letter|addendum|variation|modification|supplement)\b")
AMENDMENT_CUES: dict[str, re.Pattern[str]] = {k: re.compile(v, _FLAGS) for k, v in {
    "initial_term_months": r"initial term|extend|extension",
    "nonrenewal_notice": r"notice|renew|opt-?out",
    "pricing": r"schedule|pricing|price|tier|rate",
    "auto_renew": r"automatic(?:ally)? renew|evergreen|no longer renew",
}.items()}


_PURCHASE_CONTEXT = re.compile(r"purchas|order|\bbuy|\buse\b|procure|access|entitled|rates|pricing", re.I)
# NOTE: single newlines are line-wraps inside a sentence ("... no Affiliate of Customer shall\nbe permitted to order ..."),
# so only sentence punctuation and blank lines split.  (Found via fault injection: the old newline split let a wrapped clause escape.)
_SPLIT = re.compile(r"(?<=[.;])\s+|\n\s*\n")


def has_cue(field: str, text: str) -> bool:
    rx = CUES.get(field)
    if rx is None:
        return False
    if field == "affiliate_purchasing":
        # "Affiliates" also appears in indemnity/confidentiality boilerplate; only count it where purchase/use is discussed.
        return any(rx.search(s) and _PURCHASE_CONTEXT.search(s) for s in _SPLIT.split(text))
    return bool(rx.search(text))


def amendment_start(text: str) -> int | None:
    m = AMENDMENT_HEADING.search(text)
    return m.start() if m else None
