PY ?= python3

.PHONY: help install lint test experiments bench charts receipts verify evidence pdf clean

help:
	@echo "install      editable install with the dev extra"
	@echo "lint         ruff check and format check"
	@echo "test         pytest with coverage, writing reports/"
	@echo "experiments  re-run all five, writing docs/experiments/*.json"
	@echo "bench        time the exact allocation against a sampled one"
	@echo "charts       redraw the cost chart from the benchmark json"
	@echo "receipts     re-measure every figure, then check it against the documents"
	@echo "verify       lint, tests and receipts. No JVM, no network, no API key"
	@echo "evidence     diagram, screenshots, demo recording and the README image check."
	@echo "             Needs the evidence extra: pip install -e '.[evidence]'"
	@echo "pdf          lay out the defense guide for offline reading"

install:
	$(PY) -m pip install -e ".[dev]"

lint:
	$(PY) -m ruff check src tests tools experiments benchmark
	$(PY) -m ruff format --check src tests tools experiments benchmark

test:
	$(PY) -m pytest -q --junitxml=reports/junit.xml \
		--cov=prefixcost --cov-report=json:reports/coverage.json \
		--cov-report=xml:reports/coverage.xml --cov-report=term-missing

experiments:
	$(PY) experiments/exp01_the_two_exact_anchors.py
	$(PY) experiments/exp02_the_same_usage_two_bills.py
	$(PY) experiments/exp03_the_policy_not_the_capacity.py
	$(PY) experiments/exp04_who_pays_the_surplus.py
	$(PY) experiments/exp05_no_scheme_has_all_three.py

bench:
	$(PY) benchmark/bench_attribution.py

# Reads benchmark/results/attribution_latency.json rather than timing anything, so
# the chart and the README table can never disagree about a number.
charts:
	$(PY) benchmark/plot_results.py

# Reads the reports that `make test` produced rather than running pytest again, so
# a red test surfaces as a failing test target and not as a traceback from a
# metrics script.
receipts:
	$(PY) tools/collect_metrics.py --skip-tests
	$(PY) tools/check_numbers.py --strict

# Everything, from a clean clone, with no JVM, no network and no API key. The
# tokeniser is trained from the committed corpus rather than downloaded, which is
# what makes this one command rather than a setup section.
verify: lint test receipts

evidence:
	$(PY) tools/render_diagram.py
	$(PY) tools/capture_screenshots.py
	$(PY) tools/record_demo.py
	$(PY) tools/check_readme.py README.md

pdf:
	$(PY) tools/build_pdf.py docs/defense-guide.md

clean:
	rm -rf reports .cache .pytest_cache .ruff_cache
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
