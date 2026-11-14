"""Sequencing planner: choose options to maximise NPV subject to real-world constraints.

Weighted set-packing with side constraints, solved exactly as a MILP (scipy/HiGHS):

    maximise   sum_o  npv_o * y_o
    subject to sum_{o contains contract c} y_o <= 1            for every contract   (each contract is consolidated at most once)
               sum_{o switching in quarter q}  load_o * y_o <= capacity            for every quarter    (integration team bandwidth)
               sum_{o streaming-stack, quarter q} load_o * y_o <= stream capacity  for every quarter    (shared with platform convergence)
               y_o in {0, 1}

If scipy is unavailable a greedy solver is used (and reported as such).  An exhaustive solver exists
for tiny instances so tests can verify the MILP formulation is exact.
"""
from __future__ import annotations

import itertools
from dataclasses import dataclass, field

from .model import PlanParams
from .opportunities import Option, generate_options
from .model import ContractRecord


@dataclass
class Plan:
    chosen: list[Option]
    npv: float
    one_time: float
    run_rate_by_q: list[float]
    solver: str
    optimal: bool
    ideal_npv: float = 0.0
    ideal_run_rate: float = 0.0
    ideal_run_rate_by_q: list[float] = field(default_factory=list)
    n_options: int = 0
    provisional_npv: float = 0.0
    notes: list[str] = field(default_factory=list)

    @property
    def final_run_rate(self) -> float:
        return self.run_rate_by_q[-1] if self.run_rate_by_q else 0.0

    def pct_by(self, quarter_index: int) -> float:
        f = self.final_run_rate
        return self.run_rate_by_q[quarter_index] / f if f else 0.0


def _run_rate(chosen: list[Option], q_total: int) -> list[float]:
    return [sum(o.annual_saving for o in chosen if o.switch_q <= q) for q in range(q_total)]


def _conflicts(chosen: list[Option], cand: Option) -> bool:
    used = {c for o in chosen for c in o.members}
    return any(m in used for m in cand.members)


def solve_greedy(options: list[Option], p: PlanParams) -> list[Option]:
    cap: dict[int, float] = {}
    scap: dict[int, float] = {}
    chosen: list[Option] = []
    for o in sorted(options, key=lambda o: -o.npv):
        if _conflicts(chosen, o):
            continue
        if cap.get(o.switch_q, 0.0) + o.load > p.capacity_per_quarter + 1e-9:
            continue
        if o.stream and scap.get(o.switch_q, 0.0) + o.load > p.stream_capacity_per_quarter + 1e-9:
            continue
        chosen.append(o)
        cap[o.switch_q] = cap.get(o.switch_q, 0.0) + o.load
        if o.stream:
            scap[o.switch_q] = scap.get(o.switch_q, 0.0) + o.load
    return chosen


def solve_milp(options: list[Option], p: PlanParams, capacity: bool = True) -> list[Option] | None:
    try:
        import numpy as np
        from scipy.optimize import Bounds, LinearConstraint, milp
    except Exception:  # pragma: no cover - optional dependency
        return None
    if not options:
        return []
    n = len(options)
    contracts = sorted({c for o in options for c in o.members})
    cidx = {c: i for i, c in enumerate(contracts)}
    rows = []
    ub = []
    for c in contracts:
        row = np.zeros(n)
        for j, o in enumerate(options):
            if c in o.members:
                row[j] = 1.0
        rows.append(row)
        ub.append(1.0)
    if capacity:
        for q in range(p.horizon_quarters):
            row = np.zeros(n)
            srow = np.zeros(n)
            for j, o in enumerate(options):
                if o.switch_q == q:
                    row[j] = o.load
                    if o.stream:
                        srow[j] = o.load
            rows.append(row)
            ub.append(p.capacity_per_quarter)
            rows.append(srow)
            ub.append(p.stream_capacity_per_quarter)
    _ = cidx
    a = np.vstack(rows)
    res = milp(c=-np.array([o.npv for o in options]), constraints=LinearConstraint(a, -np.inf, np.array(ub)),
               integrality=np.ones(n), bounds=Bounds(0, 1))
    if not res.success or res.x is None:
        return None
    return [o for o, x in zip(options, res.x) if x > 0.5]


def solve_bruteforce(options: list[Option], p: PlanParams) -> list[Option]:
    """Exhaustive optimum (tests only; exponential)."""
    best: list[Option] = []
    best_v = 0.0
    for k in range(len(options) + 1):
        for combo in itertools.combinations(options, k):
            ok = True
            used: set[str] = set()
            cap: dict[int, float] = {}
            scap: dict[int, float] = {}
            for o in combo:
                if used & set(o.members):
                    ok = False
                    break
                used |= set(o.members)
                cap[o.switch_q] = cap.get(o.switch_q, 0.0) + o.load
                if o.stream:
                    scap[o.switch_q] = scap.get(o.switch_q, 0.0) + o.load
            if not ok or any(v > p.capacity_per_quarter + 1e-9 for v in cap.values()) or \
                    any(v > p.stream_capacity_per_quarter + 1e-9 for v in scap.values()):
                continue
            v = sum(o.npv for o in combo)
            if v > best_v:
                best_v, best = v, list(combo)
    return best


def plan(records: list[ContractRecord], p: PlanParams, use_milp: bool = True) -> Plan:
    options = generate_options(records, p)
    chosen = solve_milp(options, p) if use_milp else None
    solver = "milp-highs"
    optimal = True
    if chosen is None:
        chosen = solve_greedy(options, p)
        solver = "greedy"
        optimal = False
    q_total = p.horizon_quarters
    # "ideal": same clusters, ignoring notice windows, exit costs, lead times and team capacity
    ideal_opts = generate_options(records, p, ideal=True)
    ideal = solve_milp(ideal_opts, p, capacity=False)
    if ideal is None:
        ideal = solve_greedy(ideal_opts, PlanParams(**{**p.__dict__, "capacity_per_quarter": 1e9, "stream_capacity_per_quarter": 1e9}))
    rr = _run_rate(chosen, q_total)
    return Plan(
        chosen=sorted(chosen, key=lambda o: (o.switch_q, -o.npv)),
        npv=sum(o.npv for o in chosen), one_time=sum(o.one_time for o in chosen), run_rate_by_q=rr,
        solver=solver, optimal=optimal, ideal_npv=sum(o.npv for o in ideal),
        ideal_run_rate=sum(o.annual_saving for o in ideal), ideal_run_rate_by_q=_run_rate(ideal, q_total), n_options=len(options),
        provisional_npv=sum(o.npv for o in chosen if o.provisional))
