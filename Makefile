PYTHON ?= python3
VENV ?= .venv

.PHONY: install test check ui-test run lock audit

install:
	$(PYTHON) -m venv $(VENV)
	$(VENV)/bin/pip install --upgrade pip
	$(VENV)/bin/pip install --require-hashes -r requirements-dev.lock
	$(VENV)/bin/pip install --no-deps -e .

test:
	$(VENV)/bin/pytest -q

check:
	$(VENV)/bin/python -m compileall -q runner_control runner_agent
	$(VENV)/bin/pyflakes runner_control runner_agent tests
	node --check runner_control/static/app.js

lock:
	$(VENV)/bin/pip install -q pip-tools
	$(VENV)/bin/pip-compile --generate-hashes --strip-extras --output-file=requirements.lock pyproject.toml
	$(VENV)/bin/pip-compile --generate-hashes --strip-extras --extra dev --allow-unsafe --output-file=requirements-dev.lock pyproject.toml

audit:
	$(VENV)/bin/pip-audit
	cd ui-tests && npm audit --audit-level=high

ui-test:
	cd ui-tests && npm ci && npm test

run:
	$(VENV)/bin/runner-control serve --host 127.0.0.1 --port 8080
