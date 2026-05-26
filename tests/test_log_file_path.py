"""Tests for `_resolve_log_file_path` in basecamp_fastmcp.py.

Pins the env-override contract that hosted deployments rely on when the
service runs under systemd `ProtectSystem=strict` (project root read-only,
only `logs/` writable). Mirrors the shape of `test_token_storage.py` for
the sibling `BASECAMP_MCP_TOKEN_FILE` override.
"""

import os

from basecamp_fastmcp import PROJECT_ROOT, _resolve_log_file_path


def test_default_falls_back_to_project_root(monkeypatch):
    """Unset env var → project-root default (backward-compatible)."""
    monkeypatch.delenv('BASECAMP_MCP_LOG_FILE', raising=False)
    expected = os.path.join(PROJECT_ROOT, 'basecamp_fastmcp.log')
    assert _resolve_log_file_path() == expected


def test_env_override_returned_verbatim_when_absolute(monkeypatch, tmp_path):
    """Absolute path in env var is honored exactly."""
    override = str(tmp_path / 'custom-logs' / 'basecamp.log')
    monkeypatch.setenv('BASECAMP_MCP_LOG_FILE', override)
    assert _resolve_log_file_path() == override


def test_tilde_expansion(monkeypatch, tmp_path):
    """`~` in the override is expanded against $HOME at resolve time."""
    home_dir = tmp_path / 'home'
    home_dir.mkdir()
    monkeypatch.setenv('HOME', str(home_dir))
    monkeypatch.setenv('BASECAMP_MCP_LOG_FILE', '~/logs/basecamp.log')

    resolved = _resolve_log_file_path()

    assert resolved == str(home_dir / 'logs' / 'basecamp.log')
    # Sanity: no literal `~` survived the expansion (would mean a real
    # `~` subdir gets created at write time relative to cwd).
    assert '~' not in resolved


def test_env_var_expansion(monkeypatch, tmp_path):
    """`$VAR`-style references inside the override expand at resolve time."""
    logs_dir = str(tmp_path / 'opt' / 'basecamp-mcp' / 'logs')
    monkeypatch.setenv('CUSTOM_LOG_DIR', logs_dir)
    monkeypatch.setenv('BASECAMP_MCP_LOG_FILE', '$CUSTOM_LOG_DIR/basecamp_fastmcp.log')

    resolved = _resolve_log_file_path()

    assert resolved == os.path.join(logs_dir, 'basecamp_fastmcp.log')
    # Sanity: no literal `$CUSTOM_LOG_DIR` survived.
    assert '$' not in resolved


def test_empty_env_var_falls_back_to_default(monkeypatch):
    """An empty-string env var is treated as 'unset' — no log at literal ''."""
    monkeypatch.setenv('BASECAMP_MCP_LOG_FILE', '')
    expected = os.path.join(PROJECT_ROOT, 'basecamp_fastmcp.log')
    assert _resolve_log_file_path() == expected
