# Product brief: contract-gated synergy capture

*Author's working document. All numbers from the demo are synthetic and illustrative.*

## 1. The situation

Skydance Corporation closed its acquisition of Warner Bros. Discovery on October 6, 2026 and committed, in its Q3'26 shareholder letter ([SEC 8-K, Ex. 99.1](https://www.sec.gov/Archives/edgar/data/0002041610/000204161026000062/ex99_q326.htm)), to **at least $6B of run-rate synergies, net of reinvestment, within three years: 30% realized in year one and 70% through year two.**

| line | target | stated base |
|---|---:|---:|
| corporate overhead, real estate & facilities | ≥ $2.2B | ~$8B |
| marketing | ≥ $1.0B | ~$5B |
| **technology** | **≥ $1.4B** | ~$4B |
| **procurement & vendor management** | **≥ $1.4B** | ~$9B |

The letter also sets the integration clock: align the Paramount+ and HBO Max back-end systems "within a year of closing". It names AI in three places (scaling AI workflows in corporate functions, AI coding tools in technology, "the latest in AI tools" for integration); the procurement line is described as "optimizing third-party spend".

**Observation.** Technology and procurement are $2.8B of the $6B (47%), and both are made of *vendor contracts*. A contract decides *when* money can move: notice windows, auto-renewals, termination rights, minimum commitments, affiliate-purchasing rights, change-of-control clauses. The 30%/70% phasing is therefore partly a contract-calendar problem, not only an execution problem.

## 2. What was checked in the framing I started from

The starting context framed the opportunity around AI automation of content production and rights. Checking primary sources moved the focus:

- **$6B is a total across four cost lines, not an "AI savings" number.** The letter names AI in three places (corporate workflows, technology/coding tools, integration tooling). A third-party piece ([FourWeekMBA](https://fourweekmba.com/ai-skydance-cites-ai-in-areas-holding-3-6b-of-its-6b-savings/), "Skydance cites AI in areas holding $3.6B of its $6B savings") reaches $3.6B, which equals the corporate ($2.2B) plus technology ($1.4B) lines; that says where AI is *mentioned*, not how much saving is attributable to AI.
- **Content spend is discussed separately from procurement.** The letter describes procurement as "optimizing third-party spend across vendors spanning hundreds of services" and, separately, an "industry-leading base of more than $30 billion in annual content spend". It does not say content licensing is excluded, but a ~$9B procurement base next to $30B+ of content spend suggests the procurement line is mostly not programming (my inference), so rights automation is not where the contract-bound savings are most visible.
- **Price alignment alone is small.** McKinsey's procurement-synergy analysis puts procurement at 25–40% of a merger's cost-saving potential, but says even a 5–10% price difference is "a fairly small opportunity"; pooled spend leverage and clean-sheet renegotiation are bigger ([source](https://www.mckinsey.com/capabilities/operations/our-insights/procurement-driven-synergies-in-mergers-landmine-or-goldmine)). Pooled-volume pricing across two companies' contracts is precisely what affiliate-purchasing and notice-window clauses gate.
- **Integration history is slow.** The Pluto TV integration is reported to have taken about 18 months, against a one-year back-end alignment target now. Time-to-capture is the scarce resource.

So the product bet is the **verifiable, contract-bound layer of the technology and procurement lines**: where AI's job is reading unstructured documents, and where a wrong answer is expensive enough that verification is the product.

## 3. Users and jobs

| user | job | what hurts today |
|---|---|---|
| Integration management office | sequence consolidation across ~thousands of overlapping vendor relationships | no single view of which contracts can be touched when, or what it costs to touch them early |
| Procurement category leads | pool volume and renegotiate | cannot tell if affiliate purchasing is allowed, what the other side's price tiers really imply, or which notice dates are coming |
| Legal operations | abstract and verify | manual abstraction does not scale to a merger; errors are silent |
| Finance (synergy tracking) | defend the 30/70 phasing | savings claims with no contractual basis |

## 4. What the demo product does

1. **Extract with evidence** (LLM or offline baseline): 19 fields per contract, each with verbatim quotes.
2. **Verify** (the gate): grounding, corroboration, cross-field invariants, a cue guard against silent misses, amendment supersession, a document-level canary. Flagged fields go to review with the evidence highlighted in the source.
3. **Decide** (deterministic): a decision calendar of every notice deadline, lock-in and expiry with the dollars that auto-commit if missed; merger-specific risks (change of control applies only to the acquired company's paper); an exact optimizer that sequences consolidation under notice windows, exit fees, legal lead times and integration capacity.
4. **Act safely** (agent surface): MCP tools that refuse when inputs are unverified.

The one-line product principle: **the savings number is only as credible as the evidence behind each contract term, so build the evidence trail and the review queue first.**

## 5. Success metrics

| metric | why | demo value (synthetic) |
|---|---|---|
| silent-error rate among auto-accepted fields | the number that must stay near zero | 0 of ~2,300 auto-accepted critical fields, fresh corpora |
| faults the gate routes to review | does the guardrail work independent of the model | 8,786 of 8,786 injected |
| share of fields sent to a human | the cost of safety | 0.3% at 0% extractor error; 5.5% at 5% |
| deadline-miss count | the loss that is easiest to explain | tracked per contract; demo flags 11 auto-renewing contracts with notice due in 90 days ($51M committed if missed) |
| contract-timing friction | what notice windows and capacity cost vs an ideal world | plan NPV is 42% of the friction-free ideal |
| phasing vs the stated 30/70 | the thing finance has to defend | 21% / 74% live by end of year 1 / 2 in the demo corpus |
| reviewer minutes per contract; time-to-first-verified-calendar | adoption | not measured (needs real users) |

## 6. Rollout I would propose

1. **Weeks 0–2: calendar-only pilot on one category** (e.g. cloud and CDN, where both companies buy the same services). Ship the decision calendar and review queue with human abstraction as the baseline; measure agreement and flag rate. Value is immediate: no missed notices.
2. **Weeks 2–6: LLM extraction behind the gate** on the pilot category; calibrate the corroboration layer on reviewer corrections; set a silent-error budget (for example ≤0.1% of auto-accepted critical fields) before widening auto-accept.
3. **Weeks 6–12: pooling plan** for categories with clean affiliate-purchasing rights; joint sequencing with the platform convergence roadmap (streaming-stack work competes for the same engineers).
4. **Then widen** by category, with the gate's flag rate per drafting template as the drift monitor.

## 7. Risks and what mitigates them

| risk | mitigation in the design |
|---|---|
| extractor makes confident mistakes | gate checks evidence, not confidence; confidence-only catches 0% in the fault study |
| a model "finds nothing" in a document it did not understand | cue guard and doc canary; without the canary, 61% of auto-accepted style-3 fields were wrong |
| savings claimed without contractual basis | every option is priced from verified terms and lists its legal prerequisites |
| plan relies on an exit the contract does not allow | `reprice_option` re-checks feasibility; invalid actions are counted in the evaluation |
| over-automation on unfamiliar contract templates | the gate fails safe (0 wrong accepted on the held-out style), at the cost of review volume; monitor flag rate per template |
| confidentiality of real contracts | runs offline with the rules extractor; the LLM path is optional and swappable |
| legal liability | decision support only; a human reviews every flag and every notice |

## 8. What I would want to learn from the team

- Which vendor categories overlap most between the two companies, and how much of that spend sits on paper that is *already* auto-renewed and locked in?
- What do current contract repositories contain (clean text, scans, CLM exports)? Is there any labelled abstraction we can calibrate the gate on?
- How is synergy tracked in finance today, and what evidence standard does it need to meet for a line to count toward the 30% in year one?
- Who owns the notice calendar now, and what is the cost of a miss in their own words?
- How should platform-convergence capacity be traded against vendor migrations?

## 9. Honest limits of this demo

Synthetic data; no live LLM evaluation; the fault catalogue was written by the gate's author (and still found a real bug); the planner is a deliberately simple contract-level model; dollar figures are illustrative. See [EVALUATION.md](EVALUATION.md).
