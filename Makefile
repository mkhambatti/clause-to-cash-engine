PY ?= python3
export PYTHONPATH := src

.PHONY: demo test typecheck eval eval-quick corpus all
demo:        ## offline demo: builds out/dashboard.html (no API key, ~1 s)
	$(PY) -m clause_to_cash demo --out out --open
test:
	$(PY) -m pytest
typecheck:
	$(PY) -m mypy src
eval:        ## reproduce docs/RESULTS.md (about 3-5 minutes)
	$(PY) -m clause_to_cash eval --out docs
eval-quick:
	$(PY) -m clause_to_cash eval --out out/eval --quick
corpus:      ## regenerate the shipped synthetic corpus (byte-identical for seed 7)
	$(PY) -m clause_to_cash generate --seed 7 --out data/corpus
all: test typecheck demo
