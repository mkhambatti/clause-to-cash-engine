import json
import re
import tempfile
from pathlib import Path

import pytest

from clause_to_cash.cli import main as cli_main
from clause_to_cash.extract.llm import ClaudeExtractor, EnsembleExtractor, TransientError, parse_response
from clause_to_cash.extract.prompts import TOOL_NAME, tool_schema
from clause_to_cash.mcp_server import Server
from clause_to_cash.schema import FIELD_LIST
from clause_to_cash.service import Workspace
from clause_to_cash.verify.gate import assess


def _tool_response(it, conf=0.9, tweak=None):
    g = it.gold.model_dump(mode="json")
    inp = {k: {"value": v, "quotes": [it.text[a:b] for a, b in it.spans.get(k, [])], "confidence": conf} for k, v in g.items()}
    if tweak:
        tweak(inp)
    return {"content": [{"type": "tool_use", "name": TOOL_NAME, "input": inp}], "usage": {"input_tokens": 1000, "output_tokens": 200,
            "cache_read_input_tokens": 500}, "stop_reason": "tool_use"}


def test_tool_schema_covers_every_field_and_requires_evidence():
    ts = tool_schema()
    props = ts["input_schema"]["properties"]
    assert set(props) == {f.name for f in FIELD_LIST}
    assert all(set(p["required"]) == {"value", "quotes", "confidence"} for p in props.values())
    json.dumps(ts)  # serialisable


def test_request_construction(corpus):
    ex = ClaudeExtractor(transport=lambda p: {})
    it = corpus.items[0]
    req = ex.request(it.doc_id, it.text)
    assert req["tool_choice"] == {"type": "tool", "name": TOOL_NAME}
    assert req["system"][0]["cache_control"]["type"] == "ephemeral" and req["tools"][0]["cache_control"]["type"] == "ephemeral"
    assert it.text in req["messages"][0]["content"] and "<contract>" in req["messages"][0]["content"]


def test_parse_retry_and_gate(corpus):
    it = next(i for i in corpus.items if i.style == 0)
    calls = []

    def transport(payload):
        calls.append(payload)
        if len(calls) < 3:
            raise TransientError("429")
        return _tool_response(it)

    slept = []
    ex = ClaudeExtractor(transport=transport, sleep=slept.append)
    e = ex.extract(it.doc_id, it.text)
    assert len(calls) == 3 and slept == [2.0, 4.0] and e.error is None
    assert e.input_tokens == 1500 and e.output_tokens == 200
    assert assess(it.text, e).accepted  # perfect answer with the true evidence passes the gate


def test_failures_fail_closed(corpus):
    it = corpus.items[0]
    boom = ClaudeExtractor(transport=lambda p: (_ for _ in ()).throw(RuntimeError("HTTP 400")), sleep=lambda s: None)
    e = boom.extract(it.doc_id, it.text)
    assert e.error and not assess(it.text, e).accepted
    flaky = ClaudeExtractor(transport=lambda p: (_ for _ in ()).throw(TransientError("503")), max_retries=1, sleep=lambda s: None)
    assert "gave up" in flaky.extract(it.doc_id, it.text).error
    nocall = parse_response(it.doc_id, "x", {"content": [{"type": "text", "text": "I refuse"}], "stop_reason": "end_turn"})
    assert nocall.error and not nocall.fields
    junk = parse_response(it.doc_id, "x", {"content": [{"type": "tool_use", "name": TOOL_NAME, "input": {"pricing": "oops", "mfn": {"value": True, "quotes": "x", "confidence": "high"}}}]})
    assert junk.get("mfn").confidence == 0.0 and junk.get("mfn").quotes == [] and junk.get("pricing").value is None


def test_hallucinated_quote_is_caught_end_to_end(corpus):
    it = next(i for i in corpus.items if i.style == 0)

    def tweak(inp):
        inp["initial_term_months"] = {"value": 99, "quotes": ["The initial term is ninety-nine months."], "confidence": 0.99}

    e = ClaudeExtractor(transport=lambda p: _tool_response(it, tweak=tweak)).extract(it.doc_id, it.text)
    a = assess(it.text, e)
    assert a.fields["initial_term_months"].status == "review" and "UNGROUNDED" in a.fields["initial_term_months"].reasons


def test_ensemble_forces_review_on_disagreement(corpus):
    it = next(i for i in corpus.items if i.style == 0)
    good = ClaudeExtractor(transport=lambda p: _tool_response(it))
    bad = ClaudeExtractor(transport=lambda p: _tool_response(it, tweak=lambda inp: inp["initial_term_months"].update(value=7)))
    good.name, bad.name = "good", "bad"
    ens = EnsembleExtractor([good, bad]).extract(it.doc_id, it.text)
    assert ens.get("initial_term_months").confidence == 0.0 and ens.get("effective_date").confidence == 0.9
    assert assess(it.text, ens).fields["initial_term_months"].status == "review"


@pytest.fixture(scope="module")
def ws(corpus):
    return Workspace(corpus)


def _rpc(srv, method, params=None, mid=1):
    return srv.handle({"jsonrpc": "2.0", "id": mid, "method": method, "params": params or {}})


def test_mcp_protocol(ws):
    srv = Server(ws)
    assert _rpc(srv, "initialize", {"protocolVersion": "2025-03-26"})["result"]["serverInfo"]["name"] == "clause-to-cash"
    assert srv.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None
    names = {t["name"] for t in _rpc(srv, "tools/list")["result"]["tools"]}
    assert names == {"list_deadlines", "get_contract", "check_notice", "review_queue", "savings_plan"}
    assert _rpc(srv, "nope")["error"]["code"] == -32601
    assert _rpc(srv, "tools/call", {"name": "nope"})["error"]["code"] == -32602
    bad = _rpc(srv, "tools/call", {"name": "get_contract", "arguments": {"contract_id": "ZZZ"}})["result"]
    assert bad["isError"]


def test_mcp_refuses_to_answer_from_unverified_fields(ws, corpus):
    srv = Server(ws)
    unverified = next(d for d in ws.run.docs if not d.assessment.accepted and d.assessment.review_fields)
    r = json.loads(_rpc(srv, "tools/call", {"name": "check_notice", "arguments": {"contract_id": unverified.item.doc_id}})["result"]["content"][0]["text"])
    notice_flagged = bool(r["needs_review"])
    assert (r["verdict"] == "REFUSED") == notice_flagged
    verified = next(d for d in ws.run.docs if d.assessment.accepted and d.terms.auto_renew)
    r2 = json.loads(_rpc(srv, "tools/call", {"name": "check_notice", "arguments": {"contract_id": verified.item.doc_id, "send_on": "2000-01-01"}})["result"]["content"][0]["text"])
    assert r2["verdict"] == "TIMELY" and r2["safe_to_act"]
    r3 = json.loads(_rpc(srv, "tools/call", {"name": "check_notice", "arguments": {"contract_id": verified.item.doc_id, "send_on": "2099-01-01"}})["result"]["content"][0]["text"])
    assert r3["verdict"] == "TOO_LATE"
    # the human-readable message must agree with the verdict (it once described the lifecycle only)
    assert "misses" in r3["message"] or "also misses" in r3["message"]
    assert "meets" in r2["message"] or "in time" in r2["message"]
    dl = json.loads(_rpc(srv, "tools/call", {"name": "list_deadlines", "arguments": {"within_days": 120}})["result"]["content"][0]["text"])
    assert dl["count"] > 0 and dl["events"] == sorted(dl["events"], key=lambda e: e["due"])


def test_mcp_stdio_loop(ws):
    import io
    srv = Server(ws)
    inp = io.StringIO('{"jsonrpc":"2.0","id":1,"method":"ping"}\nnot json\n\n{"jsonrpc":"2.0","id":2,"method":"tools/list"}\n')
    out = io.StringIO()
    srv.serve(inp, out)
    lines = [json.loads(x) for x in out.getvalue().splitlines()]
    assert lines[0]["result"] == {} and lines[1]["error"]["code"] == -32700 and "tools" in lines[2]["result"]


def _embedded(html: str):
    m = re.search(r'<script id="data" type="application/json">(.*?)</script>', html, re.S)
    return json.loads(m.group(1))


def test_cli_demo_builds_self_contained_dashboard(tmp_path):
    out = tmp_path / "o"
    assert cli_main(["demo", "--out", str(out), "--results", str(tmp_path / "none.json")]) == 0
    html = (out / "dashboard.html").read_text()
    assert "/*__DATA__*/null" not in html, "template placeholder must be replaced"
    # nothing may be fetched from the network when the page loads (plain <a href> links are fine)
    assert not re.search(r'src\s*=\s*["\']?(https?:)?//|<link\b|@import|url\(\s*["\']?(https?:)?//', html), "dashboard must be fully self-contained"
    d = _embedded(html)
    assert d["meta"]["docs"] == len(d["docs"]) > 50
    for doc in d["docs"]:
        for f in doc["fields"]:
            for s, e in f["spans"]:
                assert 0 <= s < e <= len(doc["text"])
    assert d["after"]["kpi"]["npv"] > d["before"]["kpi"]["npv"] > 0
    assert (out / "calendar.csv").read_text().startswith("due,")
    assert json.loads((out / "review_queue.json").read_text())


def test_cli_runs_on_plain_text_without_ground_truth(corpus, tmp_path):
    root = tmp_path / "contracts" / "legacy_a"
    root.mkdir(parents=True)
    for it in corpus.items[:12]:
        (root / f"{it.doc_id}.txt").write_text(it.text)
    out = tmp_path / "o"
    assert cli_main(["run", "--contracts", str(tmp_path / "contracts"), "--out", str(out), "--results", str(tmp_path / "x.json")]) == 0
    d = _embedded((out / "dashboard.html").read_text())
    assert d["meta"]["synthetic"] is False and d["before"]["kpi"]["npv"] == d["after"]["kpi"]["npv"]


def test_generate_command(tmp_path):
    assert cli_main(["generate", "--seed", "3", "--out", str(tmp_path / "c")]) == 0
    assert (tmp_path / "c" / "manifest.json").exists()


def test_worked_example_script_matches_project_page(capsys, monkeypatch):
    import runpy
    monkeypatch.setattr("sys.argv", ["worked_example.py"])
    from pathlib import Path
    runpy.run_path(str(Path(__file__).resolve().parents[1] / "scripts" / "worked_example.py"), run_name="__main__")
    out = capsys.readouterr().out
    assert "clean extraction -> accepted" in out
    assert "review  ['UNGROUNDED']" in out and "review  ['CUE_MISSED']" in out
    assert "'CONTRADICTED', 'INVARIANT:IMPLAUSIBLE_NOTICE', 'INVARIANT:NOTICE_EXCEEDS_TERM'" in out
