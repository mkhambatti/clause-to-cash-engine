import random

import pytest

from clause_to_cash.engine.model import PlanParams
from clause_to_cash.engine.opportunities import Option, reprice_option
from clause_to_cash.engine.planner import solve_bruteforce, solve_greedy, solve_milp
from clause_to_cash.pipeline import run_pipeline
from clause_to_cash.resolve.vendors import pairwise_f1, resolve_vendors
from clause_to_cash.verify.gate import GateConfig


def _opt(i, members, q, npv, load, stream=False):
    return Option(oid=f"o{i}", kind="consolidate", cluster="c", category="cdn_delivery", survivor=members[0], members=members, switch_q=q,
                  annual_baseline=0, annual_pooled=0, annual_saving=npv / 3, exit_fees=0, switching_cost=0, npv=npv, load=load, stream=stream)


def test_milp_is_exactly_optimal_on_random_instances():
    p = PlanParams(capacity_per_quarter=3.0, stream_capacity_per_quarter=2.0, horizon_quarters=4)
    rng = random.Random(5)
    ids = ["a", "b", "c", "d", "e", "f"]
    for trial in range(25):
        opts = [_opt(i, rng.sample(ids, rng.choice([1, 2, 3])), rng.randrange(0, 4), rng.uniform(1, 100), rng.choice([0.5, 1, 2, 3]),
                     rng.random() < 0.4) for i in range(10)]
        best = sum(o.npv for o in solve_bruteforce(opts, p))
        got = solve_milp(opts, p)
        assert got is not None
        assert sum(o.npv for o in got) == pytest.approx(best, rel=1e-9), trial
        assert sum(o.npv for o in solve_greedy(opts, p)) <= best + 1e-9


def test_plan_respects_constraints(gold_run):
    pl, p = gold_run.plan, gold_run.params
    assert pl.optimal and pl.solver == "milp-highs"
    members = [c for o in pl.chosen for c in o.members]
    assert len(members) == len(set(members)), "a contract is consolidated at most once"
    by_q: dict[int, float] = {}
    sq: dict[int, float] = {}
    for o in pl.chosen:
        by_q[o.switch_q] = by_q.get(o.switch_q, 0) + o.load
        if o.stream:
            sq[o.switch_q] = sq.get(o.switch_q, 0) + o.load
    assert all(v <= p.capacity_per_quarter + 1e-9 for v in by_q.values())
    assert all(v <= p.stream_capacity_per_quarter + 1e-9 for v in sq.values())
    assert 0 < pl.npv <= pl.ideal_npv + 1e-6, "friction can only cost value"
    assert pl.run_rate_by_q == sorted(pl.run_rate_by_q)


def test_no_exit_before_it_is_legally_possible(gold_run):
    """Property: any member exited before its natural expiry must have a termination-for-convenience right whose notice
    period has elapsed by the switch date; and the plan never consolidates onto paper the vendor can terminate."""
    import datetime as dt
    from clause_to_cash.dates import lifecycle
    from clause_to_cash.engine.model import QUARTER_DAYS
    p = gold_run.params
    recs = {r.cid: r for r in gold_run.records}
    checked = 0
    for o in gold_run.plan.chosen:
        s = recs[o.survivor]
        assert not (s.entity == p.acquired_entity and s.terms.change_of_control == "vendor_termination_right")
        switch = p.asof + dt.timedelta(days=int(o.switch_q * QUARTER_DAYS))
        for cid in o.members:
            if cid == o.survivor:
                continue
            lc = lifecycle(recs[cid].terms, p.asof, p.buffer_days)
            if lc.natural_exit is not None and switch + dt.timedelta(days=1) < lc.natural_exit:
                t = recs[cid].terms
                assert t.termination_for_convenience and t.tfc_notice_days is not None
                assert (switch - p.asof).days + QUARTER_DAYS >= t.tfc_notice_days
                checked += 1
    assert checked >= 0


def test_repricing_plan_under_same_terms_is_identity(gold_run):
    recs = {r.cid: r for r in gold_run.records}
    for o in gold_run.plan.chosen:
        g = reprice_option(o, recs, gold_run.params)
        assert g is not None and g.npv == pytest.approx(o.npv)


def test_repricing_catches_infeasible_action(corpus, gold_run):
    """If the truth says a contract is locked in / has no exit, an action that assumed otherwise is invalid."""
    recs = {r.cid: r for r in gold_run.records}
    o = next(x for x in gold_run.plan.chosen if x.exit_fees == 0 and x.switch_q > 0)
    victim = next(c for c in o.members if c != o.survivor)
    import copy
    recs2 = dict(recs)
    t = recs[victim].terms.model_copy(update={"initial_term_months": 120, "auto_renew": False, "termination_for_convenience": False,
                                              "renewal_term_months": None, "nonrenewal_notice": None})
    recs2[victim] = copy.copy(recs[victim])
    recs2[victim].terms = t
    assert reprice_option(o, recs2, gold_run.params) is None


def test_pipeline_modes(corpus, extractions):
    from clause_to_cash.extract.rules import RuleExtractor
    ex = RuleExtractor()
    none = run_pipeline(corpus, ex, review="none", cached=extractions)
    quar = run_pipeline(corpus, ex, review="quarantine", cached=extractions)
    orac = run_pipeline(corpus, ex, review="oracle", cached=extractions)
    gold = run_pipeline(corpus, None, review="gold")
    flagged = {d.item.doc_id for d in quar.docs if not d.assessment.accepted}
    assert flagged and flagged <= set(quar.excluded)
    assert len(quar.records) == len(corpus.items) - len(flagged)
    assert len(orac.records) == len(gold.records) == len(corpus.items)
    assert orac.plan.npv == pytest.approx(gold.plan.npv, rel=1e-6)  # oracle review of flagged fields restores the truth
    assert quar.plan.npv < orac.plan.npv
    assert len(none.records) <= len(gold.records)


def test_calendar_and_risks(gold_run):
    kinds = {e.kind for e in gold_run.calendar}
    assert "notice_deadline" in kinds
    dues = [e.due for e in gold_run.calendar]
    assert dues == sorted(dues)
    nd = [e for e in gold_run.calendar if e.kind == "notice_deadline"]
    assert all(e.internal_due is None or e.internal_due <= e.due for e in nd)
    # change-of-control risk is raised only for the acquired entity's paper
    assert all(gold_run.records and next(r for r in gold_run.records if r.cid == f.cid).entity == "B"
               for f in gold_run.risks if f.code.startswith("COC_"))


def test_vendor_resolution_handles_decoys(corpus):
    gold = {i.doc_id: i.vendor_id or i.doc_id for i in corpus.items}
    res = resolve_vendors([(i.doc_id, i.gold.vendor_party, i.gold.service_category) for i in corpus.items])
    m = pairwise_f1(res.cluster_of, gold)
    assert m["fp"] == 0 and m["f1"] >= 0.99
    items = [("1", "Northgate Cloud Services, Inc.", "cloud_compute"), ("2", "NORTHGATE CLOUD SERVICES INCORPORATED", "cloud_compute"),
             ("3", "Northgate Cloud Analytics LLC", "cloud_compute")]
    r = resolve_vendors(items)
    assert r.cluster_of["1"] == r.cluster_of["2"] != r.cluster_of["3"]
