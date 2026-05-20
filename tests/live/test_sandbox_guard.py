"""Two-layer sandbox guard tests. These tests do NOT touch BC — they
exercise the guard's refusal paths with a stub client."""
# These tests do NOT touch BC; they exercise the guard's refusal paths with
# a stub client. They are intentionally NOT marked @pytest.mark.live so the
# default suite covers them.
from unittest.mock import MagicMock

import pytest

from auth.live_sandbox_guard import (
    DEFAULT_SENTINEL,
    SandboxGuardError,
    assert_sandbox,
)


def test_refuses_when_project_id_env_is_unset(monkeypatch):
    monkeypatch.delenv('BASECAMP_TEST_PROJECT_ID', raising=False)
    with pytest.raises(SandboxGuardError, match='BASECAMP_TEST_PROJECT_ID'):
        assert_sandbox(client=MagicMock())


def test_refuses_when_project_name_lacks_sentinel(monkeypatch):
    monkeypatch.setenv('BASECAMP_TEST_PROJECT_ID', '12345')
    client = MagicMock()
    client.get_project.return_value = {'id': 12345, 'name': 'production hot path'}
    with pytest.raises(SandboxGuardError, match='sentinel'):
        assert_sandbox(client=client)


def test_returns_project_id_when_both_layers_pass(monkeypatch):
    monkeypatch.setenv('BASECAMP_TEST_PROJECT_ID', '12345')
    client = MagicMock()
    client.get_project.return_value = {'id': 12345,
        'name': 'PN MCP_TEST_SANDBOX live tests'}
    assert assert_sandbox(client=client) == '12345'


def test_custom_sentinel_via_env_var(monkeypatch):
    monkeypatch.setenv('BASECAMP_TEST_PROJECT_ID', '12345')
    monkeypatch.setenv('BASECAMP_TEST_PROJECT_NAME_GUARD', 'CUSTOM_SENTINEL')
    client = MagicMock()
    client.get_project.return_value = {'id': 12345, 'name': 'CUSTOM_SENTINEL project'}
    assert assert_sandbox(client=client) == '12345'


def test_default_sentinel_is_the_documented_one():
    """If this constant ever changes, the Bootstrap section's sandbox
    project name must change with it."""
    assert DEFAULT_SENTINEL == 'MCP_TEST_SANDBOX'
