"""Render a ``ContractSpec`` as contract text in one of four drafting styles.

Styles 0-2 are the *seen* styles (numbered plain English, long-form legalese with line-wrapped
"scanned" layout, and a terse key/value order form).  Style 3 is the *held-out* style: informal,
narrative drafting.  The baseline rule extractor and the verification layer were written against
styles 0-2 only; style 3 exists to measure what happens when wording shifts.

Every paragraph that states a fact is recorded as a character span so evidence localisation can
be scored against ground truth.
"""
from __future__ import annotations

import datetime as dt
import random
import textwrap
from typing import Callable

from ..schema import Duration, Pricing
from .spec import Amend, ContractSpec

# --------------------------------------------------------------------------- #
# number / money / date formatting
# --------------------------------------------------------------------------- #
_ONES = ("zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen "
         "sixteen seventeen eighteen nineteen").split()
_TENS = "_ _ twenty thirty forty fifty sixty seventy eighty ninety".split()


def W(n: int) -> str:
    if n < 20:
        return _ONES[n]
    if n < 100:
        return _TENS[n // 10] + (f"-{_ONES[n % 10]}" if n % 10 else "")
    if n < 1000:
        return f"{_ONES[n // 100]} hundred" + (f" {W(n % 100)}" if n % 100 else "")
    return f"{W(n // 1000)} thousand" + (f" {W(n % 1000)}" if n % 1000 else "")


def P(n: int) -> str:
    return f"{W(n)} ({n})"


def pctnum(x: float) -> str:
    return f"{x:g}"


def fnum(x: float) -> str:
    return f"{int(round(x)):,}"


def fprice(x: float) -> str:
    if x >= 1000:
        return f"${x:,.0f}"
    if x >= 1:
        return f"${x:,.2f}"
    return f"${x:.4f}"


def parse_price(s: str) -> float:
    return float(s.replace("$", "").replace(",", ""))


def fmt_date(style: int, d: dt.date) -> str:
    if style == 0:
        return f"{d.strftime('%B')} {d.day}, {d.year}"
    if style == 1:
        suf = "th" if 10 <= d.day % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(d.day % 10, "th")
        return f"the {d.day}{suf} day of {d.strftime('%B')}, {d.year}"
    if style == 2:
        return d.isoformat()
    suf = "th" if 10 <= d.day % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(d.day % 10, "th")
    return f"{d.day}{suf} {d.strftime('%B')} {d.year}"


def fmt_money(style: int, v: float) -> str:
    if style == 1:
        return f"USD {v:,.0f}"
    if style == 3:
        return f"US${v / 1e6:g} million" if v % 10000 == 0 and v >= 1e6 else f"${v:,.0f}"
    return f"${v:,.0f}"


def dur_phrase(style: int, d: Duration) -> str:
    return f"{P(d.amount)} {'days' if d.unit == 'days' else ('month' if d.amount == 1 else 'months')}"


def months_phrase(m: int, prefer_years: bool) -> str:
    if prefer_years and m % 12 == 0:
        y = m // 12
        return f"{P(y)} {'year' if y == 1 else 'years'}"
    return f"{P(m)} months"


def S3_months(m: int) -> str:
    if m % 12 == 0:
        y = m // 12
        return f"{W(y)} {'year' if y == 1 else 'years'}"
    return f"{W(m)} months"


# --------------------------------------------------------------------------- #
# document builder
# --------------------------------------------------------------------------- #
class Doc:
    def __init__(self, wrap: int | None = None, pages: bool = False) -> None:
        self.parts: list[str] = []
        self.pos = 0
        self.spans: dict[str, list[tuple[int, int]]] = {}
        self.wrap = wrap
        self.pages = pages
        self._n = 0

    def add(self, text: str, fields: tuple[str, ...] = (), sep: str = "\n\n", wrap: bool = True) -> None:
        if self.wrap and wrap and "\n" not in text and "|" not in text:
            text = textwrap.fill(text, self.wrap)
        start = self.pos
        self.parts.append(text)
        self.pos += len(text)
        for f in fields:
            self.spans.setdefault(f, []).append((start, start + len(text)))
        self.parts.append(sep)
        self.pos += len(sep)
        self._n += 1
        if self.pages and self._n % 6 == 0:
            marker = f"CONFIDENTIAL - Page {self._n // 6 + 1}"
            self.parts.append(marker + "\n\n")
            self.pos += len(marker) + 2

    def text(self) -> str:
        return "".join(self.parts).rstrip() + "\n"


# --------------------------------------------------------------------------- #
# schedule (pricing) renderers
# --------------------------------------------------------------------------- #
def _mode_sentence(style: int, mode: str) -> str:
    grad = mode == "graduated"
    if style == 0:
        return ("Tiered pricing applies on a graduated basis: each unit is charged at the rate of the tier in which it falls."
                if grad else
                "Tiered pricing applies on an all-units basis: the rate for the tier in which total Annual Volume falls applies to all units.")
    if style == 1:
        return ("The foregoing tiered Unit Prices shall be applied on a marginal basis, such that each unit of Annual Volume "
                "shall be priced at the rate applicable to the tier in which such unit falls."
                if grad else
                "The foregoing tiered Unit Prices shall be applied on a volume-discount basis, such that the Unit Price "
                "applicable to the tier in which total Annual Volume falls shall apply to all units of Annual Volume.")
    return "Tier Mode: Graduated" if grad else "Tier Mode: All-Units"


def schedule_text(style: int, p: Pricing, heading: bool = True) -> str:
    u = p.unit
    t = p.tiers
    if style == 0:
        lines = ["SCHEDULE A - PRICING"] if heading else []
        lines.append(f"Unit: {u}")
        for i, x in enumerate(t, 1):
            if x.upper is None:
                lines.append(f"Tier {i}: Above {fnum(x.lower)} {u} per year at {fprice(x.unit_price)} per {u}")
            else:
                lines.append(f"Tier {i}: {fnum(x.lower)} to {fnum(x.upper)} {u} per year at {fprice(x.unit_price)} per {u}")
        lines.append(_mode_sentence(0, p.tier_mode))
        return "\n".join(lines)
    if style == 1:
        segs = []
        for i, x in enumerate(t):
            if i == 0:
                segs.append(f"For Annual Volume of up to {fnum(x.upper or 0)} {u}, the Unit Price shall be {fprice(x.unit_price)} per {u}")
            elif x.upper is None:
                segs.append(f"and for Annual Volume in excess of {fnum(x.lower)} {u}, the Unit Price shall be {fprice(x.unit_price)} per {u}")
            else:
                segs.append(f"for Annual Volume in excess of {fnum(x.lower)} {u} and up to {fnum(x.upper)} {u}, "
                            f"the Unit Price shall be {fprice(x.unit_price)} per {u}")
        head = "SCHEDULE A. PRICING. " if heading else ""
        return head + "; ".join(segs) + ". " + _mode_sentence(1, p.tier_mode)
    if style == 2:
        lines = [_mode_sentence(2, p.tier_mode), f"| Tier | From ({u}) | To ({u}) | Price per {u} |", "|------|------|------|------|"]
        for i, x in enumerate(t, 1):
            to = "and above" if x.upper is None else fnum(x.upper)
            lines.append(f"| {i} | {fnum(x.lower)} | {to} | {fprice(x.unit_price)} |")
        return "\n".join(lines)
    # style 3 (held-out)
    if p.tier_mode == "graduated":
        segs = []
        for i, x in enumerate(t):
            if i == 0:
                segs.append(f"the first {fnum(x.upper or 0)} {u} cost {fprice(x.unit_price)} each")
            elif x.upper is None:
                segs.append(f"and anything beyond {fnum(x.lower)} {u} costs {fprice(x.unit_price)} each")
            else:
                segs.append(f"the next {fnum(x.upper - x.lower)} {u} cost {fprice(x.unit_price)} each")
        return "Pricing steps down as volume grows: " + ", ".join(segs) + "."
    segs = []
    for i, x in enumerate(t):
        if i == 0:
            segs.append(f"up to {fnum(x.upper or 0)} {u} a year each {u} costs {fprice(x.unit_price)}")
        elif x.upper is None:
            segs.append(f"past {fnum(x.lower)} it drops to {fprice(x.unit_price)} for every {u}")
        else:
            segs.append(f"once volume passes {fnum(x.lower)} every {u} is repriced at {fprice(x.unit_price)}")
    return "Pricing steps down with volume, applied to everything: " + "; ".join(segs) + "."


# --------------------------------------------------------------------------- #
# style renderers
# --------------------------------------------------------------------------- #
ETF_S3 = {25.0: "a quarter", 50.0: "half", 75.0: "three quarters", 100.0: "all"}


def _boiler(style: int, s: ContractSpec, rng: random.Random) -> list[str]:
    """Distractor clauses containing numbers, 'days', 'Affiliates', percentages and 'exclusive'."""
    pd = s.payment_days
    if style == 0:
        return [
            f"Payment Terms. Customer will pay undisputed invoices within {P(pd)} days of receipt.",
            f"Confidentiality. Each party's confidentiality obligations survive for {P(3)} years after termination of this Agreement.",
            "Indemnification. Vendor will indemnify Customer, its Affiliates, and their respective officers and directors "
            "against third-party claims arising from Vendor's breach of this Agreement.",
            f"Limitation of Liability. Each party's total liability will not exceed the fees paid in the {P(12)} months preceding "
            "the claim. This Section states each party's exclusive remedy for such claims.",
            f"Service Levels. Vendor will maintain 99.9% availability and will issue service credits of {W(10)} percent (10%) "
            "of the monthly fees for any month in which availability falls below 99.5%.",
            f"Notices. Notices are deemed given {P(5)} days after mailing.",
        ]
    if style == 1:
        return [
            f"Customer shall pay all undisputed invoices within {P(pd)} days after receipt thereof.",
            f"The obligations of confidentiality set forth herein shall survive for a period of {P(3)} years following termination "
            "or expiration of this Agreement.",
            "Vendor shall defend, indemnify and hold harmless Customer, its Affiliates and their respective directors, officers "
            "and employees from and against any third-party claims arising out of Vendor's breach hereof.",
            f"In no event shall either Party's aggregate liability exceed the fees paid in the {P(12)} months immediately preceding "
            "the event giving rise to liability, which shall be the Parties' exclusive remedy.",
            f"Vendor shall maintain availability of 99.9% and shall credit Customer {W(10)} percent (10%) of the monthly fees for "
            "each month in which availability is below 99.5%.",
            f"Any notice shall be deemed given {P(5)} days after deposit in the mail.",
        ]
    if style == 2:
        return [f"Payment Terms: Net {pd}", "SLA: 99.9% availability; service credit 10% of monthly fees below 99.5%",
                "Confidentiality: survives 3 years after termination", "Liability Cap: 12 months of fees (exclusive remedy)",
                "Indemnity: Vendor indemnifies Customer and its Affiliates for third-party claims"]
    return [
        f"Invoices are due {pd} days after receipt.",
        "Confidential information stays confidential for three years after the agreement ends.",
        "Each side's liability is capped at twelve months of fees and that is the exclusive remedy.",
        "We will credit 10% of the monthly fee if uptime drops below 99.5%.",
        "The Customer indemnifies us and our affiliates against claims arising from its content.",
    ]


def render(s: ContractSpec, rng: random.Random) -> tuple[str, dict[str, list[tuple[int, int]]]]:
    fn: Callable[[ContractSpec, random.Random], Doc] = {0: _style0, 1: _style1, 2: _style2, 3: _style3}[s.style]
    d = fn(s, rng)
    return d.text(), d.spans


def _neg(s: ContractSpec, key: str) -> bool:
    return s.explicit_negatives.get(key, False)


# -- style 0: numbered plain English ----------------------------------------- #
def _style0(s: ContractSpec, rng: random.Random) -> Doc:
    d = Doc()
    d.add("MASTER SERVICES AGREEMENT")
    d.add(f"This Master Services Agreement (this \"Agreement\") is dated as of {fmt_date(0, s.eff)} (the \"Effective Date\") by and "
          f"between {s.customer_name} (\"Customer\") and {s.vendor_name}, a {s.state} corporation (\"Vendor\").",
          ("effective_date", "customer_party", "vendor_party"))
    d.add(f"1. Services. Vendor shall provide {s.cat.phrases[0]} (the \"Services\") to Customer in accordance with this Agreement.",
          ("service_category",))
    d.add(f"2.1 Term. This Agreement commences on the Effective Date and continues for an initial term of {P(s.term_months)} months "
          "(the \"Initial Term\").", ("initial_term_months",))
    if s.auto_renew and s.notice:
        d.add(f"2.2 Renewal. Upon expiration of the Initial Term, this Agreement will automatically renew for successive renewal terms "
              f"of {P(s.renewal_months)} months each, unless either party provides written notice of non-renewal at least "
              f"{dur_phrase(0, s.notice)} prior to the end of the then-current term.",
              ("auto_renew", "renewal_term_months", "nonrenewal_notice"))
    else:
        d.add("2.2 Renewal. This Agreement will not renew automatically; any renewal requires a written amendment signed by both parties.",
              ("auto_renew", "renewal_term_months", "nonrenewal_notice"))
    if s.tfc:
        d.add(f"2.3 Termination for Convenience. Customer may terminate this Agreement for convenience upon {P(s.tfc_days or 0)} days' "
              f"prior written notice, subject to payment of an early termination fee equal to {W(int(s.etf_pct or 0))} percent "
              f"({pctnum(s.etf_pct or 0)}%) of the fees that would have been payable for the remainder of the then-current term.",
              ("termination_for_convenience", "tfc_notice_days", "early_termination_fee_pct"))
    elif _neg(s, "tfc"):
        d.add("2.3 Termination for Convenience. Neither party may terminate this Agreement for convenience.",
              ("termination_for_convenience",))
    d.add("3.1 Fees. Customer shall pay the fees set forth in Schedule A.", ("pricing",))
    if s.escalator:
        d.add(f"3.2 Fee Adjustments. Fees will increase by {W(int(s.escalator)) if s.escalator == int(s.escalator) else pctnum(s.escalator)} "
              f"percent ({pctnum(s.escalator)}%) on each anniversary of the Effective Date.", ("annual_escalator_pct",))
    elif _neg(s, "escalator"):
        d.add("3.2 Fee Adjustments. Fees are fixed for the Term.", ("annual_escalator_pct",))
    if s.min_commit:
        d.add(f"3.3 Minimum Commitment. Customer commits to purchase not less than {fmt_money(0, s.min_commit)} of Services in each "
              "contract year (the \"Annual Minimum Commitment\"). Any shortfall will be invoiced at the end of the contract year.",
              ("min_annual_commit_usd",))
    elif _neg(s, "commit"):
        d.add("3.3 Minimum Commitment. There is no minimum purchase commitment under this Agreement.", ("min_annual_commit_usd",))
    boiler = _boiler(0, s, rng)
    d.add("3.4 " + boiler[0])
    if s.affiliate == "allowed":
        d.add("4.1 Affiliates. Customer's Affiliates may purchase Services under this Agreement at the rates set forth in Schedule A, "
              "and all such purchases will count toward Annual Volume.", ("affiliate_purchasing",))
    elif s.affiliate == "consent_required":
        d.add("4.1 Affiliates. Customer's Affiliates may purchase Services under this Agreement only with Vendor's prior written consent.",
              ("affiliate_purchasing",))
    elif s.affiliate == "prohibited":
        d.add("4.1 Affiliates. The Services are for Customer's internal use only, and no Affiliate or third party may use or purchase "
              "the Services under this Agreement.", ("affiliate_purchasing",))
    if s.coc == "free":
        d.add("5.1 Assignment. Either party may assign this Agreement without the other party's consent to an Affiliate or to a successor "
              "in connection with a merger, acquisition, or sale of all or substantially all of its assets.", ("change_of_control",))
    elif s.coc == "consent_required":
        d.add("5.1 Assignment. Neither party may assign this Agreement, whether by operation of law, change of control, or otherwise, "
              "without the other party's prior written consent, which will not be unreasonably withheld.", ("change_of_control",))
    elif s.coc == "vendor_termination_right":
        d.add("5.1 Assignment. Neither party may assign this Agreement without the other party's prior written consent. If Customer "
              "undergoes a change of control, Vendor may terminate this Agreement upon thirty (30) days' written notice.",
              ("change_of_control",))
    else:
        d.add("5.1 General. This Agreement is binding upon and inures to the benefit of the parties and their respective successors "
              "and permitted assigns.", ("change_of_control",))
    if s.exclusive:
        d.add("6.1 Exclusivity. During the Term, Customer will purchase all of its requirements for the Services exclusively from Vendor.",
              ("exclusive_requirements",))
    elif _neg(s, "exclusive"):
        d.add("6.1 Exclusivity. Nothing in this Agreement obligates Customer to purchase the Services exclusively from Vendor.",
              ("exclusive_requirements",))
    if s.mfn:
        d.add("6.2 Pricing Protection. Vendor represents that the unit prices in Schedule A are no less favorable than those offered to "
              "any similarly situated customer purchasing comparable volumes.", ("mfn",))
    elif _neg(s, "mfn"):
        d.add("6.2 Pricing Protection. Vendor makes no representation regarding prices charged to its other customers.", ("mfn",))
    for b in boiler[1:]:
        d.add(f"7. {b}")
    d.add(f"8. Governing Law. This Agreement is governed by the laws of {'the State of ' if s.law != 'England and Wales' else ''}{s.law}, "
          "without regard to conflict of law principles.", ("governing_law",))
    d.add(schedule_text(0, s.pricing), ("pricing",))
    d.add("IN WITNESS WHEREOF, the parties have executed this Agreement.\nCustomer: ____________________  Vendor: ____________________")
    for i, a in enumerate(sorted(s.amendments, key=lambda x: x.date), 1):
        _amend0(d, s, a, i)
    return d


def _amend0(d: Doc, s: ContractSpec, a: Amend, i: int) -> None:
    d.add(f"AMENDMENT NO. {i} TO MASTER SERVICES AGREEMENT")
    intro = f"This Amendment No. {i} is entered into as of {fmt_date(0, a.date)}."
    tail = "Except as modified hereby, all terms of the Agreement remain in full force and effect."
    if a.kind == "extend":
        d.add(f"{intro} Section 2.1 (Term) is amended by extending the Initial Term by {P(a.months)} months. {tail}", ("initial_term_months",))
    elif a.kind == "reprice" and a.pricing:
        d.add(f"{intro} Schedule A is deleted in its entirety and replaced with the following:\n{schedule_text(0, a.pricing, heading=False)}\n{tail}",
              ("pricing",))
    elif a.kind == "notice" and a.notice:
        d.add(f"{intro} The notice of non-renewal under Section 2.2 must be given at least {dur_phrase(0, a.notice)} prior to the end of "
              f"the then-current term. {tail}", ("nonrenewal_notice",))


# -- style 1: long-form legalese, wrapped "scanned" layout ------------------- #
def _style1(s: ContractSpec, rng: random.Random) -> Doc:
    d = Doc(wrap=78, pages=True)
    d.add("MASTER SERVICES AGREEMENT", wrap=False)
    d.add(f"THIS MASTER SERVICES AGREEMENT (this \"Agreement\") is made and entered into effective as of {fmt_date(1, s.eff)} "
          f"(the \"Effective Date\"), by and between {s.vendor_name}, a {s.state} corporation (\"Vendor\"), and {s.customer_name} "
          "(\"Customer\").", ("effective_date", "customer_party", "vendor_party"))
    d.add(f"WHEREAS, Vendor is in the business of providing {s.cat.phrases[1]}; and WHEREAS, Customer wishes to obtain such services; "
          "NOW, THEREFORE, the parties agree as follows:", ("service_category",))
    b = _boiler(1, s, rng)
    yrs = s.term_months % 12 == 0
    d.add(f"3.1 Initial Term. The term of this Agreement shall commence on the Effective Date and, unless earlier terminated in "
          f"accordance with its terms, shall continue for a period of {months_phrase(s.term_months, yrs)} (the \"Initial Term\").",
          ("initial_term_months",))
    if s.auto_renew and s.notice:
        d.add("3.2 Renewal. Following the Initial Term, this Agreement shall be automatically extended for successive periods of "
              f"{months_phrase(s.renewal_months, True)} (each, a \"Renewal Term\") unless either Party gives the other written notice "
              f"of its intention not to renew not less than {dur_phrase(1, s.notice)} prior to the expiration of the then-current term.",
              ("auto_renew", "renewal_term_months", "nonrenewal_notice"))
    else:
        d.add("3.2 No Automatic Renewal. This Agreement shall expire at the end of the Initial Term and shall not be renewed except by "
              "written instrument executed by both Parties.", ("auto_renew", "renewal_term_months", "nonrenewal_notice"))
    if s.tfc:
        d.add("3.3 Termination Without Cause. Customer shall have the right, exercisable at any time upon not less than "
              f"{P(s.tfc_days or 0)} days' written notice, to terminate this Agreement without cause; provided that Customer shall pay "
              f"Vendor an early termination charge equal to {W(int(s.etf_pct or 0))} percent ({pctnum(s.etf_pct or 0)}%) of the fees "
              "payable for the balance of the then-current term.",
              ("termination_for_convenience", "tfc_notice_days", "early_termination_fee_pct"))
    elif _neg(s, "tfc"):
        d.add("3.3 No Termination for Convenience. Customer shall have no right to terminate this Agreement without cause prior to the "
              "expiration of the then-current term.", ("termination_for_convenience",))
    d.add("4.1 Fees. Customer shall pay Vendor the fees set forth in Schedule A.", ("pricing",))
    if s.min_commit:
        d.add(f"4.2 Minimum Fees. Customer shall pay Vendor annual minimum fees of {fmt_money(1, s.min_commit)} irrespective of actual "
              "usage (the \"Minimum Commitment\"), on a take-or-pay basis.", ("min_annual_commit_usd",))
    elif _neg(s, "commit"):
        d.add("4.2 No Minimum. Customer is not subject to any minimum fee or volume commitment.", ("min_annual_commit_usd",))
    if s.escalator:
        d.add("4.3 Adjustments. Fees shall be adjusted annually, effective on each anniversary of the Effective Date, by an increase of "
              f"{W(int(s.escalator)) if s.escalator == int(s.escalator) else pctnum(s.escalator)} percent ({pctnum(s.escalator)}%) over "
              "the fees in effect for the preceding year.", ("annual_escalator_pct",))
    elif _neg(s, "escalator"):
        d.add("4.3 Adjustments. The fees set forth in Schedule A shall remain fixed during the Initial Term.", ("annual_escalator_pct",))
    d.add("4.4 " + b[0])
    if s.affiliate == "allowed":
        d.add("5.1 Affiliate Orders. Customer's Affiliates shall be entitled to order Services hereunder on the terms and at the prices "
              "set forth herein, and the volumes ordered by all such Affiliates shall be aggregated with Customer's volumes for purposes "
              "of Schedule A.", ("affiliate_purchasing",))
    elif s.affiliate == "consent_required":
        d.add("5.1 Affiliate Orders. Affiliates of Customer may order Services hereunder solely with the prior written approval of Vendor.",
              ("affiliate_purchasing",))
    elif s.affiliate == "prohibited":
        d.add("5.1 Use Restriction. The Services are licensed to Customer solely for Customer's own internal business purposes, and no "
              "Affiliate of Customer shall be permitted to order or use the Services hereunder.", ("affiliate_purchasing",))
    if s.coc == "free":
        d.add("6.1 Assignment. Either Party may, without the consent of the other Party, assign this Agreement in its entirety to an "
              "Affiliate or to any successor to all or substantially all of its business or assets, whether by merger, consolidation "
              "or otherwise.", ("change_of_control",))
    elif s.coc == "consent_required":
        d.add("6.1 Assignment. Neither Party shall assign or otherwise transfer this Agreement, including by operation of law or in "
              "connection with a change of control, without the prior written consent of the other Party, such consent not to be "
              "unreasonably withheld.", ("change_of_control",))
    elif s.coc == "vendor_termination_right":
        d.add("6.1 Assignment. Neither Party shall assign this Agreement without the prior written consent of the other Party. In "
              "addition, if Customer undergoes a Change of Control, Vendor may terminate this Agreement upon thirty (30) days' written "
              "notice to Customer.", ("change_of_control",))
    else:
        d.add("6.1 Successors. This Agreement shall be binding upon and shall inure to the benefit of the Parties and their respective "
              "successors and permitted assigns.", ("change_of_control",))
    if s.exclusive:
        d.add("6.2 Requirements. During the term of this Agreement, Customer shall purchase exclusively from Vendor all of Customer's "
              "requirements for the Services.", ("exclusive_requirements",))
    elif _neg(s, "exclusive"):
        d.add("6.2 Requirements. Nothing herein shall obligate Customer to purchase the Services exclusively from Vendor.",
              ("exclusive_requirements",))
    if s.mfn:
        d.add("6.3 Pricing Warranty. Vendor hereby represents and warrants that the prices set forth in Schedule A are, and shall remain "
              "during the term, no less favorable than the prices offered by Vendor to any similarly situated customer for comparable "
              "volumes.", ("mfn",))
    elif _neg(s, "mfn"):
        d.add("6.3 Pricing Warranty. Vendor makes no representation or warranty regarding the prices charged to other customers.", ("mfn",))
    for i, x in enumerate(b[1:], 1):
        d.add(f"7.{i} {x}")
    d.add(f"8.1 Governing Law. This Agreement shall be governed by and construed in accordance with the laws of "
          f"{'the State of ' if s.law != 'England and Wales' else ''}{s.law}, without giving effect to conflict of laws principles.",
          ("governing_law",))
    d.add(schedule_text(1, s.pricing), ("pricing",))
    d.add("IN WITNESS WHEREOF, the Parties have caused this Agreement to be executed by their duly authorized representatives.", wrap=True)
    names = ["First", "Second", "Third"]
    for i, a in enumerate(sorted(s.amendments, key=lambda x: x.date)):
        _amend1(d, s, a, names[i])
    return d


def _amend1(d: Doc, s: ContractSpec, a: Amend, name: str) -> None:
    d.add(f"{name.upper()} AMENDMENT TO MASTER SERVICES AGREEMENT", wrap=False)
    intro = f"This {name} Amendment is made as of {fmt_date(1, a.date)}."
    tail = "In all other respects the Agreement is ratified and confirmed."
    if a.kind == "extend":
        d.add(f"{intro} The Initial Term set forth in Section 3.1 is hereby extended by a period of {months_phrase(a.months, True)}. {tail}",
              ("initial_term_months",))
    elif a.kind == "reprice" and a.pricing:
        d.add(f"{intro} Schedule A is hereby deleted in its entirety and replaced with the following: {schedule_text(1, a.pricing, heading=False)} {tail}",
              ("pricing",))
    elif a.kind == "notice" and a.notice:
        d.add(f"{intro} The notice period for non-renewal in Section 3.2 is hereby amended to read: \"not less than "
              f"{dur_phrase(1, a.notice)} prior to the expiration of the then-current term.\" {tail}", ("nonrenewal_notice",))


# -- style 2: terse order form ------------------------------------------------ #
def _style2(s: ContractSpec, rng: random.Random) -> Doc:
    d = Doc()
    b = _boiler(2, s, rng)
    d.add("ORDER FORM")
    d.add(f"Customer: {s.customer_name}\nVendor: {s.vendor_name.upper()}\nEffective Date: {fmt_date(2, s.eff)}\n"
          f"Services: {s.cat.phrases[2]}\nGoverning Law: {s.law}",
          ("effective_date", "customer_party", "vendor_party", "service_category", "governing_law"))
    renew = (f"Auto-Renewal: Yes - {s.renewal_months} months\nNon-Renewal Notice: {s.notice.amount} {s.notice.unit}"
             if s.auto_renew and s.notice else "Auto-Renewal: No")
    d.add(f"TERM & RENEWAL\nTerm: {s.term_months} months from Effective Date\n{renew}",
          ("initial_term_months", "auto_renew", "renewal_term_months", "nonrenewal_notice"))
    if s.tfc:
        tf = f"Termination for Convenience: Customer - {s.tfc_days} days notice; ETF {pctnum(s.etf_pct or 0)}% of remaining fees"
    else:
        tf = "Termination for Convenience: Not permitted"
    d.add(f"TERMINATION\n{tf}", ("termination_for_convenience", "tfc_notice_days", "early_termination_fee_pct"))
    commit = f"Annual Minimum Commitment: {fmt_money(2, s.min_commit)}" if s.min_commit else "Annual Minimum Commitment: None"
    esc = f"Annual Price Increase: {pctnum(s.escalator)}%" if s.escalator else "Annual Price Increase: None"
    d.add(f"COMMERCIALS\n{commit}\n{esc}\n{b[0]}", ("min_annual_commit_usd", "annual_escalator_pct"))
    d.add("PRICING\n" + schedule_text(2, s.pricing), ("pricing",))
    aff = {"allowed": "Affiliate Purchasing: Allowed", "consent_required": "Affiliate Purchasing: Allowed with Vendor consent",
           "prohibited": "Affiliate Purchasing: Not permitted", "silent": None}[s.affiliate]
    coc = {"free": "Assignment / Change of Control: Free assignment to successors",
           "consent_required": "Assignment / Change of Control: Consent required",
           "vendor_termination_right": "Assignment / Change of Control: Vendor may terminate on Customer change of control (30 days)",
           "silent": "Assignment / Change of Control: Not addressed"}[s.coc]
    other = [x for x in (aff, coc, f"Exclusive Requirements: {'Yes' if s.exclusive else 'No'}",
                         f"MFN: {'Yes' if s.mfn else 'No'}") if x]
    d.add("OTHER TERMS\n" + "\n".join(other), ("affiliate_purchasing", "change_of_control", "exclusive_requirements", "mfn"))
    d.add("GENERAL\n" + "\n".join(b[1:]))
    d.add("Signed: ____________________")
    for i, a in enumerate(sorted(s.amendments, key=lambda x: x.date), 1):
        d.add(f"AMENDMENT {i}")
        eff = f"Effective Date of Amendment: {fmt_date(2, a.date)}"
        if a.kind == "extend":
            d.add(f"{eff}\nTerm extension: +{a.months} months", ("initial_term_months",))
        elif a.kind == "reprice" and a.pricing:
            d.add(f"{eff}\nReplacement Pricing (supersedes prior schedule):\n{schedule_text(2, a.pricing)}", ("pricing",))
        elif a.kind == "notice" and a.notice:
            d.add(f"{eff}\nNon-Renewal Notice (amended): {a.notice.amount} {a.notice.unit}", ("nonrenewal_notice",))
    return d


# -- style 3: HELD-OUT informal narrative ------------------------------------ #
def _style3(s: ContractSpec, rng: random.Random) -> Doc:
    d = Doc()
    b = _boiler(3, s, rng)
    d.add("Services Agreement")
    d.add(f"This agreement is signed on {fmt_date(3, s.eff)} between {s.vendor_name} (\"we\", \"us\") and {s.customer_name} (\"the Customer\"). "
          f"The service, in plain words: {s.cat.holdout}.", ("effective_date", "customer_party", "vendor_party", "service_category"))
    d.add(f"The relationship runs for {S3_months(s.term_months)} counting from signature.", ("initial_term_months",))
    if s.auto_renew and s.notice:
        n = s.notice
        nd = f"{n.amount} days" if n.unit == "days" else f"{W(n.amount)} months"
        again = "year" if s.renewal_months == 12 else S3_months(s.renewal_months)
        d.add(f"Unless one side tells the other at least {nd} before the anniversary that it wants out, the deal rolls over for another "
              f"{again}.", ("auto_renew", "renewal_term_months", "nonrenewal_notice"))
    else:
        d.add("There is no evergreen: if the parties want to keep going after the initial period they will have to sign something new.",
              ("auto_renew", "renewal_term_months", "nonrenewal_notice"))
    if s.tfc:
        d.add(f"The Customer can walk away early on {s.tfc_days} days' notice, but owes {ETF_S3[float(s.etf_pct or 0)]} of what is left on "
              "the commitment.", ("termination_for_convenience", "tfc_notice_days", "early_termination_fee_pct"))
    elif _neg(s, "tfc"):
        d.add("Neither side gets to cancel early without cause.", ("termination_for_convenience",))
    d.add(schedule_text(3, s.pricing), ("pricing",))
    if s.min_commit:
        d.add(f"The Customer is on the hook for at least {fmt_money(3, s.min_commit)} a year whether or not it uses that much.",
              ("min_annual_commit_usd",))
    elif _neg(s, "commit"):
        d.add("There is no spend floor.", ("min_annual_commit_usd",))
    if s.escalator:
        d.add(f"Prices go up {pctnum(s.escalator)}% every year on the anniversary.", ("annual_escalator_pct",))
    elif _neg(s, "escalator"):
        d.add("Prices are locked for the whole relationship.", ("annual_escalator_pct",))
    d.add(b[0])
    if s.affiliate == "allowed":
        d.add("Sister companies of the Customer can also buy at these rates, and their volume counts toward the tiers.", ("affiliate_purchasing",))
    elif s.affiliate == "consent_required":
        d.add("Sister companies of the Customer can only buy under this deal if we agree first.", ("affiliate_purchasing",))
    elif s.affiliate == "prohibited":
        d.add("Only the Customer itself may use this arrangement; its sister companies cannot.", ("affiliate_purchasing",))
    if s.coc == "free":
        d.add("If either side is acquired or merges, the contract simply moves to the new owner.", ("change_of_control",))
    elif s.coc == "consent_required":
        d.add("The contract cannot be transferred, including to an acquirer, unless the other side agrees.", ("change_of_control",))
    elif s.coc == "vendor_termination_right":
        d.add("The contract cannot be transferred unless the other side agrees, and if the Customer is bought by someone else we may end "
              "the deal on 30 days' notice.", ("change_of_control",))
    else:
        d.add("The contract binds both sides and whoever follows them.", ("change_of_control",))
    if s.exclusive:
        d.add("The Customer agrees to buy all its needs for this service from us and nobody else.", ("exclusive_requirements",))
    elif _neg(s, "exclusive"):
        d.add("The Customer remains free to use other suppliers.", ("exclusive_requirements",))
    if s.mfn:
        d.add("We promise the prices here are as good as any comparable client gets.", ("mfn",))
    elif _neg(s, "mfn"):
        d.add("We make no promises about what other clients pay.", ("mfn",))
    for x in b[1:]:
        d.add(x)
    d.add(f"{s.law} law governs.", ("governing_law",))
    d.add("Signed by both sides.")
    for i, a in enumerate(sorted(s.amendments, key=lambda x: x.date), 1):
        d.add(f"Side Letter {i} - dated {fmt_date(3, a.date)}")
        if a.kind == "extend":
            d.add(f"The parties agree to add {S3_months(a.months)} to the initial period.", ("initial_term_months",))
        elif a.kind == "reprice" and a.pricing:
            d.add("Replacing the pricing above, from now on: " + schedule_text(3, a.pricing).split(": ", 1)[1], ("pricing",))
        elif a.kind == "notice" and a.notice:
            nd = f"{a.notice.amount} days" if a.notice.unit == "days" else f"{W(a.notice.amount)} months"
            d.add(f"The opt-out window is now {nd}.", ("nonrenewal_notice",))
    return d
