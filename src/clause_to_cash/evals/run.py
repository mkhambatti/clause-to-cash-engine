"""Reproducible evaluation: ``python -m clause_to_cash.evals.run``  (writes docs/results.json + docs/RESULTS.md).

Protocol (written down BEFORE the final numbers were produced; see docs/EVALUATION.md):

* dev corpus      seed 7  - the shipped corpus; all rule/cue/corroboration development happened here, on drafting styles 0-2.
* fresh corpora   seeds 1001-1003 - generated after development was frozen; nothing was tuned on them.  Headline numbers.
* held-out style  drafting style 3 - paraphrase-heavy; no rule or lexicon was written against its text.

Every number in the docs is produced by this file, so they cannot drift from the code.
"""
from __future__ import annotations

import argparse
import json
import time
from collections import Counter, defaultdict
from dataclasses import replace
from pathlib import Path
from typing import Any

from ..corpus import Corpus, CorpusItem
from ..engine.model import PlanParams
from ..engine.opportunities import reprice_option
from ..extract.rules import RuleExtractor
from ..pipeline import RunResult, run_pipeline
from ..resolve.vendors import normalize, pairwise_f1, resolve_vendors
from ..schema import CRITICAL_FIELDS, Extraction
from ..synth.generate import generate
from ..verify.faults import FAULTS, LAYERS, NoisyExtractor, build_trials, config, escapes, run_trials
from ..verify.gate import GateConfig, assess
from .metrics import score_fields, values_equal

DEV_SEED = 7
# Seeds 1001-1003 were used once, found a bug (see docs/EVALUATION.md, protocol log) and are now DEV data.
# Headline numbers come from seeds that nobody has inspected.
FRESH_SEEDS = (2001, 2002, 2003)
SEEN_ONLY = (1 / 3, 1 / 3, 1 / 3, 0.0)
GATE_OFF = GateConfig(grounding=False, corroboration=False, invariants=False, cues=False, supersession=False,
                      confidence=False, canary=False)


def _pct(a: float, b: float) -> float:
    return round(100.0 * a / b, 1) if b else 0.0


# --------------------------------------------------------------------------------------------- extraction + gate
def extraction_and_gate(corpora: dict[int, Corpus], cfg: GateConfig) -> dict[str, Any]:
    ex = RuleExtractor()
    by_style: dict[int, Counter] = defaultdict(Counter)
    for corpus in corpora.values():
        for it in corpus.items:
            assert it.gold is not None
            e = ex.extract(it.doc_id, it.text)
            sc = score_fields(e, it.gold)
            a = assess(it.text, e, cfg)
            c = by_style[it.style]
            c["docs"] += 1
            c["docs_all_correct"] += all(sc[f] for f in CRITICAL_FIELDS)
            c["docs_accepted"] += a.accepted
            c["docs_accepted_with_error"] += a.accepted and not all(sc[f] for f in CRITICAL_FIELDS)
            for f in CRITICAL_FIELDS:
                fa = a.fields[f]
                c["fields"] += 1
                c["fields_correct"] += sc[f]
                c["fields_accepted"] += fa.status == "accept"
                c["fields_accepted_wrong"] += fa.status == "accept" and not sc[f]
                c["fields_wrong"] += not sc[f]
                c["fields_wrong_flagged"] += (not sc[f]) and fa.status == "review"
    out = {}
    for s, c in sorted(by_style.items()):
        out[str(s)] = dict(c) | {
            "field_accuracy_pct": _pct(c["fields_correct"], c["fields"]),
            "doc_auto_accept_pct": _pct(c["docs_accepted"], c["docs"]),
            "field_auto_accept_pct": _pct(c["fields_accepted"], c["fields"]),
            "silent_error_rate_pct": _pct(c["fields_accepted_wrong"], c["fields_accepted"]),
            "error_recall_pct": _pct(c["fields_wrong_flagged"], c["fields_wrong"]),
        }
    return out


def _expand(text: str, a: int, b: int) -> str:
    s = max(text.rfind("\n", 0, a), text.rfind(". ", 0, a) + 1)
    e1, e2 = text.find("\n", b), text.find(". ", b)
    ends = [x for x in (e1, e2 + 1 if e2 >= 0 else -1) if x >= 0]
    return text[(0 if s < 0 else s):(min(ends) if ends else len(text))].strip()


def gate_with_perfect_extractor(corpora: dict[int, Corpus]) -> dict[str, Any]:
    """Automation rate the gate allows when the extractor is PERFECT and cites the ground-truth evidence sentence.

    Isolates the gate's own conservatism (false alarms) per drafting style - the cost side of 'fail safe'."""
    from ..schema import FIELD_LIST, FieldExtraction
    by_style: dict[int, Counter] = defaultdict(Counter)
    for corpus in corpora.values():
        for it in corpus.items:
            assert it.gold is not None
            g = it.gold.model_dump(mode="json")
            ext = Extraction(doc_id=it.doc_id, extractor="perfect")
            for spec in FIELD_LIST:
                qs = list(dict.fromkeys(_expand(it.text, a, b) for a, b in it.spans.get(spec.name, [])))
                ext.fields[spec.name] = FieldExtraction(value=g.get(spec.name), quotes=qs, confidence=0.95)
            a = assess(it.text, ext, GateConfig())
            c = by_style[it.style]
            c["docs"] += 1
            c["docs_accepted"] += a.accepted
            for f in CRITICAL_FIELDS:
                c["fields"] += 1
                c["fields_accepted"] += a.fields[f].status == "accept"
    return {str(s): dict(c) | {"field_auto_accept_pct": _pct(c["fields_accepted"], c["fields"]),
                               "doc_auto_accept_pct": _pct(c["docs_accepted"], c["docs"])} for s, c in sorted(by_style.items())}


# --------------------------------------------------------------------------------------------- faults
def fault_study(corpora: dict[int, Corpus]) -> dict[str, Any]:
    ex = RuleExtractor()
    items: list[tuple[CorpusItem, Extraction]] = []
    texts: dict[str, str] = {}
    for seed, corpus in corpora.items():
        for it in corpus.items:
            e = ex.extract(it.doc_id, it.text)
            if it.gold is not None and all(score_fields(e, it.gold)[f] for f in CRITICAL_FIELDS):
                uid = f"{seed}:{it.doc_id}"
                it2 = replace(it, doc_id=uid)
                e2 = e.model_copy(update={"doc_id": uid})
                items.append((it2, e2))
                texts[uid] = it.text
    trials = build_trials(items)
    cfgs: dict[str, GateConfig] = {"full": config(), "confidence_only": config(only="confidence")}
    for layer in LAYERS:
        cfgs[f"only_{layer}"] = config(only=layer)
    for layer in LAYERS:
        cfgs[f"without_{layer}"] = config(without=layer)
    cfgs["without_canary"] = replace(config(), canary=False)
    table: dict[str, dict[str, float]] = {}
    counts = Counter(t.fault for t in trials)
    for name, cfg in cfgs.items():
        r = run_trials(trials, texts, cfg)
        table[name] = {f: round(100.0 * r[f][0] / r[f][1], 1) for f in FAULTS if f in r}
        table[name]["ALL"] = round(100.0 * sum(v[0] for v in r.values()) / max(1, sum(v[1] for v in r.values())), 1)
    esc = escapes(trials, texts, config())
    escaped = [{"fault": t.fault, "field": t.field, "doc": t.doc_id,
                "value": str(t.extraction.get(t.field).value)[:80], "quotes": [q[:140] for q in t.extraction.get(t.field).quotes][:1]}
               for t in esc[:12]]
    return {"clean_docs": len(items), "trials": len(trials), "per_fault": dict(counts), "catch_pct": table,
            "escapes": len(esc), "escape_examples": escaped}


# --------------------------------------------------------------------------------------------- entity resolution
def er_study(corpora: dict[int, Corpus]) -> dict[str, Any]:
    agg: Counter[str] = Counter()
    naive: Counter[str] = Counter()
    for seed, corpus in corpora.items():
        gold = {it.doc_id: it.vendor_id or it.doc_id for it in corpus.items}
        res = resolve_vendors([(it.doc_id, (it.gold.vendor_party if it.gold else None) or it.doc_id,
                                it.gold.service_category if it.gold else None) for it in corpus.items])
        m = pairwise_f1(res.cluster_of, gold)
        for k in ("tp", "fp", "fn"):
            agg[k] += int(m[k])
        pred_naive = {it.doc_id: " ".join(normalize((it.gold.vendor_party if it.gold else "") or "")) for it in corpus.items}
        mn = pairwise_f1(pred_naive, gold)
        for k in ("tp", "fp", "fn"):
            naive[k] += int(mn[k])

    def f1(c: Counter[str]) -> dict[str, float]:
        p = c["tp"] / (c["tp"] + c["fp"]) if c["tp"] + c["fp"] else 1.0
        r = c["tp"] / (c["tp"] + c["fn"]) if c["tp"] + c["fn"] else 1.0
        return {"precision": round(p, 3), "recall": round(r, 3), "f1": round(2 * p * r / (p + r), 3) if p + r else 0.0,
                "false_merges": c["fp"], "missed_merges": c["fn"]}

    return {"resolver": f1(agg), "normalised_exact_match": f1(naive)}


# --------------------------------------------------------------------------------------------- end to end
def _realized(run: RunResult, gold: RunResult) -> dict[str, float]:
    recs = {r.cid: r for r in gold.records}
    real = invalid = claimed_invalid = 0.0
    for o in run.plan.chosen:
        g = reprice_option(o, recs, run.params)
        if g is None:
            invalid += 1
            claimed_invalid += o.npv
        else:
            real += g.npv
    return {"claimed_npv": run.plan.npv, "realized_npv": real, "invalid_actions": invalid, "claimed_invalid_npv": claimed_invalid,
            "actions": float(len(run.plan.chosen))}


def _silent_wrong(run: RunResult) -> tuple[int, int]:
    wrong = total = 0
    in_plan = {r.cid for r in run.records}
    for d in run.docs:
        if d.item.doc_id not in in_plan or d.item.gold is None:
            continue
        g = d.item.gold.model_dump(mode="json")
        t = d.terms.model_dump(mode="json")
        for f in CRITICAL_FIELDS:
            total += 1
            wrong += not values_equal(f, t.get(f), g.get(f))
    return wrong, total


def e2e_study(seeds: tuple[int, ...], weights: tuple[float, ...], rates: tuple[float, ...], use_milp: bool = True) -> dict[str, Any]:
    arms = {"trust_all": ("none", GATE_OFF), "gate_quarantine": ("quarantine", GateConfig()), "gate_review": ("oracle", GateConfig())}
    acc: dict[float, dict[str, defaultdict[str, float]]] = {r: {a: defaultdict(float) for a in arms} for r in rates}
    gold_tot: defaultdict[str, float] = defaultdict(float)
    for seed in seeds:
        corpus = generate(seed, style_weights=weights)
        gold = run_pipeline(corpus, None, review="gold", use_milp=use_milp)
        gold_tot["npv"] += gold.plan.npv
        gold_tot["run_rate"] += gold.plan.final_run_rate
        gold_tot["contracts"] += len(gold.records)
        items = corpus.by_id()
        for rate in rates:
            noisy = NoisyExtractor(RuleExtractor(), items, rate, seed=seed)
            cached = {it.doc_id: noisy.extract(it.doc_id, it.text) for it in corpus.items}
            for arm, (review, gate) in arms.items():
                run = run_pipeline(corpus, noisy, gate, review=review, use_milp=use_milp, cached=cached)  # type: ignore[arg-type]
                r = _realized(run, gold)
                wrong, total = _silent_wrong(run)
                a = acc[rate][arm]
                for k, v in r.items():
                    a[k] += v
                a["fields_wrong_in_plan"] += wrong
                a["fields_in_plan"] += total
                a["contracts_in_plan"] += len(run.records)
                a["contracts"] += len(corpus.items)
                flagged = sum(len([n for n, x in d.assessment.fields.items() if x.status == "review" and n in CRITICAL_FIELDS]) for d in run.docs)
                a["fields_flagged"] += flagged if arm != "trust_all" else 0
                a["fields_total"] += len(run.docs) * len(CRITICAL_FIELDS)
                a["docs_flagged"] += sum(1 for d in run.docs if not d.assessment.accepted) if arm != "trust_all" else 0
                a["docs"] += len(run.docs)
                a["run_rate"] += run.plan.final_run_rate
    out: dict[str, Any] = {"seeds": list(seeds), "gold": {"npv": gold_tot["npv"], "run_rate": gold_tot["run_rate"], "contracts": gold_tot["contracts"]}, "rates": {}}
    for rate in rates:
        out["rates"][str(rate)] = {}
        for arm in arms:
            a = acc[rate][arm]
            out["rates"][str(rate)][arm] = {
                "realized_pct_of_gold_npv": round(100 * a["realized_npv"] / gold_tot["npv"], 1),
                "claimed_pct_of_gold_npv": round(100 * a["claimed_npv"] / gold_tot["npv"], 1),
                "invalid_actions": int(a["invalid_actions"]),
                "actions": int(a["actions"]),
                "wrong_critical_fields_in_plan": int(a["fields_wrong_in_plan"]),
                "critical_fields_in_plan": int(a["fields_in_plan"]),
                "contracts_in_plan_pct": _pct(a["contracts_in_plan"], a["contracts"]),
                "fields_flagged_pct": _pct(a["fields_flagged"], a["fields_total"]),
                "docs_flagged_pct": _pct(a["docs_flagged"], a["docs"]),
            }
    return out


# --------------------------------------------------------------------------------------------- driver
def run_all(quick: bool = False) -> dict[str, Any]:
    t0 = time.time()
    fresh_seeds = FRESH_SEEDS[:1] if quick else FRESH_SEEDS
    fresh = {s: generate(s) for s in fresh_seeds}
    dev = {DEV_SEED: generate(DEV_SEED)}
    res: dict[str, Any] = {"protocol": {"dev_seed": DEV_SEED, "fresh_seeds": list(fresh_seeds), "held_out_style": 3}}
    res["extraction_gate_dev"] = extraction_and_gate(dev, GateConfig())
    res["extraction_gate_fresh"] = extraction_and_gate(fresh, GateConfig())
    res["extraction_gate_fresh_no_canary"] = extraction_and_gate(fresh, replace(GateConfig(), canary=False))
    res["extraction_gate_fresh_ungated"] = extraction_and_gate(fresh, GATE_OFF)
    res["gate_perfect_extractor"] = gate_with_perfect_extractor(fresh)
    res["faults"] = fault_study({**dev, **fresh})
    res["entity_resolution"] = er_study(fresh)
    rates = (0.0, 0.05) if quick else (0.0, 0.02, 0.05, 0.10)
    res["e2e_seen_styles"] = e2e_study(fresh_seeds, SEEN_ONLY, rates)
    res["e2e_with_held_out_style"] = e2e_study(fresh_seeds, (0.25, 0.25, 0.25, 0.25), (0.0,))
    res["elapsed_s"] = round(time.time() - t0, 1)
    return res


# --------------------------------------------------------------------------------------------- markdown
def _table(head: list[str], rows: list[list[Any]]) -> str:
    out = ["| " + " | ".join(head) + " |", "|" + "|".join("---" if i == 0 else "---:" for i in range(len(head))) + "|"]
    out += ["| " + " | ".join(str(c) for c in r) + " |" for r in rows]
    return "\n".join(out)


def to_markdown(res: dict[str, Any]) -> str:  # noqa: C901
    L: list[str] = ["# Evaluation results (auto-generated by `python -m clause_to_cash.evals.run`)", "",
                    f"Protocol: dev corpus seed {res['protocol']['dev_seed']}; fresh corpora seeds {res['protocol']['fresh_seeds']} "
                    f"(generated after development was frozen); held-out drafting style {res['protocol']['held_out_style']}.", ""]

    def style_table(key: str, title: str) -> None:
        d = res[key]
        L.extend([f"## {title}", ""])
        rows = []
        for s, v in d.items():
            rows.append([f"style {s}" + (" (held-out)" if s == "3" else ""), v["docs"], f"{v['field_accuracy_pct']}%",
                         f"{v['doc_auto_accept_pct']}%", f"{v['field_auto_accept_pct']}%", v["fields_accepted_wrong"],
                         f"{v['silent_error_rate_pct']}%", f"{v['error_recall_pct']}%"])
        L.extend([_table(["drafting style", "docs", "field accuracy", "docs auto-accepted", "fields auto-accepted",
                          "wrong fields auto-accepted", "silent error rate", "errors flagged"], rows), ""])

    style_table("extraction_gate_dev", "Rules baseline + gate on the dev corpus (seed 7)")
    style_table("extraction_gate_fresh", "Rules baseline + gate on fresh corpora")
    style_table("extraction_gate_fresh_no_canary", "Same, with the doc-level canary switched off (the 'before')")
    style_table("extraction_gate_fresh_ungated", "Same, with every gate layer off (trust the extractor)")

    gp = res["gate_perfect_extractor"]
    L.extend(["## Gate conservatism: what a PERFECT extractor would get automated", "",
              "Extraction = ground truth, evidence = the ground-truth sentence. Anything not auto-accepted here is a false alarm of the gate, "
              "i.e. the review cost of 'fail safe'.", "",
              _table(["drafting style", "docs", "docs auto-accepted", "critical fields auto-accepted"],
                     [[f"style {s}" + (" (held-out)" if s == "3" else ""), v["docs"], f"{v['doc_auto_accept_pct']}%", f"{v['field_auto_accept_pct']}%"] for s, v in gp.items()]), ""])
    f = res["faults"]
    L.extend([f"## Fault injection: does the gate notice a wrong extraction?", "",
              f"{f['trials']} single-field corruptions of {f['clean_docs']} clean documents; every corrupted field carries 90-95% self-reported confidence. "
              "Cell = % of injected faults routed to review.", ""])
    cols = ["full", "confidence_only"] + [f"only_{l}" for l in LAYERS]
    rows = [[k + f" (n={f['per_fault'].get(k, 0)})"] + [f["catch_pct"][c].get(k, "-") for c in cols] for k in FAULTS] + \
           [["**ALL**"] + [f["catch_pct"][c]["ALL"] for c in cols]]
    L.extend([_table(["fault"] + [c.replace("_", " ") for c in cols], rows), "", "Leave-one-layer-out (what each layer uniquely protects):", ""])
    cols2 = [f"without_{l}" for l in LAYERS]
    rows2 = [[k] + [f["catch_pct"][c].get(k, "-") for c in cols2] for k in FAULTS] + [["**ALL**"] + [f["catch_pct"][c]["ALL"] for c in cols2]]
    L.extend([_table(["fault"] + [c.replace("without_", "w/o ") for c in cols2], rows2), ""])

    if f["escapes"]:
        L.extend([f"**Escapes of the full gate: {f['escapes']} of {f['trials']}.** Examples:", ""])
        for e in f["escape_examples"]:
            L.append(f"- `{e['fault']}` on `{e['field']}` ({e['doc']}): value `{e['value']}`, evidence {e['quotes']}")
        L.append("")
    er = res["entity_resolution"]
    L.extend(["## Vendor entity resolution (pairwise, fresh corpora, includes same-prefix decoy vendors)", "",
              _table(["method", "precision", "recall", "F1", "false merges", "missed merges"],
                     [[k.replace("_", " "), v["precision"], v["recall"], v["f1"], v["false_merges"], v["missed_merges"]] for k, v in er.items()]), ""])

    def e2e_table(key: str, title: str) -> None:
        d = res[key]
        L.extend([f"## {title}", "", f"Seeds {d['seeds']}; ground-truth plan NPV ${d['gold']['npv'] / 1e6:.1f}M over {d['gold']['contracts']} contracts. "
                  "'Realized' re-prices every chosen action under ground-truth terms (invalid = infeasible or value-destroying in truth).", ""])
        rows = []
        for rate, arms in d["rates"].items():
            for arm, v in arms.items():
                rows.append([rate, arm.replace("_", " "), f"{v['claimed_pct_of_gold_npv']}%", f"{v['realized_pct_of_gold_npv']}%",
                             f"{v['invalid_actions']}/{v['actions']}", f"{v['wrong_critical_fields_in_plan']}/{v['critical_fields_in_plan']}",
                             f"{v['contracts_in_plan_pct']}%", f"{v['fields_flagged_pct']}%"])
        L.extend([_table(["field error rate injected", "arm", "plan NPV claimed (% of truth)", "NPV realized (% of truth)", "invalid actions",
                          "wrong fields in plan", "contracts in plan", "fields sent to review"], rows), ""])

    e2e_table("e2e_seen_styles", "End to end: noisy extractor on seen drafting styles")
    e2e_table("e2e_with_held_out_style", "End to end: including the held-out drafting style (rules baseline cannot read it)")
    L.append(f"_Run time: {res['elapsed_s']} s._")
    return "\n".join(L) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="docs", help="output directory")
    ap.add_argument("--quick", action="store_true")
    args = ap.parse_args()
    res = run_all(args.quick)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "results.json").write_text(json.dumps(res, indent=1))
    (out / "RESULTS.md").write_text(to_markdown(res))
    print(to_markdown(res))


if __name__ == "__main__":
    main()
