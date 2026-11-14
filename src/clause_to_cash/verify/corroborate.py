"""Independent corroboration: does the *quote* actually support the extracted *value*?

This is a second, simpler code path from the extractors.  It deliberately accepts any
generic English rendering of a number or date (digits, number words, "million", "half"),
but only returns SUPPORTS when the claimed value is derivable from the quoted words.
Anything it cannot interpret is UNKNOWN, which the gate treats conservatively.
"""
from __future__ import annotations

import datetime as dt
import re
from enum import Enum
from typing import Any

from ..taxonomy import CATEGORY_BY_KEY, CATEGORIES
from ..textutil import find_dates, find_money, find_numbers, fold, norm_ws


class Corr(str, Enum):
    SUPPORTS = "supports"
    CONTRADICTS = "contradicts"
    UNKNOWN = "unknown"


_UNIT_RX = re.compile(r"\s*(?:\(\s*\d+\s*\)\s*)?[-\s]*(business\s+days?|days?|months?|years?|weeks?)\b", re.I)


def duration_candidates(text: str) -> list[tuple[int, str]]:
    """(amount, unit) for every '<number> <unit>' in the text; years are converted to months."""
    out: list[tuple[int, str]] = []
    t = fold(text)
    for val, s, e in find_numbers(t):
        m = _UNIT_RX.match(t, e)
        if not m or val != int(val):
            continue
        u = m.group(1).lower()
        n = int(val)
        if u.startswith("year"):
            out.append((n * 12, "months"))
        elif u.startswith("month"):
            out.append((n, "months"))
        elif u.startswith("week"):
            out.append((n * 7, "days"))
        elif u.startswith("business"):
            continue
        else:
            out.append((n, "days"))
    return out


_FRACTIONS = {"half": 50.0, "one-half": 50.0, "one half": 50.0, "quarter": 25.0, "one-quarter": 25.0,
              "three-quarters": 75.0, "three quarters": 75.0, "entire": 100.0, "full": 100.0, "all": 100.0}


def percent_candidates(text: str) -> list[float]:
    t = fold(text)
    out: list[float] = []
    for val, s, e in find_numbers(t):
        if re.match(r"\s*(?:\(\s*\d+(?:\.\d+)?\s*%\s*\)\s*)?(?:%|percent|per cent)", t[e:], re.I):
            out.append(val)
    low = t.lower()
    for w, v in _FRACTIONS.items():
        if re.search(rf"\b{re.escape(w)}\b", low) and w in ("half", "one-half", "one half", "quarter", "one-quarter",
                                                          "three-quarters", "three quarters"):
            out.append(v)
    return out


def _bool_support(value: bool, positive: bool, negative: bool) -> Corr:
    if value:
        if negative and not positive:
            return Corr.CONTRADICTS
        return Corr.SUPPORTS if positive and not negative else (Corr.UNKNOWN if not positive else Corr.CONTRADICTS)
    if negative:
        return Corr.SUPPORTS
    return Corr.CONTRADICTS if positive else Corr.UNKNOWN


def _affiliate_implied(low: str) -> str | None:
    kv = re.search(r"affiliate purchasing:\s*([^\n]+)", low)
    if kv:
        v = kv.group(1)
        if re.search(r"not permitted|prohibit|\bno\b", v):
            return "prohibited"
        if re.search(r"consent|approval", v):
            return "consent_required"
        if re.search(r"allowed|permitted|yes", v):
            return "allowed"
    aff = re.search(r"affiliat|subsidiar|group compan|related compan", low)
    internal = re.search(r"internal use only|solely for (?:the )?customer|for customer'?s? (?:sole|internal)", low)
    if not (aff or internal):
        return None
    if re.search(r"\bno affiliate|may not|shall not|not be (?:used|purchased|permitted)|prohibit|internal use only|solely for", low):
        return "prohibited"
    if re.search(r"consent|approval|approve|permission", low):
        return "consent_required"
    if re.search(r"may (?:purchase|order|use|procure|buy|access)|entitled|(?:purchase|order)s? .{0,60}(?:rates|pricing|under this)", low):
        return "allowed"
    return None


def _control_implied(low: str) -> str | None:
    kv = re.search(r"assignment / change of control:\s*([^\n]+)", low)
    if kv:
        v = kv.group(1)
        if "vendor may terminate" in v:
            return "vendor_termination_right"
        if "consent" in v:
            return "consent_required"
        if re.search(r"free", v):
            return "free"
        if re.search(r"not addressed|silent|none", v):
            return "silent"
    cues = re.search(r"change (?:of|in) control|assign|merger|successor|acquisition|acquir", low)
    if not cues:
        return None
    if re.search(r"vendor may terminate|right to terminate", low) and re.search(r"change (?:of|in) control|acquir", low):
        return "vendor_termination_right"
    neg = re.search(r"(?:may not|shall not|(?:no|neither) party (?:may|shall)|not) (?:\w+ ){0,4}assign|assign\w* .{0,80}(?:requires?|subject to) .{0,30}consent", low)
    if neg and "consent" in low:
        return "consent_required"
    if re.search(r"(?:may|can) assign .{0,80}without", low) or re.search(r"without .{0,40}consent.{0,120}(?:successor|merger|affiliate)", low):
        return "free"
    if re.search(r"binding (?:up)?on .{0,120}(?:successors|assigns)", low) and "consent" not in low:
        return "silent"
    return None


def corroborate(field: str, value: Any, quotes: list[str]) -> Corr:  # noqa: C901 - one switch, intentionally flat
    if value is None:
        return Corr.UNKNOWN
    text = fold(" ".join(quotes))
    low = text.lower()
    if not text.strip():
        return Corr.UNKNOWN

    if field == "effective_date":
        ds = [d for d, _, _ in find_dates(text)]
        try:
            v = dt.date.fromisoformat(str(value))
        except ValueError:
            return Corr.CONTRADICTS
        return Corr.SUPPORTS if v in ds else (Corr.CONTRADICTS if ds else Corr.UNKNOWN)

    if field in ("initial_term_months", "renewal_term_months"):
        months = [a for a, u in duration_candidates(text) if u == "months"]
        if not months:
            return Corr.UNKNOWN
        iv = int(value)
        if iv in months:
            return Corr.SUPPORTS
        if field == "initial_term_months" and any(iv == a + b for i, a in enumerate(months) for j, b in enumerate(months) if i != j):
            return Corr.SUPPORTS  # base term + amendment extension
        return Corr.CONTRADICTS

    if field == "nonrenewal_notice":
        cands = duration_candidates(text)
        if not cands:
            return Corr.UNKNOWN
        want = (int(value["amount"]), value["unit"])
        if want in cands:
            return Corr.SUPPORTS
        return Corr.CONTRADICTS  # includes the "3 months vs 90 days" unit confusion

    if field == "tfc_notice_days":
        days = [a for a, u in duration_candidates(text) if u == "days"]
        if not days:
            return Corr.UNKNOWN
        return Corr.SUPPORTS if int(value) in days else Corr.CONTRADICTS

    if field in ("early_termination_fee_pct", "annual_escalator_pct"):
        pcs = percent_candidates(text)
        if not pcs:
            return Corr.UNKNOWN
        return Corr.SUPPORTS if any(abs(p - float(value)) < 1e-9 for p in pcs) else Corr.CONTRADICTS

    if field == "min_annual_commit_usd":
        ms = find_money(text)
        if not ms:
            return Corr.UNKNOWN
        return Corr.SUPPORTS if any(abs(m - float(value)) < 0.5 for m in ms) else Corr.CONTRADICTS

    if field == "pricing":
        nums = [n for n, _, _ in find_numbers(text)]
        if not nums:
            return Corr.UNKNOWN
        tiers = value.get("tiers") or []
        for t in tiers:
            if not any(abs(n - float(t["unit_price"])) <= 1e-9 * max(1.0, abs(n)) for n in nums):
                return Corr.CONTRADICTS
            if t["lower"] and not any(abs(n - float(t["lower"])) < 1e-6 for n in nums):
                return Corr.CONTRADICTS
        if value.get("unit") and str(value["unit"]).lower() not in low:
            return Corr.CONTRADICTS
        grad = re.search(r"graduated|marginal|first [\d,]+|next [\d,]+|tier in which (?:it|such unit|each unit) falls", low)
        alln = re.search(r"all[- ]units|all units|volume[- ]discount|every (?:unit|\w+) is|apply to all|applies to all", low)
        mode = value.get("tier_mode")
        if mode == "graduated" and alln and not grad:
            return Corr.CONTRADICTS
        if mode == "all_units" and grad and not alln:
            return Corr.CONTRADICTS
        if (mode == "graduated" and grad) or (mode == "all_units" and alln):
            return Corr.SUPPORTS
        return Corr.UNKNOWN  # prices match but the quotes do not say how tiers apply

    if field == "affiliate_purchasing":
        implied = _affiliate_implied(low)
        if implied is None:
            return Corr.SUPPORTS if value == "silent" and not re.search(r"affiliat", low) else Corr.UNKNOWN
        return Corr.SUPPORTS if implied == value else Corr.CONTRADICTS

    if field == "change_of_control":
        implied = _control_implied(low)
        if implied is None:
            return Corr.UNKNOWN
        return Corr.SUPPORTS if implied == value else Corr.CONTRADICTS

    if field == "auto_renew":
        neg = bool(re.search(r"(?:will|shall) not (?:be )?(?:renew|automatic)|does not (?:automatically )?renew|not automatically renew|"
                             r"no (?:automatic|evergreen)|auto-renewal:\s*no|any renewal requires|not be renewed", low))
        pos = bool(re.search(r"automatic|evergreen|successive|renew", low)) and not neg
        return _bool_support(bool(value), pos, neg)

    if field == "termination_for_convenience":
        neg = bool(re.search(r"neither party may|may not terminate|no right to terminate|no termination for convenience|not permitted|not be permitted|only for cause|not (?:be )?entitled", low))
        pos = bool(re.search(r"convenience|without cause|may terminate|can terminate|early terminat|at any time", low)) and not neg
        return _bool_support(bool(value), pos, neg)

    if field == "exclusive_requirements":
        kv = re.search(r"exclusive requirements:\s*(yes|no)\b", low)
        if kv:
            return _bool_support(bool(value), kv.group(1) == "yes", kv.group(1) == "no")
        neg = bool(re.search(r"nothing .{0,60}obligat|no obligation|non-exclusive|not (?:required|obligated) to", low))
        pos = bool(re.search(r"exclusiv(?:e|ely)(?! remed)|sole (?:source|supplier)|all of (?:its|customer's) requirements", low)) and not neg
        return _bool_support(bool(value), pos, neg)

    if field == "mfn":
        kv = re.search(r"\bmfn:\s*(yes|no)\b", low)
        if kv:
            return _bool_support(bool(value), kv.group(1) == "yes", kv.group(1) == "no")
        neg = bool(re.search(r"no representation|makes no (?:warranty|representation)|no most.favou?red", low))
        pos = bool(re.search(r"no less favou?rable|most.favou?red|lowest price|similarly situated", low)) and not neg
        return _bool_support(bool(value), pos, neg)

    if field in ("governing_law", "customer_party", "vendor_party"):
        return Corr.SUPPORTS if norm_ws(str(value)).lower() in norm_ws(text).lower() else Corr.CONTRADICTS

    if field == "service_category":
        cat = CATEGORY_BY_KEY.get(str(value))
        if cat is None:
            return Corr.CONTRADICTS
        toks = _tokens(cat.phrases[0] + " " + cat.phrases[1] + " " + cat.phrases[2])
        mine = toks & _tokens(low)
        if mine:
            return Corr.SUPPORTS
        for other in CATEGORIES:
            if other.key != cat.key and _tokens(" ".join(other.phrases)) & _tokens(low) - toks:
                return Corr.CONTRADICTS
        return Corr.UNKNOWN

    return Corr.UNKNOWN


_STOP = {"services", "service", "and", "the", "for", "of", "software", "platform", "management", "data"}


def _tokens(s: str) -> set[str]:
    return {t for t in re.findall(r"[a-z][a-z-]{2,}", s.lower()) if t not in _STOP}


_ABSENT_EVIDENCE = {
    "min_annual_commit_usd": r"no minimum|not subject to any minimum|minimum[^.:\n]*:\s*(?:none|n/a)|without (?:any )?minimum|no (?:volume )?commitment",
    "annual_escalator_pct": r"fixed|no (?:annual )?(?:increase|escalat)|(?:increase|escalat\w*)[^.:\n]*:\s*(?:none|n/a)",
    "nonrenewal_notice": r"not renew|no automatic|auto-renewal:\s*no|any renewal requires|shall not be renewed|will not renew|does not renew|shall expire",
    "early_termination_fee_pct": r"not permitted|neither party may|no right to terminate|no termination|no (?:early termination )?(?:fee|charge)",
    "renewal_term_months": r"not renew|no automatic|auto-renewal:\s*no|any renewal requires|shall not be renewed|will not renew|does not renew",
    "tfc_notice_days": r"not permitted|neither party may|no right to terminate|no termination",
}


def corroborate_absence(field: str, quotes: list[str]) -> Corr:
    """Does a quote explicitly state that the clause/value does not exist?"""
    rx = _ABSENT_EVIDENCE.get(field)
    if rx is None or not quotes:
        return Corr.UNKNOWN
    return Corr.SUPPORTS if re.search(rx, fold(" ".join(quotes)).lower()) else Corr.UNKNOWN
