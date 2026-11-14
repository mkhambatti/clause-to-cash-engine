# Architecture

## Principles

1. **LLM proposes, code disposes.** Extraction is a proposal with evidence. The verification gate decides what is trusted. Dates and dollars are computed by deterministic code from verified terms; no model is ever asked to do arithmetic.
2. **Every number traces to a clause.** A field is `value + verbatim quotes + confidence`. The dashboard and the MCP tools show the quote, located in the source by the verifier.
3. **Fail closed.** Missing, malformed, or unverifiable output becomes "needs a human", never a default. Agent-facing tools refuse instead of guessing.
4. **One registry.** `schema.FIELD_LIST` drives the LLM tool schema, the prompt, the gate, the evaluation comparators and the dashboard, so they cannot drift apart.
5. **Measure the guardrail, not just the model.** Fault injection and held-out drafting styles test the gate independently of any extractor.

## Data flow

```
Corpus ──▶ Extractor.extract(doc_id, text) ──▶ Extraction{field: (value, quotes, confidence)}
                                                      │
                                         verify.gate.assess(text, extraction, GateConfig)
                                                      │  per field: accept | review(+reasons, evidence spans)
                                                      ▼
                    pipeline.run_pipeline(review = none | quarantine | oracle | gold)
                                                      │  Terms per contract
              resolve.vendors ── vendor clusters ─────┤
                                                      ▼
                         ContractRecord(terms, units, vendor_cluster, provisional)
                                                      │
        engine.calendar ◀─────────────────────────────┼──────────────▶ engine.risks
                                                      ▼
                    engine.opportunities  ──▶  engine.planner (MILP)  ──▶  Plan
```

`Extractor` is a one-method protocol (`extract(doc_id, text) -> Extraction`), so a new model, a vendor API, or a human-in-the-loop abstraction service plugs in without touching anything downstream.

## Core model (`schema.py`)

- `Terms`: the **effective** (post-amendment) commercial terms of one agreement: parties, service category, effective date, initial term, auto-renewal, non-renewal notice (as written: `{amount, unit: days|months}`, never silently converted), termination for convenience (+ notice days, fee %), take-or-pay commitment, pricing schedule (unit, `graduated | all_units`, tiers), escalator, affiliate purchasing, change of control, exclusivity, MFN, governing law.
- `Extraction`: `{field: FieldExtraction(value, quotes, confidence)}` plus token counts, latency and an error field.
- Critical fields (those that feed a money or date decision) are flagged in the registry; only they determine whether a document is "auto-verified".

## Verification gate (`verify/`)

| module | role |
|---|---|
| `gate.py` | combines the layers; each can be disabled independently, which is how the ablation and fault studies work |
| `textutil.locate` | finds a quote in the document tolerant of whitespace, typography and line-end hyphenation; returns offsets for the highlighter |
| `corroborate.py` | independent, generic parsers (digits, number words, "million", fractions, dates, durations, percentages, enum cue words) answering *does the quote support this value?* → `supports / contradicts / unknown` |
| `invariants.py` | cross-field consistency and plausibility ranges |
| `cues.py` | high-recall vocabulary per clause for the **cue guard**: an "absent" answer is accepted only with explicit negative evidence, or when the document has none of that clause's vocabulary at all. Lexicons are generic legal vocabulary written before the held-out style existed. |
| `faults.py` | fault catalogue, trial builder, `NoisyExtractor` for end-to-end simulation |

Policy: a field is auto-accepted only if **every** enabled check passes. `UNKNOWN` from corroboration is a reason to review, not a pass. The gate is intentionally asymmetric: false alarms cost minutes of review, silent errors cost money.

**Doc canary.** If mandatory facts (effective date, term, pricing) are missing, the extractor demonstrably did not understand the document, so *all* of its absence answers in that document are distrusted (`DOC_UNREADABLE`).

## Deterministic engines

**Dates (`dates.py`).** A term of N months starting on D ends the day before the N-month anniversary (inclusive). "Notice at least N days/months before the end of the then-current term" must be *given* by `end − N` (calendar-month arithmetic with end-of-month clamping), rolled back to the previous business day (weekends only; no holiday calendar). An *internal* deadline subtracts a configurable buffer. Lifecycle status: `open` (deadline ahead), `locked` (deadline for the current period already passed, so the contract is committed through the next period), `fixed_term`, `expired`, `unknown`.

**Pricing (`pricing.py`).** `graduated` is bracket-style (each unit at its own tier's rate; cost is continuous). `all_units` prices *every* unit at the tier reached by total volume, so cost can **drop** when volume crosses a threshold (a pricing cliff); the planner never assumes monotonicity. Annual cost applies the escalator by anniversary and the take-or-pay floor.

**Calendar and risks (`engine/calendar.py`, `risks.py`).** Every contract-forced decision with the dollars attached. Change-of-control flags apply only to the **acquired** entity's paper (the closing is a change of control for it, not for the acquirer). Others: requirements exclusivity, shelfware against a take-or-pay floor, auto-renewed lock-ins.

**Options (`engine/opportunities.py`).** For every service category (and every vendor within a category) with two or more contracts, enumerate `(survivor contract, switch quarter)`: all volume in the cluster moves onto the survivor's paper at quarter *t*.

- savings = Σ current annual cost − survivor's schedule applied to the **pooled** volume (tier mode, escalator and floor honoured);
- one-time = early-termination fees for members exited before natural expiry via a termination-for-convenience right + category switching cost (≈10% of that when volume stays with the same vendor);
- earliest quarter = max over members of the earliest legal exit (notice window, locked renewal, TfC notice), the survivor's affiliate-purchasing lead time (consent: 1 quarter; prohibited or silent: 2, meaning an amendment must be negotiated), change-of-control consent where the survivor is acquired-entity paper, and the category's migration lead time;
- never consolidates onto paper whose vendor holds a change-of-control termination right on the acquired company; never pools across different pricing units.

`_price_option` prices one option exactly; `reprice_option` re-evaluates a chosen option under *different* records (ground truth), which is how the end-to-end study measures invalid actions.

**Planner (`engine/planner.py`).** Weighted set packing with side constraints, solved exactly:

```
maximise   Σ_o npv_o · y_o
s.t.       Σ_{o ∋ c} y_o ≤ 1                       for every contract c        (consolidated at most once)
           Σ_{o: q(o)=q} load_o · y_o ≤ C          for every quarter q         (integration team bandwidth)
           Σ_{o: q(o)=q, stream(o)} load_o · y_o ≤ C_s   for every quarter q  (shared with platform convergence)
           y_o ∈ {0,1}
```

solved with scipy `milp` (HiGHS). A greedy fallback is used and *reported as such* when scipy is missing; an exhaustive solver exists so tests can prove the MILP is exactly optimal on random instances. A second solve with notice windows, exit fees, lead times and capacity removed gives the **no-friction ideal**, so the product can state what contract timing costs. At the demo scale (84 contracts, ~440 options) the whole pipeline runs in under a second.

## Review modes (`pipeline.py`)

| mode | meaning |
|---|---|
| `none` | use extracted values as they are; flagged contracts are marked provisional |
| `quarantine` | only fully verified contracts reach the planner (default for the agent-facing service) |
| `oracle` | flagged fields are replaced by ground truth: a simulated perfect reviewer (synthetic corpus only) |
| `gold` | ground-truth terms (the reference plan for evaluation) |

## Agent-facing surface (`service.py`, `mcp_server.py`)

A stdlib JSON-RPC/MCP server exposes five tools. The contract for anything an agent can act on: *if a field the answer depends on is unverified, return `safe_to_act: false` and the reasons, not a confident number.* `check_notice` is the clearest example: it answers TIMELY / TOO_LATE only when effective date, term, auto-renew and notice period are all verified, otherwise `REFUSED`.

## LLM extractor (`extract/llm.py`, `prompts.py`)

Messages API with a forced tool call; the tool schema is generated from the field registry (value, verbatim quotes, confidence per field). The long static system prompt and the tool schema carry `cache_control` so a corpus run pays for them once. Transient errors (429/5xx/network) retry with exponential backoff; any other failure returns an `Extraction` with `error` set and no fields, which the gate then routes to review. `EnsembleExtractor` runs several extractors and forces review (confidence 0) on any field they disagree about. Not exercised against the live API in this repository.

## Scaling notes (not built)

Extraction is embarrassingly parallel per document and the verifier is pure CPU. Planning decomposes by service category except for the shared capacity constraints, which suggests Lagrangian or rolling-horizon decomposition if the monolithic MILP becomes slow; this has not been tested beyond ~10² contracts. Real deployments would add OCR/layout parsing, exhibit and cross-reference handling, a reviewer UI that writes corrections back as labelled data, and drift monitoring on the gate's flag rate per drafting template.

## Extension points

- **New extractor:** implement `extract(doc_id, text) -> Extraction`.
- **New gate layer:** add a check in `gate.assess` behind a `GateConfig` flag; add a fault class in `faults.py`; the ablation tables pick it up.
- **New field:** add it to `FIELD_LIST`; the tool schema, prompt, comparators and dashboard follow. Add a corroborator and, if absence matters, a cue lexicon.
