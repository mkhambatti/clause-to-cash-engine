"""Engine-side data model: a verified/provisional contract plus planning parameters."""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

from ..corpus import ACQUIRED_ENTITY, ASOF, CLOSING
from ..schema import Terms
from ..taxonomy import CATEGORY_BY_KEY


@dataclass
class ContractRecord:
    cid: str
    entity: str
    terms: Terms
    units: float
    vendor_cluster: str = ""
    vendor_display: str = ""
    provisional: bool = False  # True if any critical field is still awaiting human review
    review_fields: list[str] = field(default_factory=list)

    @property
    def category(self) -> str:
        return self.terms.service_category or ""

    @property
    def stream(self) -> bool:
        c = CATEGORY_BY_KEY.get(self.category)
        return bool(c and c.stream)


@dataclass(frozen=True)
class PlanParams:
    asof: dt.date = ASOF
    closing: dt.date = CLOSING
    acquired_entity: str = ACQUIRED_ENTITY
    horizon_quarters: int = 12
    discount_rate: float = 0.08
    buffer_days: int = 14  # internal safety margin ahead of every contractual notice deadline
    capacity_per_quarter: float = 4.0  # contract migrations the integration org can run in parallel
    stream_capacity_per_quarter: float = 2.0  # of which streaming-stack (competes with P+/HBO Max convergence)
    paper_load: float = 0.25  # relative effort of re-papering onto the same vendor vs migrating vendors
    same_vendor_switch_factor: float = 0.1  # switching cost multiplier when volume stays with the same vendor


QUARTER_DAYS = 91.3125
