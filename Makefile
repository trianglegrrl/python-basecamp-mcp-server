# Convenience targets for the Basecamp MCP Server. Real CI runs
# `pytest -m "not live"` directly; these targets are for operator
# laptops and the live workflow in .github/workflows/live.yml.

PYTHON ?= python
PYTEST ?= $(PYTHON) -m pytest

.PHONY: test test-live test-live-cleanup smoke help

help:
	@echo "Targets:"
	@echo "  test                — fast unit + integration tests (no BC API)"
	@echo "  test-live           — sandbox-only lifecycle tests (BC API)"
	@echo "  test-live-cleanup   — sweep .test-live-ids-*.json sidecars; trash all entries"
	@echo "  smoke               — local streamable-http handshake smoke"

test:
	$(PYTEST) -v

test-live:
	$(PYTEST) -v -m live

test-live-cleanup:
	$(PYTHON) scripts/test_live_cleanup.py

smoke:
	$(PYTHON) scripts/smoke_streamable_http.py --port 8090
