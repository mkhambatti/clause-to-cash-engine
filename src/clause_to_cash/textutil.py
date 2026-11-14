"""Small, dependency-free text parsing helpers (numbers, money, dates, quotes).

They are deliberately generic English/legal-text utilities: they are shared by the
baseline extractor and by the independent corroborators, and know nothing about
any particular drafting style.
"""
from __future__ import annotations

import datetime as dt
import re

_UNITS = {w: i for i, w in enumerate(
    "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen "
    "fifteen sixteen seventeen eighteen nineteen".split())}
_TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90}
_WORDS = sorted([*_UNITS, *_TENS, "hundred", "thousand"], key=len, reverse=True)
_WORD_ALT = "|".join(_WORDS)
_NUMWORD_RE = re.compile(rf"\b(?:(?:{_WORD_ALT})(?:[-\s]+|\b))+", re.I)
_DIGIT_RE = re.compile(r"\d[\d,]*(?:\.\d+)?")

MONTHS = {m.lower(): i for i, m in enumerate(
    ["January", "February", "March", "April", "May", "June", "July", "August", "September",
     "October", "November", "December"], 1)}
_MON = "|".join(MONTHS)

_TRANS = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"', "–": "-", "—": "-",
                        " ": " ", "−": "-"})


def fold(s: str) -> str:
    """Length-preserving fold of typographic characters (so offsets stay valid)."""
    return s.translate(_TRANS)


def words_to_int(s: str) -> int | None:
    toks = [t for t in re.split(r"[-\s]+", s.lower().strip()) if t]
    if not toks:
        return None
    total = cur = 0
    for t in toks:
        if t in _UNITS:
            cur += _UNITS[t]
        elif t in _TENS:
            cur += _TENS[t]
        elif t == "hundred":
            cur = (cur or 1) * 100
        elif t == "thousand":
            total += (cur or 1) * 1000
            cur = 0
        else:
            return None
    return total + cur


def find_numbers(text: str) -> list[tuple[float, int, int]]:
    """All numbers (digits or English number words) with spans."""
    out: list[tuple[float, int, int]] = []
    for m in _DIGIT_RE.finditer(text):
        try:
            out.append((float(m.group().replace(",", "").rstrip(".")), m.start(), m.end()))
        except ValueError:
            pass
    for m in _NUMWORD_RE.finditer(text):
        v = words_to_int(m.group())
        if v is not None:
            out.append((float(v), m.start(), m.end()))
    out.sort(key=lambda x: x[1])
    return out


_SCALE = {"million": 1e6, "billion": 1e9, "m": 1e6, "k": 1e3, "thousand": 1e3}
_MONEY_RE = re.compile(
    r"(?:(?:US|U\.S\.)\s?\$|\$|USD\s?)\s?(\d[\d,]*(?:\.\d+)?)(?:\s*(million|billion|thousand|m|k)\b)?", re.I)


def find_money(text: str) -> list[float]:
    vals = []
    for m in _MONEY_RE.finditer(text):
        v = float(m.group(1).replace(",", "").rstrip("."))
        if m.group(2):
            v *= _SCALE[m.group(2).lower()]
        vals.append(v)
    return vals


_DATE_PATTERNS = [
    (re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b"), lambda m: dt.date(int(m[1]), int(m[2]), int(m[3]))),
    (re.compile(rf"\b({_MON})\s+(\d{{1,2}})(?:st|nd|rd|th)?,?\s+(\d{{4}})\b", re.I),
     lambda m: dt.date(int(m[3]), MONTHS[m[1].lower()], int(m[2]))),
    (re.compile(rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+(?:day\s+of\s+|of\s+)?({_MON}),?\s+(\d{{4}})\b", re.I),
     lambda m: dt.date(int(m[3]), MONTHS[m[2].lower()], int(m[1]))),
]


def find_dates(text: str) -> list[tuple[dt.date, int, int]]:
    out: list[tuple[dt.date, int, int]] = []
    for rx, build in _DATE_PATTERNS:
        for m in rx.finditer(text):
            try:
                out.append((build(m), m.start(), m.end()))
            except ValueError:
                continue
    out.sort(key=lambda x: x[1])
    return out


def norm_ws(s: str) -> str:
    return re.sub(r"\s+", " ", fold(s)).strip()


def locate(doc: str, quote: str) -> tuple[int, int] | None:
    """Find ``quote`` in ``doc`` tolerating whitespace/typography differences.

    Returns offsets into the *original* ``doc`` or ``None``.
    """
    q = quote.strip()
    if not q:
        return None
    toks = re.split(r"\s+", fold(q))
    # tolerate line-end hyphenation in scanned layouts: "million-\nmessages" matches "million-messages"
    pat = r"\s+".join(re.escape(t).replace(r"\-", r"-\s*") for t in toks if t)
    m = re.search(pat, fold(doc))
    return (m.start(), m.end()) if m else None


def sentences(text: str) -> list[tuple[int, int]]:
    """Sentence/clause spans (offsets) – splits on '.', ';', blank lines and table rows."""
    spans: list[tuple[int, int]] = []
    start = 0
    for m in re.finditer(r"(?<=[.;])\s+(?=[A-Z(\"'$0-9])|\n\s*\n|\n(?=\s*(?:\||Tier\s*\d|[A-Za-z][A-Za-z /&-]{2,40}:))", text):
        if m.start() > start:
            spans.append((start, m.start()))
        start = m.end()
    if start < len(text):
        spans.append((start, len(text)))
    return [(s, e) for s, e in spans if text[s:e].strip()]
