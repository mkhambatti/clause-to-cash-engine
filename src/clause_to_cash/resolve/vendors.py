"""Vendor entity resolution: which legal-entity spellings are the same vendor?

Strategy (deliberately conservative - a wrong merge pools unrelated spend and fabricates savings):

1. normalise: lowercase, strip punctuation and legal suffixes, fold plurals, '&' -> 'and'
2. *strong* match: identical normalised token sequence, or near-identical spelling
   (character-trigram Dice >= 0.88 on the joined tokens)  -> union
3. *short form* ("Kestrel Edge" for "Kestrel Edge Networks") merges only if exactly ONE candidate
   cluster in the same service category starts with those tokens.  If two do (a real company and a
   same-prefix decoy), the name is AMBIGUOUS and left unmerged for a human - never guessed.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

LEGAL = {"inc", "incorporated", "llc", "ltd", "limited", "corp", "corporation", "co", "company", "plc", "gmbh", "lp", "llp", "sa", "ag"}


def normalize(name: str) -> tuple[str, ...]:
    s = name.lower().replace("&", " and ")
    toks = [t for t in re.findall(r"[a-z0-9]+", s) if t not in LEGAL]
    out = []
    for t in toks:
        if len(t) > 3 and t.endswith("s") and not t.endswith("ss"):
            t = t[:-1]
        out.append(t)
    return tuple(out)


_TRAIL = re.compile(r"[,\s]+(?:" + "|".join(sorted(LEGAL, key=len, reverse=True)) + r")\.?$", re.I)


def _display_name(raw: str) -> str:
    """Readable canonical name: the best surface form with trailing legal-entity suffix removed (never stemmed)."""
    s = raw.strip()
    prev = None
    while prev != s:
        prev, s = s, _TRAIL.sub("", s).strip(" ,")
    return s.title() if s.isupper() else s


def _trigrams(s: str) -> set[str]:
    s = f"  {s} "
    return {s[i:i + 3] for i in range(len(s) - 2)}


def dice(a: str, b: str) -> float:
    ta, tb = _trigrams(a), _trigrams(b)
    return 2 * len(ta & tb) / (len(ta) + len(tb)) if ta and tb else 0.0


@dataclass
class ERResult:
    cluster_of: dict[str, str]  # doc_id -> cluster id
    display: dict[str, str]  # cluster id -> canonical display name
    ambiguous: set[str] = field(default_factory=set)  # doc_ids whose vendor could not be safely resolved


def resolve_vendors(items: list[tuple[str, str, str | None]]) -> ERResult:
    """``items``: (doc_id, vendor_name_as_written, service_category). Returns clusters."""
    toks = {d: normalize(n) for d, n, _ in items}
    cat = {d: c for d, _, c in items}
    names = {d: n for d, n, _ in items}
    parent = {d: d for d in toks}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    ids = sorted(toks)
    for i, a in enumerate(ids):
        for b in ids[i + 1:]:
            if not toks[a] or not toks[b]:
                continue
            if toks[a] == toks[b] or dice(" ".join(toks[a]), " ".join(toks[b])) >= 0.88 and abs(len(toks[a]) - len(toks[b])) == 0:
                union(a, b)

    ambiguous: set[str] = set()
    # short forms
    roots = {find(d) for d in ids}
    for d in ids:
        t = toks[d]
        if not t:
            continue
        longer = {find(o) for o in ids if len(toks[o]) > len(t) and toks[o][:len(t)] == t and cat[o] == cat[d]}
        longer.discard(find(d))
        if not longer:
            continue
        if len(longer) == 1:
            union(next(iter(longer)), d)
        else:
            ambiguous.add(d)
    # a long name can also be the *other side* of an ambiguous short form -> unaffected (it is unambiguous itself)
    clusters: dict[str, list[str]] = {}
    for d in ids:
        clusters.setdefault(find(d), []).append(d)
    cluster_of: dict[str, str] = {}
    display: dict[str, str] = {}
    for n, (root, members) in enumerate(sorted(clusters.items()), 1):
        cid = f"VC-{n:03d}"
        best = max(members, key=lambda m: (len(toks[m]), -len(names[m])))
        display[cid] = _display_name(names[best])
        for m in members:
            cluster_of[m] = cid
    _ = roots
    return ERResult(cluster_of, display, ambiguous)


def pairwise_f1(pred: dict[str, str], gold: dict[str, str], exclude: set[str] | None = None) -> dict[str, float]:
    """Pairwise precision/recall/F1 of a clustering against gold vendor ids."""
    exclude = exclude or set()
    ids = sorted(d for d in pred if d in gold and d not in exclude)
    tp = fp = fn = 0
    for i, a in enumerate(ids):
        for b in ids[i + 1:]:
            p = pred[a] == pred[b]
            g = gold[a] == gold[b]
            tp += p and g
            fp += p and not g
            fn += (not p) and g
    prec = tp / (tp + fp) if tp + fp else 1.0
    rec = tp / (tp + fn) if tp + fn else 1.0
    f1 = 2 * prec * rec / (prec + rec) if prec + rec else 0.0
    return {"precision": prec, "recall": rec, "f1": f1, "tp": tp, "fp": fp, "fn": fn}
