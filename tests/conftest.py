import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from clause_to_cash.extract.rules import RuleExtractor  # noqa: E402
from clause_to_cash.pipeline import run_pipeline  # noqa: E402
from clause_to_cash.synth.generate import generate  # noqa: E402


@pytest.fixture(scope="session")
def corpus():
    return generate(7)


@pytest.fixture(scope="session")
def extractions(corpus):
    ex = RuleExtractor()
    return {it.doc_id: ex.extract(it.doc_id, it.text) for it in corpus.items}


@pytest.fixture(scope="session")
def gold_run(corpus):
    return run_pipeline(corpus, None, review="gold")
