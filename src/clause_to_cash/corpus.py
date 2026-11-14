"""Corpus container + on-disk format.

Layout::

    corpus/
      manifest.json                 contracts, styles, vendor ids, scenario dates
      contracts/legacy_a/A-0001.txt raw contract text (the only thing a real deployment has)
      gold/A-0001.json              ground truth (synthetic corpus only)
      usage.csv                     trailing-12-month volume per contract (stand-in for the AP/usage ledger)
"""
from __future__ import annotations

import csv
import datetime as dt
import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .schema import Terms

ASOF = dt.date(2026, 11, 12)  # "today" for the scenario
CLOSING = dt.date(2026, 10, 6)  # public closing date of the Skydance / Warner Bros. Discovery deal
ACQUIRED_ENTITY = "B"  # entity whose customer-side change-of-control clauses are triggered by closing


@dataclass
class CorpusItem:
    doc_id: str
    entity: str
    style: int
    text: str
    sha256: str
    units: float = 0.0  # trailing-12-month volume from the usage ledger
    gold: Optional[Terms] = None
    spans: dict[str, list[tuple[int, int]]] = field(default_factory=dict)
    vendor_id: Optional[str] = None
    base_gold: Optional[Terms] = None  # terms of the BASE agreement before amendments (used to build stale-value faults)


@dataclass
class Corpus:
    items: list[CorpusItem]
    meta: dict

    def by_id(self) -> dict[str, CorpusItem]:
        return {i.doc_id: i for i in self.items}


def sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def save_corpus(c: Corpus, root: Path) -> None:
    (root / "contracts" / "legacy_a").mkdir(parents=True, exist_ok=True)
    (root / "contracts" / "legacy_b").mkdir(parents=True, exist_ok=True)
    (root / "gold").mkdir(parents=True, exist_ok=True)
    man = []
    with (root / "usage.csv").open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["contract_id", "trailing_12m_units"])
        for it in c.items:
            w.writerow([it.doc_id, f"{it.units:.6f}"])
    for it in c.items:
        rel = f"contracts/legacy_{it.entity.lower()}/{it.doc_id}.txt"
        (root / rel).write_text(it.text)
        man.append({"contract_id": it.doc_id, "entity": it.entity, "style": it.style, "path": rel, "sha256": it.sha256})
        if it.gold is not None:
            (root / "gold" / f"{it.doc_id}.json").write_text(json.dumps({
                "terms": it.gold.model_dump(mode="json"),
                "spans": {k: [list(s) for s in v] for k, v in it.spans.items()},
                "vendor_id": it.vendor_id,
                "base_terms": it.base_gold.model_dump(mode="json") if it.base_gold is not None else None,
            }, indent=1))
    (root / "manifest.json").write_text(json.dumps({"meta": c.meta, "contracts": man}, indent=1))


def load_corpus(root: Path, with_gold: bool = True) -> Corpus:
    man = json.loads((root / "manifest.json").read_text())
    usage: dict[str, float] = {}
    up = root / "usage.csv"
    if up.exists():
        with up.open() as fh:
            for row in csv.DictReader(fh):
                usage[row["contract_id"]] = float(row["trailing_12m_units"])
    items = []
    for m in man["contracts"]:
        text = (root / m["path"]).read_text()
        gold = None
        spans: dict[str, list[tuple[int, int]]] = {}
        vid = None
        base = None
        gp = root / "gold" / f"{m['contract_id']}.json"
        if with_gold and gp.exists():
            g = json.loads(gp.read_text())
            gold = Terms.model_validate(g["terms"])
            spans = {k: [(a, b) for a, b in v] for k, v in g["spans"].items()}
            vid = g.get("vendor_id")
            base = Terms.model_validate(g["base_terms"]) if g.get("base_terms") else None
        items.append(CorpusItem(m["contract_id"], m["entity"], m["style"], text, m["sha256"],
                                usage.get(m["contract_id"], 0.0), gold, spans, vid, base))
    return Corpus(items, man.get("meta", {}))


def load_text_dir(root: Path) -> Corpus:
    """Ingest plain-text contracts with no ground truth (e.g. OCR/PDF-to-text output of real agreements).

    Layout: ``root/**/*.txt``; a parent folder whose name ends in ``_b`` marks the acquired entity (B), everything
    else is entity A.  Optional ``root/usage.csv`` (contract_id,trailing_12m_units) supplies volumes.
    """
    usage: dict[str, float] = {}
    up = root / "usage.csv"
    if up.exists():
        with up.open() as fh:
            for row in csv.DictReader(fh):
                usage[row["contract_id"]] = float(row["trailing_12m_units"])
    items = []
    for p in sorted(root.rglob("*.txt")):
        text = p.read_text(errors="replace")
        ent = "B" if p.parent.name.lower().endswith("_b") else "A"
        items.append(CorpusItem(p.stem, ent, -1, text, sha(text), usage.get(p.stem, 0.0)))
    return Corpus(items, {"source": str(root), "synthetic": False})
