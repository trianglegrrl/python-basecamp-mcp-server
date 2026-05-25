"""Project setup tools: create_project / update_project / trash_project /
update_project_access.

Ports the project-write surface from the deprecated Node MCP repo to the
Python upstream. Mocked unit tests only — live tests live in
tests/live/test_lifecycle_projects.py and are gated by the `live` marker.
"""
import inspect
from unittest.mock import MagicMock, patch

import pytest


PROJECT_SETUP_TOOL_NAMES = [
    'create_project',
    'update_project',
    'trash_project',
    'update_project_access',
]


def test_every_project_setup_tool_takes_ctx_first():
    """Every migrated tool MUST take ctx as its first positional parameter so
    the FastMCP dispatcher can supply the request context. See PR T3."""
    import basecamp_fastmcp as bc
    for name in PROJECT_SETUP_TOOL_NAMES:
        fn = getattr(bc, name)
        params = list(inspect.signature(fn).parameters.values())
        assert params[0].name == 'ctx', \
            f"{name}: first param should be 'ctx', got {params[0].name!r}"


@pytest.mark.asyncio
@pytest.mark.parametrize("tool_name,kwargs", [
    ("create_project",        {"name": "x"}),
    ("update_project",        {"project_id": "1"}),
    ("trash_project",         {"project_id": "1"}),
    ("update_project_access", {"project_id": "1"}),
])
async def test_project_setup_tool_auth_error_path_accepts_ctx(tool_name, kwargs):
    """When credentials are unavailable, each project-setup tool returns the
    auth-error dict via _get_auth_error_response(ctx) — the helper must accept ctx,
    not TypeError."""
    import basecamp_fastmcp as bc
    tool = getattr(bc, tool_name)
    provider = MagicMock(name='CredentialProvider')
    provider.credentials_for.return_value = None  # no creds -> client is None
    fake_ctx = MagicMock(name='Context')
    fake_ctx.request_context.lifespan_context = {"provider": provider}
    with patch('basecamp_fastmcp.token_storage') as mock_storage:
        mock_storage.is_token_expired.return_value = True
        result = await tool(fake_ctx, **kwargs)
    assert isinstance(result, dict)
    assert 'error' in result


@pytest.mark.asyncio
async def test_create_project_happy_path_returns_project():
    from basecamp_fastmcp import create_project

    fake_client = MagicMock(name='BasecampClient')
    fake_client.create_project.return_value = {
        'id': 12345, 'name': 'My project', 'description': None,
    }
    fake_ctx = MagicMock(name='Context')

    with patch('basecamp_fastmcp._get_basecamp_client', return_value=fake_client):
        result = await create_project(
            fake_ctx, name='My project', description=None,
        )

    assert result['status'] == 'success'
    assert result['project']['id'] == 12345
    assert result['project']['name'] == 'My project'
    fake_client.create_project.assert_called_once_with(
        name='My project', description=None,
    )


@pytest.mark.asyncio
async def test_update_project_happy_path_returns_project():
    from basecamp_fastmcp import update_project

    fake_client = MagicMock(name='BasecampClient')
    fake_client.update_project.return_value = {
        'id': 12345, 'name': 'Same name', 'description': 'new desc',
    }
    fake_ctx = MagicMock(name='Context')

    with patch('basecamp_fastmcp._get_basecamp_client', return_value=fake_client):
        result = await update_project(
            fake_ctx, project_id='12345', description='new desc',
        )

    assert result['status'] == 'success'
    assert result['project']['description'] == 'new desc'
    fake_client.update_project.assert_called_once_with(
        '12345', name=None, description='new desc',
        admissions=None, schedule_attributes=None,
    )


@pytest.mark.asyncio
async def test_trash_project_happy_path_returns_success():
    from basecamp_fastmcp import trash_project

    fake_client = MagicMock(name='BasecampClient')
    fake_client.trash_project.return_value = True
    fake_ctx = MagicMock(name='Context')

    with patch('basecamp_fastmcp._get_basecamp_client', return_value=fake_client):
        result = await trash_project(fake_ctx, project_id='12345')

    assert result['status'] == 'success'
    assert '12345' in result['message']
    fake_client.trash_project.assert_called_once_with('12345')


@pytest.mark.asyncio
async def test_update_project_access_grant_and_revoke():
    from basecamp_fastmcp import update_project_access

    fake_client = MagicMock(name='BasecampClient')
    fake_client.update_project_access.return_value = {
        'granted': [{'id': 99, 'name': 'Alice'}],
        'revoked': [{'id': 42, 'name': 'Bob'}],
    }
    fake_ctx = MagicMock(name='Context')

    with patch('basecamp_fastmcp._get_basecamp_client', return_value=fake_client):
        result = await update_project_access(
            fake_ctx,
            project_id='12345',
            grant=[99],
            revoke=[42],
        )

    assert result['status'] == 'success'
    assert result['access']['granted'][0]['id'] == 99
    assert result['access']['revoked'][0]['id'] == 42
    fake_client.update_project_access.assert_called_once_with(
        '12345', grant=[99], revoke=[42], create=None,
    )


@pytest.mark.asyncio
async def test_update_project_access_create_invites_new_user():
    from basecamp_fastmcp import update_project_access

    fake_client = MagicMock(name='BasecampClient')
    fake_client.update_project_access.return_value = {
        'granted': [{'id': 1001, 'name': 'New person'}],
        'revoked': [],
    }
    fake_ctx = MagicMock(name='Context')
    new_user = {
        'name': 'New person',
        'email_address': 'new@example.com',
        'title': 'Engineer',
    }

    with patch('basecamp_fastmcp._get_basecamp_client', return_value=fake_client):
        result = await update_project_access(
            fake_ctx,
            project_id='12345',
            create=[new_user],
        )

    assert result['status'] == 'success'
    assert result['access']['granted'][0]['name'] == 'New person'
    fake_client.update_project_access.assert_called_once_with(
        '12345', grant=None, revoke=None, create=[new_user],
    )


@pytest.mark.asyncio
async def test_update_project_propagates_client_errors():
    """Errors from the BC client (e.g. 404 not found) must come back as
    {'error': 'Execution error', 'message': ...}, matching the existing
    pattern in get_project / create_todo."""
    from basecamp_fastmcp import update_project

    fake_client = MagicMock(name='BasecampClient')
    fake_client.update_project.side_effect = Exception(
        'Failed to update project: 404 - Not Found',
    )
    fake_ctx = MagicMock(name='Context')

    with patch('basecamp_fastmcp._get_basecamp_client', return_value=fake_client):
        result = await update_project(
            fake_ctx, project_id='99999', name='nope',
        )

    assert result.get('error') == 'Execution error'
    assert '404' in result['message']
