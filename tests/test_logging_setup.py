"""Regression test for the root-logger handler set installed by basecamp_fastmcp.

Pins the contract that importing basecamp_fastmcp REGISTERS BOTH:
  - a `logging.FileHandler` (writing to BASECAMP_MCP_LOG_FILE or fallback)
  - a `logging.StreamHandler` (writing to sys.stderr)

Without `force=True` on the `logging.basicConfig` call, the FileHandler is
silently dropped because `mcp.server.fastmcp` (or one of its sub-deps in
the FastMCP / uvicorn tree) installs a `StreamHandler` on the root logger
at import time, BEFORE basecamp_fastmcp's own basicConfig runs. Per the
Python docs, basicConfig is a no-op when the root logger already has
handlers (unless `force=True`). The bug presented as a 0-byte log file in
production despite the env var being set correctly and the service running
healthily — caught against pn-ai-portal#94's hosted deployment.

This test imports the module fresh and asserts both handler types are
present. A regression would surface as 'FileHandler not registered'.
"""

import importlib
import logging
import sys


def test_basicconfig_installs_both_handlers(tmp_path, monkeypatch):
    """Both FileHandler and StreamHandler must end up on root after import."""
    log_path = tmp_path / 'test_logging_setup.log'
    monkeypatch.setenv('BASECAMP_MCP_LOG_FILE', str(log_path))

    # Force a fresh module load. `basicConfig(force=True)` clears + reinstalls
    # handlers on each import; reload ensures we exercise the install path.
    sys.modules.pop('basecamp_fastmcp', None)
    importlib.import_module('basecamp_fastmcp')

    handler_types = {type(h).__name__ for h in logging.root.handlers}
    assert 'FileHandler' in handler_types, (
        f'FileHandler missing from root logger after import; got {handler_types}. '
        f'This is the pn-ai-portal#94 regression — basicConfig without force=True is '
        f'silently no-op when an import-time side-effect already added a handler.'
    )
    assert 'StreamHandler' in handler_types, (
        f'StreamHandler missing from root logger after import; got {handler_types}.'
    )


def test_file_handler_writes_to_configured_path(tmp_path, monkeypatch):
    """A `logger.info(...)` call after import should land in the configured file."""
    log_path = tmp_path / 'test_logging_setup_writes.log'
    monkeypatch.setenv('BASECAMP_MCP_LOG_FILE', str(log_path))

    sys.modules.pop('basecamp_fastmcp', None)
    import basecamp_fastmcp  # noqa: F401

    sentinel = 'sentinel-line-9d8f7a2c'
    logger = logging.getLogger('basecamp_fastmcp')
    logger.info(sentinel)

    # The FileHandler is buffered per-line at the OS level; flush explicitly
    # to make the assertion deterministic against any open writes.
    for h in logging.root.handlers:
        h.flush()

    assert log_path.exists(), f'Log file was never created at {log_path}'
    content = log_path.read_text()
    assert sentinel in content, (
        f'Sentinel line "{sentinel}" not in log file contents:\n{content[:500]}'
    )
