"""Command line:  python -m clause_to_cash <command>

    demo      build the dashboard from the shipped synthetic corpus (offline, no API key, ~5 s)
    run       full pipeline on a corpus (or a folder of .txt contracts) with a chosen extractor
    generate  write a fresh synthetic corpus with ground truth
    eval      reproduce every number in docs/RESULTS.md
    mcp       start the agent-facing MCP server on stdio
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
import time
import webbrowser
from pathlib import Path

from .corpus import load_corpus, load_text_dir, save_corpus
from .extract.base import Extractor
from .extract.rules import RuleExtractor
from .report import render_dashboard
from .service import default_corpus
from .synth.generate import generate


def make_extractor(name: str, model: str) -> Extractor:
    if name == "rules":
        return RuleExtractor()
    from .extract.llm import ClaudeExtractor, EnsembleExtractor
    if name == "claude":
        return ClaudeExtractor(model=model)
    if name == "ensemble":
        return EnsembleExtractor([ClaudeExtractor(model=model), RuleExtractor()])
    raise SystemExit(f"unknown extractor {name!r}")


def _results(path: Path):  # type: ignore[no-untyped-def]
    for cand in (path, Path(__file__).resolve().parents[2] / "docs" / "results.json"):
        if cand.exists():
            return json.loads(cand.read_text())
    return None


def cmd_run(a: argparse.Namespace) -> int:
    t0 = time.time()
    if a.contracts:
        corpus = load_text_dir(Path(a.contracts))
    else:
        corpus = default_corpus(a.corpus)
    ex = make_extractor(a.extractor, a.model)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    payload = render_dashboard(corpus, ex, out / "dashboard.html", _results(Path(a.results)))
    b, af = payload["before"], payload["after"]
    (out / "plan.json").write_text(json.dumps({"queue_open": b["plan"], "queue_cleared": af["plan"]}, indent=1))
    (out / "review_queue.json").write_text(json.dumps(
        [{"contract": d["id"], "flagged": d["flagged"], "fields": {f["name"]: f["reasons"] for f in d["fields"] if f["status"] == "review" and f["critical"]}}
         for d in payload["docs"] if not d["accepted"]], indent=1))
    with (out / "calendar.csv").open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["due", "internal_due", "days", "contract", "entity", "vendor", "category", "kind", "annual_cost_usd", "at_stake_usd", "provisional"])
        for e in af["calendar"]:
            w.writerow([e["due"], e["internal_due"] or "", e["days"], e["cid"], e["entity"], e["vendor"], e["category"], e["kind"], e["annual_cost"], e["at_stake"], e["provisional"]])
    m = payload["meta"]
    print(f"extractor {ex.name}: {m['docs_accepted']}/{m['docs']} contracts auto-verified; "
          f"{m['fields_flagged']}/{m['fields_total']} critical fields to review")
    print(f"queue open   : {b['kpi']['contracts']:>3} contracts in plan, run-rate ${b['kpi']['run_rate'] / 1e6:6.1f}M, NPV ${b['kpi']['npv'] / 1e6:6.1f}M")
    if m["has_gold"]:
        print(f"queue cleared: {af['kpi']['contracts']:>3} contracts in plan, run-rate ${af['kpi']['run_rate'] / 1e6:6.1f}M, NPV ${af['kpi']['npv'] / 1e6:6.1f}M"
              f"  (simulated reviewer, synthetic ground truth)")
    n90 = af["kpi"]["next90_n"]
    print(f"{n90} auto-renewing contracts have notice deadlines in the next 90 days (${af['kpi']['next90_at_stake'] / 1e6:.1f}M at stake)")
    print(f"wrote {out}/dashboard.html, plan.json, review_queue.json, calendar.csv  ({time.time() - t0:.1f}s)")
    if a.open:
        webbrowser.open((out / "dashboard.html").resolve().as_uri())
    return 0


def cmd_generate(a: argparse.Namespace) -> int:
    c = generate(a.seed)
    save_corpus(c, Path(a.out))
    print(f"wrote {len(c.items)} synthetic contracts (seed {a.seed}) to {a.out}")
    return 0


def cmd_eval(a: argparse.Namespace) -> int:
    from .evals.run import main as eval_main
    sys.argv = ["eval", "--out", a.out] + (["--quick"] if a.quick else [])
    eval_main()
    return 0


def cmd_mcp(a: argparse.Namespace) -> int:
    from .mcp_server import main as mcp_main
    mcp_main()
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="clause-to-cash", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("demo", "run"):
        p = sub.add_parser(name, help="build dashboard + plan" if name == "run" else "one-command offline demo")
        p.add_argument("--corpus", help="corpus directory (default: shipped data/corpus)")
        p.add_argument("--contracts", help="folder of plain-text contracts (no ground truth)")
        p.add_argument("--extractor", choices=["rules", "claude", "ensemble"], default="rules")
        p.add_argument("--model", default="claude-sonnet-5-5")
        p.add_argument("--out", default="out")
        p.add_argument("--results", default="docs/results.json", help="evaluation results to embed (optional)")
        p.add_argument("--open", action="store_true", help="open the dashboard in a browser")
        p.set_defaults(fn=cmd_run)
    g = sub.add_parser("generate", help="write a synthetic corpus")
    g.add_argument("--seed", type=int, default=7)
    g.add_argument("--out", default="data/corpus")
    g.set_defaults(fn=cmd_generate)
    e = sub.add_parser("eval", help="reproduce docs/RESULTS.md")
    e.add_argument("--out", default="docs")
    e.add_argument("--quick", action="store_true")
    e.set_defaults(fn=cmd_eval)
    m = sub.add_parser("mcp", help="MCP server on stdio")
    m.set_defaults(fn=cmd_mcp)
    a = ap.parse_args(argv)
    return int(a.fn(a))


if __name__ == "__main__":
    raise SystemExit(main())
