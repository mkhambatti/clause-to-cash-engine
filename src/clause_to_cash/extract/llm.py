"""Claude extractor (Messages API, forced tool use, prompt caching) + agreement ensemble.

* No SDK dependency: a ~40-line stdlib HTTP transport.  ``transport`` is injectable, which is how the test
  suite exercises request construction, response parsing, retries and failure handling with NO network.
* Whatever the model returns is just a *proposal*; ``verify.gate`` decides what is trusted.
* HONESTY NOTE: this adapter has been tested against a fake transport only.  It has not been run against the
  live API in this repository's evaluation, so no accuracy numbers are claimed for it.
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from typing import Any, Callable, Optional

from ..evals.metrics import values_equal
from ..schema import FIELD_LIST, Extraction, FieldExtraction
from .base import Extractor
from .prompts import SYSTEM_PROMPT, TOOL_NAME, tool_schema, user_message

API_URL = "https://api.anthropic.com/v1/messages"
API_VERSION = "2023-06-01"
DEFAULT_MODEL = "claude-sonnet-5-5"

Transport = Callable[[dict[str, Any]], dict[str, Any]]


class TransientError(Exception):
    """Retryable API failure (429 / 5xx / network)."""


def http_transport(api_key: str, timeout: float = 120.0, url: str = API_URL) -> Transport:
    def send(payload: dict[str, Any]) -> dict[str, Any]:
        req = urllib.request.Request(url, data=json.dumps(payload).encode(), method="POST", headers={
            "content-type": "application/json", "x-api-key": api_key, "anthropic-version": API_VERSION})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 - fixed https endpoint
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            if e.code in (408, 409, 429) or e.code >= 500:
                raise TransientError(f"HTTP {e.code}") from e
            raise RuntimeError(f"HTTP {e.code}: {e.read()[:300]!r}") from e
        except (urllib.error.URLError, TimeoutError) as e:
            raise TransientError(str(e)) from e
    return send


class ClaudeExtractor:
    def __init__(self, model: str = DEFAULT_MODEL, transport: Optional[Transport] = None, api_key: Optional[str] = None,
                 max_retries: int = 3, backoff_s: float = 2.0, sleep: Callable[[float], None] = time.sleep) -> None:
        if transport is None:
            key = api_key or os.environ.get("ANTHROPIC_API_KEY")
            if not key:
                raise RuntimeError("ANTHROPIC_API_KEY is not set (use --extractor rules for the offline baseline)")
            transport = http_transport(key)
        self.transport, self.model = transport, model
        self.max_retries, self.backoff_s, self._sleep = max_retries, backoff_s, sleep
        self.name = f"claude:{model}"

    def request(self, doc_id: str, text: str) -> dict[str, Any]:
        return {
            "model": self.model,
            "max_tokens": 8000,
            # the long, static system prompt and tool schema are cached across the whole corpus
            "system": [{"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}],
            "tools": [{**tool_schema(), "cache_control": {"type": "ephemeral"}}],
            "tool_choice": {"type": "tool", "name": TOOL_NAME},
            "messages": [{"role": "user", "content": user_message(doc_id, text)}],
        }

    def extract(self, doc_id: str, text: str) -> Extraction:
        t0 = time.time()
        payload = self.request(doc_id, text)
        last: Optional[Exception] = None
        for attempt in range(self.max_retries + 1):
            try:
                resp = self.transport(payload)
                break
            except TransientError as e:
                last = e
                self._sleep(self.backoff_s * (2 ** attempt))
            except Exception as e:  # non-retryable: fail closed
                return Extraction(doc_id=doc_id, extractor=self.name, error=str(e), latency_s=time.time() - t0)
        else:
            return Extraction(doc_id=doc_id, extractor=self.name, error=f"gave up after retries: {last}", latency_s=time.time() - t0)
        ext = parse_response(doc_id, self.name, resp)
        ext.latency_s = time.time() - t0
        return ext


def parse_response(doc_id: str, name: str, resp: dict[str, Any]) -> Extraction:
    """Tolerant parser: anything missing or malformed becomes an empty, zero-confidence field (-> review)."""
    ext = Extraction(doc_id=doc_id, extractor=name)
    usage = resp.get("usage") or {}
    ext.input_tokens = int(usage.get("input_tokens", 0)) + int(usage.get("cache_read_input_tokens", 0) or 0) \
        + int(usage.get("cache_creation_input_tokens", 0) or 0)
    ext.output_tokens = int(usage.get("output_tokens", 0))
    block = next((b for b in resp.get("content", []) if b.get("type") == "tool_use" and b.get("name") == TOOL_NAME), None)
    if block is None:
        ext.error = f"no {TOOL_NAME} tool call (stop_reason={resp.get('stop_reason')})"
        return ext
    data = block.get("input") or {}
    for f in FIELD_LIST:
        raw = data.get(f.name)
        if not isinstance(raw, dict):
            continue
        quotes = raw.get("quotes") or []
        quotes = [q for q in quotes if isinstance(q, str) and q.strip()] if isinstance(quotes, list) else []
        try:
            conf = float(raw.get("confidence", 0.0))
        except (TypeError, ValueError):
            conf = 0.0
        ext.fields[f.name] = FieldExtraction(value=raw.get("value"), quotes=quotes, confidence=max(0.0, min(1.0, conf)))
    return ext


class EnsembleExtractor:
    """Run several independent extractors; fields they AGREE on are corroborated, fields they disagree on
    are forced to review (confidence 0).  An LLM plus the offline rules baseline is already a useful pair."""

    def __init__(self, members: list[Extractor]) -> None:
        self.members = members
        self.name = "ensemble(" + "+".join(m.name for m in members) + ")"

    def extract(self, doc_id: str, text: str) -> Extraction:
        outs = [m.extract(doc_id, text) for m in self.members]
        merged = Extraction(doc_id=doc_id, extractor=self.name,
                            input_tokens=sum(o.input_tokens for o in outs), output_tokens=sum(o.output_tokens for o in outs),
                            latency_s=max(o.latency_s for o in outs))
        errs = [o.error for o in outs if o.error]
        if errs:
            merged.error = "; ".join(errs)
        primary = outs[0]
        for f in FIELD_LIST:
            fes = [o.get(f.name) for o in outs]
            agree = all(values_equal(f.name, fe.value, fes[0].value) for fe in fes[1:])
            base = primary.get(f.name)
            quotes = list(dict.fromkeys(q for fe in fes for q in fe.quotes))
            if agree:
                merged.fields[f.name] = FieldExtraction(value=base.value, quotes=quotes, confidence=min(fe.confidence for fe in fes))
            else:
                merged.fields[f.name] = FieldExtraction(value=base.value, quotes=quotes, confidence=0.0)
        return merged
