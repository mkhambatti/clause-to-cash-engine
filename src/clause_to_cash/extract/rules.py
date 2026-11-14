"""Offline baseline extractor: regex + heuristics, no model, no network.

Purpose: (1) a zero-cost baseline every LLM extractor must beat, (2) a second, *independent*
opinion for ensembling, (3) a way to run the whole system - gate, engines, planner, dashboard -
on a laptop with no API key.

HONESTY NOTE: these patterns were written against drafting styles 0-2 of the synthetic corpus
only and never tuned on style 3 (held-out).  High accuracy on styles 0-2 is therefore *expected*
and not evidence of generalisation; the drop on style 3 is the point of the held-out split.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Optional

from ..schema import FIELD_LIST, Extraction, FieldExtraction
from ..taxonomy import CATEGORIES
from ..textutil import find_dates, find_money, fold, sentences
from ..verify.corroborate import duration_candidates, percent_candidates
from ..verify.cues import amendment_start

HI, MID, ABSENT = 0.92, 0.60, 0.80


def _fe(value, quotes=None, conf=HI) -> FieldExtraction:  # type: ignore[no-untyped-def]
    return FieldExtraction(value=value, quotes=[q for q in (quotes or []) if q], confidence=conf if quotes else ABSENT)


def _sents(text: str) -> list[str]:
    # whitespace is collapsed (line-wrapped "scanned" layouts); quotes still ground because locate() is whitespace-tolerant
    return [re.sub(r"\s+", " ", text[s:e]).strip() for s, e in sentences(text)]


# ---- category scoring (tokens from the three seen phrasings only) ---------- #
_STOP = {"services", "service", "and", "the", "for", "of", "software", "platform", "management", "data", "as", "a"}


def _toks(s: str) -> set[str]:
    return {t for t in re.findall(r"[a-z][a-z-]{2,}", s.lower()) if t not in _STOP}


_CAT_TOKS = {c.key: _toks(" ".join(c.phrases)) for c in CATEGORIES}
_TOK_DF: dict[str, int] = {}
for _k, _v in _CAT_TOKS.items():
    for _t in _v:
        _TOK_DF[_t] = _TOK_DF.get(_t, 0) + 1


@dataclass
class _Group:
    start: int
    end: int
    unit: str
    tiers: list[dict]
    quote: str


def _num(s: str) -> float:
    return float(s.replace(",", ""))


def _price(s: str) -> float:
    return float(s.replace("$", "").replace(",", ""))


class RuleExtractor:
    name = "rules-v1"

    # ------------------------------------------------------------------ #
    def extract(self, doc_id: str, text: str) -> Extraction:
        t0 = time.perf_counter()
        text = re.sub(r"(?<=\w)-[ \t]*\n[ \t]*(?=\w)", "-", fold(text))  # rejoin line-end hyphenation
        a0 = amendment_start(text)
        base = text[:a0] if a0 is not None else text
        amend = text[a0:] if a0 is not None else ""
        out: dict[str, FieldExtraction] = {}
        out.update(self._parties(base))
        out["effective_date"] = self._effective_date(base)
        out["service_category"] = self._category(base)
        out["initial_term_months"] = self._term(base, amend)
        out.update(self._renewal(base, amend))
        out.update(self._tfc(base))
        out["min_annual_commit_usd"] = self._commit(base)
        out["pricing"] = self._pricing(text)
        out["annual_escalator_pct"] = self._escalator(base)
        out["affiliate_purchasing"] = self._affiliates(base)
        out["change_of_control"] = self._control(base)
        out["exclusive_requirements"] = self._exclusive(base)
        out["mfn"] = self._mfn(base)
        out["governing_law"] = self._law(base)
        for f in FIELD_LIST:
            out.setdefault(f.name, _fe(None))
        return Extraction(doc_id=doc_id, extractor=self.name, fields=out, latency_s=time.perf_counter() - t0)

    # ------------------------------------------------------------------ #
    def _parties(self, t: str) -> dict[str, FieldExtraction]:
        res: dict[str, FieldExtraction] = {}
        m = re.search(r"^Customer:\s*(.+?)\s*$", t, re.M)
        v = re.search(r"^Vendor:\s*(.+?)\s*$", t, re.M)
        if m and v:
            res["customer_party"] = _fe(m.group(1), [m.group(0)])
            res["vendor_party"] = _fe(v.group(1), [v.group(0)])
            return res
        q = '["“]'
        # style 0: between CUSTOMER ("Customer") and VENDOR, a X corporation ("Vendor")
        m0 = re.search(rf"between\s+(.+?)\s+\({q}Customer{q}\)\s+and\s+(.+?),\s+an?\s+[A-Za-z ]+?\s+corporation\s+\({q}Vendor{q}\)", t, re.S)
        if m0:
            res["customer_party"] = _fe(re.sub(r"\s+", " ", m0.group(1)), [m0.group(0)])
            res["vendor_party"] = _fe(re.sub(r"\s+", " ", m0.group(2)), [m0.group(0)])
            return res
        # style 1: between VENDOR, a X corporation ("Vendor"), and CUSTOMER ("Customer")
        m1 = re.search(rf"between\s+(.+?),\s+an?\s+[A-Za-z ]+?\s+corporation\s+\({q}Vendor{q}\),?\s+and\s+(.+?)\s+\({q}Customer{q}\)", t, re.S)
        if m1:
            res["vendor_party"] = _fe(re.sub(r"\s+", " ", m1.group(1)), [m1.group(0)])
            res["customer_party"] = _fe(re.sub(r"\s+", " ", m1.group(2)), [m1.group(0)])
        return res

    def _effective_date(self, t: str) -> FieldExtraction:
        m = re.search(r"(?:dated as of|effective as of|Effective Date:)\s*([^\n(]{4,48})", t)
        if m:
            ds = find_dates(m.group(1))
            if ds:
                return _fe(ds[0][0].isoformat(), [m.group(0).strip()])
        return _fe(None)

    def _category(self, t: str) -> FieldExtraction:
        q = '["“]'
        for rx in (r"Services:[ \t]*([^\n]+)", rf"provide\s+(.+?)\s+\({q}the Services{q}\)|provide\s+(.+?)\s+\(the {q}Services{q}\)",
                   r"business of providing\s+(.+?);"):
            m = re.search(rx, t, re.S)
            if m:
                desc = next(g for g in m.groups() if g)
                toks = _toks(desc)
                scores = {k: sum(1.0 / _TOK_DF[x] for x in toks & v) for k, v in _CAT_TOKS.items()}
                best = max(scores, key=lambda k: scores[k])
                ranked = sorted(scores.values(), reverse=True)
                if scores[best] > 0:
                    conf = HI if ranked[0] - ranked[1] >= 0.5 else MID
                    return _fe(best, [m.group(0).strip()], conf)
        return _fe(None)

    # ------------------------------------------------------------------ #
    def _term(self, base: str, amend: str) -> FieldExtraction:
        total: Optional[int] = None
        quotes: list[str] = []
        for s in _sents(base):
            if re.search(r"initial term|^Term:|\bTerm:", s, re.I) and not re.search(r"renew|survive|liabilit|confiden|notice|amend|expir", s, re.I):
                months = [a for a, u in duration_candidates(s) if u == "months"]
                if months:
                    total, quotes = months[0], [s]
                    break
        if total is None:
            return _fe(None)
        for s in _sents(amend):
            m = re.search(r"extend(?:ing|ed|s)?\s+(?:the\s+)?(?:Initial Term\s+)?(?:set forth in Section [\d.]+\s+)?(?:is hereby extended\s+)?by\s+(?:a period of\s+)?(.{3,30})", s, re.I)
            m2 = re.search(r"Term extension:\s*\+?\s*(\d+)\s*months", s, re.I)
            if m2:
                total += int(m2.group(1))
                quotes.append(s)
            elif m:
                ms = [a for a, u in duration_candidates(m.group(1)) if u == "months"]
                if ms:
                    total += ms[0]
                    quotes.append(s)
        return _fe(total, quotes)

    def _renewal(self, base: str, amend: str) -> dict[str, FieldExtraction]:
        res: dict[str, FieldExtraction] = {}
        sents = _sents(base)
        neg = pos = None
        for s in sents:
            if re.search(r"not renew automatically|shall not be renewed|will not renew|no automatic renewal|Auto-Renewal:\s*No\b|does not renew", s, re.I):
                neg = s
            elif re.search(r"automatically (?:renew|extended)|Auto-Renewal:\s*Yes", s, re.I):
                pos = s
        if pos:
            res["auto_renew"] = _fe(True, [pos])
            m = re.search(r"(?:successive (?:renewal terms|periods) of|Auto-Renewal:\s*Yes\s*-?)\s*(.{3,40}?)(?:\s+each|\s*\(each|\n|,|$)", pos, re.I)
            rm = [a for a, u in duration_candidates(m.group(1)) if u == "months"] if m else []
            res["renewal_term_months"] = _fe(rm[0], [pos]) if rm else _fe(None)
            notice, nq = None, None
            for src in (base, amend):  # amendments (later) override
                for s in _sents(src):
                    mm = re.search(r"(?:at least|not less than|not fewer than|no less than|no later than)\s+(.{3,40}?)\s+(?:prior to|before)\b", s, re.I)
                    mk = re.search(r"Non-Renewal Notice(?:\s*\(amended\))?:\s*(.{3,25})", s, re.I)
                    cand = (mm.group(1) if mm else None) or (mk.group(1) if mk else None)
                    if cand and re.search(r"renew|Non-Renewal", s, re.I):
                        ds = duration_candidates(cand)
                        if ds:
                            notice, nq = {"amount": ds[0][0], "unit": ds[0][1]}, s
            if notice:
                res["nonrenewal_notice"] = _fe(notice, [nq])
            else:
                res["nonrenewal_notice"] = _fe(None)
        elif neg:
            res["auto_renew"] = _fe(False, [neg])
            res["renewal_term_months"] = _fe(None, [neg])
            res["nonrenewal_notice"] = _fe(None, [neg])
        else:
            res["auto_renew"] = _fe(False)
            res["renewal_term_months"] = _fe(None)
            res["nonrenewal_notice"] = _fe(None)
        return res

    def _tfc(self, t: str) -> dict[str, FieldExtraction]:
        res: dict[str, FieldExtraction] = {}
        kv = re.search(r"Termination for Convenience:\s*Customer[^\n]*", t)
        if kv:  # order-form line: "...: Customer - 60 days notice; ETF 50% of remaining fees"
            s = kv.group(0).strip()
            days = [a for a, u in duration_candidates(s) if u == "days"]
            pc = percent_candidates(s)
            res["termination_for_convenience"] = _fe(True, [s])
            res["tfc_notice_days"] = _fe(days[0], [s]) if days else _fe(None)
            res["early_termination_fee_pct"] = _fe(pc[0], [s]) if pc else _fe(None)
            return res
        ss = _sents(t)
        for i, s in enumerate(ss):
            if re.search(r"neither party may terminate .{0,40}convenience|no right to terminate .{0,40}without cause|"
                         r"Termination for Convenience:\s*Not permitted|No Termination for Convenience", s, re.I):
                qs = [s]
                if s.rstrip().endswith("Convenience.") and i + 1 < len(ss):  # heading sentence + operative sentence
                    qs.append(ss[i + 1])
                res["termination_for_convenience"] = _fe(False, qs)
                return res
            if re.search(r"terminate this agreement (?:for convenience|without cause)|Termination for Convenience:\s*Customer|"
                         r"to terminate this agreement without cause", s, re.I):
                res["termination_for_convenience"] = _fe(True, [s])
                days = [a for a, u in duration_candidates(s) if u == "days"]
                res["tfc_notice_days"] = _fe(days[0], [s]) if days else _fe(None)
                pc = percent_candidates(s)
                res["early_termination_fee_pct"] = _fe(pc[0], [s]) if pc else _fe(None)
                return res
        res["termination_for_convenience"] = _fe(False)
        return res

    def _commit(self, t: str) -> FieldExtraction:
        for s in _sents(t):
            if re.search(r"no minimum|not subject to any minimum|Minimum Commitment:\s*None", s, re.I):
                return _fe(None, [s])
            if re.search(r"minimum|take-or-pay", s, re.I):
                ms = find_money(s)
                if ms:
                    return _fe(round(ms[0], 2), [s])
        return _fe(None)

    def _escalator(self, t: str) -> FieldExtraction:
        for s in _sents(t):
            if re.search(r"Annual Price Increase:\s*None|fees are fixed|shall remain fixed", s, re.I):
                return _fe(None, [s])
            if re.search(r"anniversary|Annual Price Increase", s, re.I) and re.search(r"increase|adjust", s, re.I):
                pc = percent_candidates(s)
                if pc:
                    return _fe(pc[0], [s])
        return _fe(None)

    def _affiliates(self, t: str) -> FieldExtraction:
        for s in _sents(t):
            m = re.search(r"Affiliate Purchasing:\s*(.+)", s)
            if m:
                v = m.group(1).lower()
                val = "consent_required" if "consent" in v else ("prohibited" if ("not permitted" in v or "prohibit" in v) else "allowed")
                return _fe(val, [s])
            if re.search(r"affiliate", s, re.I) and re.search(r"purchase|order|\buse\b", s, re.I) and not re.search(r"indemnif", s, re.I):
                if re.search(r"internal use only|internal business purposes|no affiliate", s, re.I):
                    return _fe("prohibited", [s])
                if re.search(r"only with|solely with|prior written (?:consent|approval)", s, re.I):
                    return _fe("consent_required", [s])
                if re.search(r"may purchase|entitled to order|may order|shall be entitled", s, re.I):
                    return _fe("allowed", [s])
        return _fe("silent")

    def _control(self, t: str) -> FieldExtraction:
        sev = {"vendor_termination_right": 3, "consent_required": 2, "free": 1, "silent": 0}
        best: Optional[str] = None
        quotes: list[str] = []
        for s in _sents(t):
            m = re.search(r"Assignment / Change of Control:\s*(.+)", s)
            val = None
            if m:
                v = m.group(1).lower()
                val = ("vendor_termination_right" if "vendor may terminate" in v else "consent_required" if "consent" in v
                       else "free" if "free" in v else "silent")
            elif re.search(r"vendor may terminate", s, re.I) and re.search(r"change of control", s, re.I):
                val = "vendor_termination_right"
            elif re.search(r"(?:neither|no) part(?:y|ies)\b.{0,40}assign|may not assign|shall not assign", s, re.I):
                val = "consent_required"
            elif re.search(r"either part(?:y|ies)\b.{0,90}assign", s, re.I):
                val = "free"
            elif re.search(r"binding upon", s, re.I) and re.search(r"successors", s, re.I):
                val = "silent"
            if val is not None and (best is None or sev[val] > sev[best]):
                best = val
                quotes = [s]
            elif val is not None and val == best:
                quotes.append(s)
        if best is None:
            return _fe("silent")
        # when the winner is vendor_termination_right also cite the companion assignment sentence if present
        return _fe(best, quotes[:2])

    def _exclusive(self, t: str) -> FieldExtraction:
        for s in _sents(t):
            if re.search(r"nothing .{0,40}obligat|Exclusive Requirements:\s*No\b", s, re.I):
                return _fe(False, [s])
            if re.search(r"exclusively from Vendor|all of (?:its|Customer's) requirements|Exclusive Requirements:\s*Yes", s, re.I):
                return _fe(True, [s])
        return _fe(False)

    def _mfn(self, t: str) -> FieldExtraction:
        for s in _sents(t):
            if re.search(r"makes no representation .{0,40}prices|MFN:\s*No\b", s, re.I):
                return _fe(False, [s])
            if re.search(r"no less favou?rable|most.favou?red|MFN:\s*Yes", s, re.I):
                return _fe(True, [s])
        return _fe(False)

    def _law(self, t: str) -> FieldExtraction:
        m = re.search(r"governed\s+by(?:\s+and\s+construed\s+in\s+accordance\s+with)?\s+the\s+laws?\s+of\s+(?:the\s+State\s+of\s+)?([A-Z][a-z]+(?:\s+(?:and\s+)?[A-Z][a-z]+)?)", t)
        if m:
            return _fe(re.sub(r"\s+", " ", m.group(1)), [re.sub(r"\s+", " ", m.group(0))])
        m = re.search(r"Governing Law:\s*(.+)", t)
        return _fe(m.group(1).strip(), [m.group(0).strip()]) if m else _fe(None)

    # ------------------------------------------------------------------ #
    def _pricing(self, t: str) -> FieldExtraction:
        groups = self._g0(t) + self._g1(t) + self._g2(t)
        if not groups:
            return _fe(None)
        groups.sort(key=lambda g: g.start)
        g = groups[-1]  # the last schedule in document order supersedes earlier ones
        mode, mq = self._mode(t, g)
        if mode is None:
            return _fe(None)
        value = {"unit": g.unit, "tier_mode": mode, "tiers": g.tiers, "currency": "USD"}
        return _fe(value, [g.quote, mq] if mq and mq not in g.quote else [g.quote])

    def _mode(self, t: str, g: _Group) -> tuple[Optional[str], Optional[str]]:
        cands = []
        for mode, rx in (("graduated", r"graduated\s+basis|marginal\s+basis|Tier\s+Mode:\s*Graduated"),
                         ("all_units", r"all-units\s+basis|volume-discount\s+basis|Tier\s+Mode:\s*All-Units")):
            for m in re.finditer(rx, t, re.I):
                d = min(abs(m.start() - g.end), abs(g.start - m.end()))
                cands.append((d, mode, m.start()))
        if not cands:
            return None, None
        d, mode, pos = min(cands)
        if d > 700:
            return None, None
        for s, e in sentences(t):
            if s <= pos < e:
                return mode, t[s:e].strip()
        return mode, None

    def _g0(self, t: str) -> list[_Group]:
        rx = re.compile(r"Tier\s*(\d+):\s*(?:([\d,\.]+)\s+to\s+([\d,\.]+)|Above\s+([\d,\.]+))\s+(\S+)\s+per year at\s+(\$[\d,\.]+)\s+per\s+(\S+)")
        groups: list[_Group] = []
        cur: Optional[_Group] = None
        for m in rx.finditer(t):
            n = int(m.group(1))
            lower = _num(m.group(2)) if m.group(2) else _num(m.group(4))
            upper = _num(m.group(3)) if m.group(3) else None
            tier = {"lower": lower, "upper": upper, "unit_price": _price(m.group(6))}
            if n == 1 or cur is None:
                cur = _Group(m.start(), m.end(), m.group(7), [tier], m.group(0))
                groups.append(cur)
            else:
                cur.tiers.append(tier)
                cur.end = m.end()
                cur.quote = t[cur.start:cur.end]
        return groups

    def _g1(self, t: str) -> list[_Group]:
        groups: list[_Group] = []
        for m in re.finditer(r"(?:SCHEDULE A\.\s+PRICING\.\s+|following:\s+)?For\s+Annual\s+Volume\s+of\s+up\s+to\s.+?(?:basis|units of Annual Volume)[^.]*\.", t, re.S):
            para = re.sub(r"\s+", " ", m.group(0))
            body = re.split(r"(?<=[a-z])\.\s+(?=The foregoing)", para)[0]
            segs = [x.strip() for x in re.split(r";\s*", body)]
            tiers = []  # type: list[dict]
            unit = None
            for i, seg in enumerate(segs):
                pm = re.search(r"Unit Price shall be (\$[\d,\.]+) per (\S+?)(?:\.|$)", seg)
                if not pm:
                    continue
                unit = pm.group(2).rstrip(".")
                up = re.search(r"up to ([\d,\.]+)", seg)
                exc = re.search(r"in excess of ([\d,\.]+)", seg)
                if exc and up:
                    tiers.append({"lower": _num(exc.group(1)), "upper": _num(up.group(1)), "unit_price": _price(pm.group(1))})
                elif exc:
                    tiers.append({"lower": _num(exc.group(1)), "upper": None, "unit_price": _price(pm.group(1))})
                elif up:
                    tiers.append({"lower": 0.0, "upper": _num(up.group(1)), "unit_price": _price(pm.group(1))})
            if tiers and unit:
                groups.append(_Group(m.start(), m.end(), unit, tiers, m.group(0)))
        return groups

    def _g2(self, t: str) -> list[_Group]:
        rows = re.compile(r"\|\s*(\d+)\s*\|\s*([\d,\.]+)\s*\|\s*(and above|[\d,\.]+)\s*\|\s*(\$[\d,\.]+)\s*\|")
        groups: list[_Group] = []
        cur: Optional[_Group] = None
        for m in rows.finditer(t):
            n = int(m.group(1))
            tier = {"lower": _num(m.group(2)), "upper": None if m.group(3) == "and above" else _num(m.group(3)),
                    "unit_price": _price(m.group(4))}
            if n == 1 or cur is None:
                head = t.rfind("| Tier |", 0, m.start())
                um = re.search(r"Price per (\S+?)\s*\|", t[head:m.start()]) if head >= 0 else None
                cur = _Group(head if head >= 0 else m.start(), m.end(), um.group(1) if um else "", [tier], m.group(0))
                groups.append(cur)
            else:
                cur.tiers.append(tier)
                cur.end = m.end()
                cur.quote = t[cur.start:cur.end]
        return [g for g in groups if g.unit]
