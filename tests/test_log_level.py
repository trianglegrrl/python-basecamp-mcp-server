"""Tests for `_resolve_log_level` in basecamp_fastmcp.py.

Pins the env-override contract for `BASECAMP_MCP_LOG_LEVEL`. Operators set
this in /etc/default/basecamp-mcp on the box to temporarily bump the
service to DEBUG for a diagnostic window without touching code or
redeploying. See pn-ai-portal#107 for the post-mortem. Mirrors the shape of
`tests/test_log_file_path.py` for the sibling `BASECAMP_MCP_LOG_FILE`
override.
"""

import logging

from basecamp_fastmcp import _resolve_log_level


def test_default_falls_back_to_info(monkeypatch):
    """Unset env var → INFO (matches previously-effective production behavior)."""
    monkeypatch.delenv('BASECAMP_MCP_LOG_LEVEL', raising=False)
    assert _resolve_log_level() == logging.INFO


def test_explicit_debug_returns_debug(monkeypatch):
    """`DEBUG` env var → logging.DEBUG (the operator diagnostic path)."""
    monkeypatch.setenv('BASECAMP_MCP_LOG_LEVEL', 'DEBUG')
    assert _resolve_log_level() == logging.DEBUG


def test_lowercase_is_case_insensitive(monkeypatch):
    """Lowercase `debug` resolves to DEBUG — operators shouldn't be tripped
    up by shell-case habits in /etc/default/basecamp-mcp."""
    monkeypatch.setenv('BASECAMP_MCP_LOG_LEVEL', 'debug')
    assert _resolve_log_level() == logging.DEBUG


def test_unrecognized_value_falls_back_to_info(monkeypatch):
    """Typo or non-level string → INFO fallback.

    A startup crash on a typo would defeat the point of having an
    operator-editable knob; the service should keep serving at the safe
    default while the operator fixes the env file.
    """
    monkeypatch.setenv('BASECAMP_MCP_LOG_LEVEL', 'VERBOSE')
    assert _resolve_log_level() == logging.INFO


def test_empty_string_falls_back_to_info(monkeypatch):
    """An empty-string env var is treated as 'unset' → INFO default."""
    monkeypatch.setenv('BASECAMP_MCP_LOG_LEVEL', '')
    assert _resolve_log_level() == logging.INFO
