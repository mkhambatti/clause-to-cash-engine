"""Extractor interface.  Everything downstream depends only on this."""
from __future__ import annotations

from typing import Protocol

from ..schema import Extraction


class Extractor(Protocol):
    name: str

    def extract(self, doc_id: str, text: str) -> Extraction:  # pragma: no cover - protocol
        ...
