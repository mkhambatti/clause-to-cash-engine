"""Minimal Model Context Protocol server (stdio, newline-delimited JSON-RPC 2.0) - stdlib only.

Exposes the verified contract workspace to any MCP client (Claude Desktop, Claude Code, an internal agent).
Every tool refuses to answer from unverified fields - see ``service.py``.

    claude mcp add clause-to-cash -- python -m clause_to_cash.mcp_server
"""
from __future__ import annotations

import json
import sys
from typing import Any, Callable, Optional, TextIO

from .service import Workspace, default_corpus

PROTOCOL = "2024-11-05"

TOOLS: list[dict[str, Any]] = [
    {"name": "list_deadlines", "description": "Contract notice deadlines / lock-ins / expiries within N days, with dollars that auto-commit if missed.",
     "inputSchema": {"type": "object", "properties": {"within_days": {"type": "integer", "default": 90}, "min_at_stake": {"type": "number", "default": 0}}}},
    {"name": "get_contract", "description": "Extracted terms for one contract with per-field verification status and the verbatim evidence.",
     "inputSchema": {"type": "object", "properties": {"contract_id": {"type": "string"}}, "required": ["contract_id"]}},
    {"name": "check_notice", "description": "Would a non-renewal notice sent on a given date be timely? Refuses (verdict REFUSED) when the date-driving fields are unverified.",
     "inputSchema": {"type": "object", "properties": {"contract_id": {"type": "string"}, "send_on": {"type": "string", "description": "ISO date; default today"}}, "required": ["contract_id"]}},
    {"name": "review_queue", "description": "Contracts with fields the verification gate sent to a human, with reasons.",
     "inputSchema": {"type": "object", "properties": {"limit": {"type": "integer", "default": 25}}}},
    {"name": "savings_plan", "description": "The sequenced consolidation plan (exact optimizer) with legal and commercial prerequisites per action.",
     "inputSchema": {"type": "object", "properties": {"limit": {"type": "integer", "default": 15}}}},
]


class Server:
    def __init__(self, ws: Workspace) -> None:
        self.ws = ws
        self.handlers: dict[str, Callable[..., dict[str, Any]]] = {
            "list_deadlines": ws.list_deadlines, "get_contract": ws.get_contract, "check_notice": ws.check_notice,
            "review_queue": ws.review_queue, "savings_plan": ws.savings_plan}

    def handle(self, msg: dict[str, Any]) -> Optional[dict[str, Any]]:
        mid, method, params = msg.get("id"), msg.get("method", ""), msg.get("params") or {}
        if mid is None:  # notification
            return None

        def ok(result: Any) -> dict[str, Any]:
            return {"jsonrpc": "2.0", "id": mid, "result": result}

        def err(code: int, message: str) -> dict[str, Any]:
            return {"jsonrpc": "2.0", "id": mid, "error": {"code": code, "message": message}}

        if method == "initialize":
            return ok({"protocolVersion": params.get("protocolVersion", PROTOCOL), "capabilities": {"tools": {}},
                       "serverInfo": {"name": "clause-to-cash", "version": "0.1.0"}})
        if method == "ping":
            return ok({})
        if method == "tools/list":
            return ok({"tools": TOOLS})
        if method == "tools/call":
            name, args = params.get("name"), params.get("arguments") or {}
            fn = self.handlers.get(str(name))
            if fn is None:
                return err(-32602, f"unknown tool {name!r}")
            try:
                res = fn(**args)
                return ok({"content": [{"type": "text", "text": json.dumps(res, indent=1)}], "isError": False})
            except (KeyError, ValueError, TypeError) as e:
                return ok({"content": [{"type": "text", "text": f"error: {e}"}], "isError": True})
        return err(-32601, f"method not found: {method}")

    def serve(self, rd: TextIO = sys.stdin, wr: TextIO = sys.stdout) -> None:
        for line in rd:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                wr.write(json.dumps({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}}) + "\n")
                wr.flush()
                continue
            out = self.handle(msg)
            if out is not None:
                wr.write(json.dumps(out) + "\n")
                wr.flush()


def main() -> None:
    Server(Workspace(default_corpus())).serve()


if __name__ == "__main__":
    main()
