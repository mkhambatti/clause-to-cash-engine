"""Contract risk flags that matter for the merger (separate from savings)."""
from __future__ import annotations

from dataclasses import dataclass

from ..dates import lifecycle
from ..pricing import cost
from .calendar import annual_run_rate
from .model import ContractRecord, PlanParams


@dataclass
class RiskFlag:
    cid: str
    code: str
    severity: str  # high | medium | low
    message: str
    exposure_usd: float


def assess_risks(records: list[ContractRecord], p: PlanParams) -> list[RiskFlag]:
    flags: list[RiskFlag] = []
    for r in records:
        t = r.terms
        annual = annual_run_rate(r, p.asof)
        triggered = r.entity == p.acquired_entity  # closing is a change of control of the *acquired* company only
        if triggered and t.change_of_control == "vendor_termination_right":
            flags.append(RiskFlag(r.cid, "COC_VENDOR_TERMINATION", "high",
                                  f"Vendor may terminate on the {p.closing} change of control - service continuity and repricing leverage at risk",
                                  annual))
        elif triggered and t.change_of_control == "consent_required":
            flags.append(RiskFlag(r.cid, "COC_CONSENT_REQUIRED", "medium",
                                  "Assignment/change of control needs vendor consent - obtain before any re-papering", annual))
        if t.exclusive_requirements:
            flags.append(RiskFlag(r.cid, "EXCLUSIVITY", "medium",
                                  "Requirements exclusivity blocks moving volume to another vendor until exit", annual))
        if t.min_annual_commit_usd and t.pricing is not None:
            used = cost(t.pricing, r.units)
            if used < t.min_annual_commit_usd * 0.999:
                short = t.min_annual_commit_usd - used
                flags.append(RiskFlag(r.cid, "SHELFWARE", "medium" if short > 0.1 * t.min_annual_commit_usd else "low",
                                      f"Usage covers only {used / t.min_annual_commit_usd:.0%} of the take-or-pay commitment", short))
        lc = lifecycle(t, p.asof, p.buffer_days)
        if lc.status == "locked":
            flags.append(RiskFlag(r.cid, "AUTO_RENEWED_LOCK_IN", "medium",
                                  f"Notice window already missed; committed until {lc.period.end if lc.period else '?'}", annual))
    return flags
