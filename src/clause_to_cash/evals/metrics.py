"""Field comparators and extraction scoring against ground truth."""
from __future__ import annotations

import re
from typing import Any

from ..schema import FIELD_LIST, FIELDS, Extraction, Terms


def _norm_str(s: Any) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(s).lower()).strip()


def values_equal(name: str, pred: Any, gold: Any) -> bool:
    spec = FIELDS[name]
    if pred is None or gold is None:
        return pred is None and gold is None
    k = spec.kind
    try:
        if k in ("bool", "enum", "date"):
            return pred == gold
        if k == "int":
            return int(pred) == int(gold)
        if k == "float":
            tol = 0.51 if name == "min_annual_commit_usd" else 1e-9
            return abs(float(pred) - float(gold)) <= tol
        if k == "str":
            return _norm_str(pred) == _norm_str(gold)
        if k == "duration":
            return int(pred["amount"]) == int(gold["amount"]) and pred["unit"] == gold["unit"]
        if k == "pricing":
            if _norm_str(pred.get("unit")) != _norm_str(gold.get("unit")) or pred.get("tier_mode") != gold.get("tier_mode"):
                return False
            pt, gt = pred.get("tiers") or [], gold.get("tiers") or []
            if len(pt) != len(gt):
                return False
            for a, b in zip(pt, gt):
                if abs(float(a["lower"]) - float(b["lower"])) > 1e-6:
                    return False
                if (a.get("upper") is None) != (b.get("upper") is None):
                    return False
                if a.get("upper") is not None and abs(float(a["upper"]) - float(b["upper"])) > 1e-6:
                    return False
                if abs(float(a["unit_price"]) - float(b["unit_price"])) > 1e-9 * max(1.0, abs(float(b["unit_price"]))):
                    return False
            return True
    except (KeyError, TypeError, ValueError):
        return False
    return False


def score_fields(ext: Extraction, gold: Terms) -> dict[str, bool]:
    g = gold.model_dump(mode="json")
    return {f.name: values_equal(f.name, ext.get(f.name).value, g.get(f.name)) for f in FIELD_LIST}
