# Evaluation protocol and an honest account of what was tuned on what

Every number in [`RESULTS.md`](RESULTS.md) is produced by `python -m clause_to_cash eval` (about 3 minutes, deterministic). This document says what the numbers do and do not show.

## Data splits

| split | what | how it was used |
|---|---|---|
| **dev** | corpus seed 7 (84 contracts), the one shipped in `data/corpus` | all development of the rules baseline, cue lexicons, corroborators and invariants, on drafting styles 0–2 |
| **held-out style** | drafting style 3 (paraphrase-heavy: "the deal rolls over for another year", "cannot be transferred, including to an acquirer, unless the other side agrees") | no rule or lexicon was written against its text; the cue lexicons were written before style-3 text existed |
| **fresh** | corpora generated from seeds 2001, 2002, 2003 (246 contracts, all four styles) | generated after development was frozen; headline numbers |

## Protocol log (what actually happened, in order)

1. **Generator parameters were tempered before any evaluation.** The first gold-terms run produced a run-rate of 12.5% of spend, which was an artefact of my assumed vendor price dispersion (±15%) and tier discounts. I narrowed both (vendor price multiplier 0.92–1.10, tier discounts 3–8%); the corpus now yields about 8–11% of spend. Skydance's own procurement target implies ~15.5% (≥$1.4B on ~$9B), and McKinsey notes pure price alignment is small, so the order of magnitude is plausible, but **it is an assumption, not a finding**.
2. **The doc-level canary was added after looking at held-out data.** On the dev corpus, the held-out style produced a failure the gate had not been designed for: the rules baseline found none of the mandatory facts but still answered "no termination right / silent" on most fields, and the per-field checks accepted 75 of 124 auto-accepted fields wrongly. I then added a rule: if mandatory facts (effective date, term, pricing) are missing, distrust every "absent" answer in that document. This is a rule written after seeing the failure, which is why its effect is **re-measured on seeds nobody had looked at** (2001–2003): without the canary, 207 wrong fields (60.9% of auto-accepted fields) pass on style 3; with it, 0.
3. **Fault injection found a real bug.** The first fault-injection run (on seeds 1001–1003) showed 6 of 8,396 injected faults escaping the gate, all `FALSE_ABSENCE` on `affiliate_purchasing`. Cause: the affiliate cue worked sentence by sentence but split on single newlines, so a hard-wrapped clause ("…no Affiliate of Customer shall⏎be permitted to order…") never had the party word and the purchase verb in the same "sentence". Fixed generically (split only on sentence punctuation and blank lines). Because the fix was made after looking at those seeds, **seeds 1001–1003 were retired as dev data and the headline numbers were regenerated on 2001–2003**, which had not been inspected. The fix did not change dev-corpus gate behaviour.
4. Nothing else was changed after the final run started.

## What each experiment measures

**Extraction + gate on the rules baseline, by drafting style.** The baseline is a stand-in for an LLM that makes the system runnable offline. High accuracy on styles 0–2 is expected (same author as the generator) and is not evidence of generalisation; style 3 is the check. Reported per style: field accuracy; docs and fields auto-accepted; *wrong fields auto-accepted* (the number that matters); error recall. Three gate settings are shown: full, canary off, and every layer off.

**Gate with a perfect extractor.** Extraction equals ground truth and the cited evidence is the ground-truth sentence. Anything not auto-accepted is a false alarm of the gate, so this isolates the review cost of "fail safe" per drafting style. Caveat: ground-truth evidence for some style-1 clauses is a table cell rather than a full sentence, so style-1 doc-level numbers are pessimistic.

**Fault injection** ([`verify/faults.py`](../src/clause_to_cash/verify/faults.py)). Start from extractions that are fully correct with genuine, grounded quotes (the rules baseline on documents it reads perfectly). Corrupt exactly one critical field per trial, in one of eight ways, giving the corrupted field 90–95% confidence:

| fault | what it models |
|---|---|
| WRONG_VALUE | number/date/enum misread (day-month swap, notice unit confusion, ×10 commit, tier price drift, flipped boolean); the genuine quote is kept |
| WRONG_CLAUSE | wrong value "supported" by a real quote copied from a different clause |
| FABRICATED_QUOTE | wrong value "supported" by an invented sentence that is not in the document |
| NO_EVIDENCE | wrong value, no quote |
| FALSE_ABSENCE | a clause that exists is reported absent / silent / false (the silent miss) |
| PHANTOM_CLAUSE | a clause that does not exist is reported present, citing an unrelated real quote |
| STALE_BASE_VALUE | value from the base agreement although an amendment overrides it |
| BROKEN_STRUCTURE | pricing tiers gapped, or notice period longer than the term |

A fault counts as caught if the target field is routed to review. Reported: the full gate, a confidence-floor-only gate (the common approach), each layer alone, and each layer left out.

**Reading the layer tables honestly.** `invariants` and `confidence` show no unique contribution in the leave-one-out table: invariants overlap with corroboration for the faults modelled here (their real job in this system is the doc canary), and injected faults are confident by construction, so a confidence floor cannot catch them. The confidence floor is kept as cheap triage for extractors whose confidence is informative. Grounding, corroboration, cue guard and supersession each protect a class of faults nothing else catches.

**Vendor entity resolution.** Pairwise precision/recall on cluster membership using ground-truth vendor ids, including same-prefix decoy vendors ("Northgate Cloud Services" vs "Northgate Cloud Analytics"). A short form that could belong to two clusters is left ambiguous for a human. The synthetic name noise (suffixes, short forms, casing) is mild, so F1 = 1.0 mostly shows the resolver does not make the mistakes it was designed not to make.

**End to end.** A noisy extractor wraps the baseline and corrupts critical fields at a stated rate with the same fault catalogue. Three arms run the whole pipeline:

- *trust all*: gate off, every extracted value used;
- *gate quarantine*: a document reaches the planner only if all its critical fields pass (no human);
- *gate review*: flagged fields are resolved by a reviewer (an **oracle** that supplies ground truth, i.e. a perfect reviewer).

"Realized" value re-prices every chosen action under ground-truth terms (`engine.opportunities.reprice_option`). An action is *invalid* if it is infeasible (e.g. it assumes an exit the contract does not allow) or destroys value under the true terms. Capacity feasibility is not re-checked, and realized NPV counts only valid actions at their true value. "Claimed" is what the plan said it would deliver.

## Threats to validity

- **Synthetic corpus, one author.** Generator, rules, gate and fault catalogue share an author and a mental model of contracts. Real contracts have scanned layouts, exhibits, redlines, cross-references and exceptions this corpus does not.
- **No live LLM.** The Claude adapter is covered by tests with a fake transport. The system's claim is that *whatever* the extractor returns is checked; how often a real model's errors fall inside the modelled fault classes is exactly what must be measured next.
- **Oracle reviewer.** Real reviewers err, tire, and anchor on the highlighted evidence. The review-effort numbers (share of fields and documents flagged) are the transferable part; the 100% recovery is an upper bound.
- **Trust-all "claimed NPV" magnitudes** (121%–350% of truth) depend on the fault mix, notably the ×10 commit corruption. Use the realized NPV and invalid-action columns.
- **Quarantine is deliberately blunt.** One flagged field out of 12 removes the whole contract, so at 5% field error it loses nearly half the contracts. It is shown to quantify the value of field-level review, not as a recommended mode.
- **Gate conservatism on unfamiliar drafting** is real (57% of style-3 fields auto-accepted even with a perfect extractor). The corroborators are generic keyword parsers; calibrating them on a labelled real corpus is the first thing to do with real data.

## Reproducing

```bash
make test        # 47 tests
make eval        # regenerates docs/RESULTS.md and docs/results.json (~3 min)
make eval-quick  # 1 fresh seed, 2 noise rates (~1 min)
```
