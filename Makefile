PY ?= python3

.PHONY: all offline setup clean

## One command: raw download -> staging -> model -> mart -> quality checks -> analysis
all: setup
	$(PY) run_pipeline.py

## Same pipeline from the committed, md5-verified snapshot (no network)
offline: setup
	$(PY) run_pipeline.py --offline

setup:
	$(PY) -m pip install -q -r requirements.txt

clean:
	rm -rf data/raw data/warehouse output
