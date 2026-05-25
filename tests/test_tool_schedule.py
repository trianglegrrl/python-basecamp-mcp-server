"""Schedule tools: get_schedule / get_schedule_entries / get_schedule_entry /
create_schedule_entry / update_schedule_entry.

Ports the schedule (calendar) surface from the deprecated Node MCP repo to
the Python upstream. Mocked unit tests only — live tests live in
tests/live/test_lifecycle_schedule.py and are gated by the `live` marker.
"""
import inspect
from unittest.mock import MagicMock, patch

import pytest


SCHEDULE_TOOL_NAMES = [
    'get_schedule',
    'get_schedule_entries',
    'get_schedule_entry',
    'create_schedule_entry',
    'update_schedule_entry',
]


def test_every_schedule_tool_takes_ctx_first():
    """Every migrated tool MUST take ctx as its first positional parameter so
    the FastMCP dispatcher can supply the request context. See PR T3."""
    import basecamp_fastmcp as bc
    for name in SCHEDULE_TOOL_NAMES:
        fn = getattr(bc, name)
        params = list(inspect.signature(fn).parameters.values())
        assert params[0].name == 'ctx', \
            f"{name}: first param should be 'ctx', got {params[0].name!r}"


@pytest.mark.asyncio
@pytest.mark.parametrize("tool_name,kwargs", [
    ("get_schedule",          {"project_id": "1"}),
    ("get_schedule_entries",  {"project_id": "1"}),
    ("get_schedule_entry",    {"project_id": "1", "entry_id": "99"}),
    ("create_schedule_entry", {
        "project_id": "1",
        "summary": "x",
        "starts_at": "2026-06-01T10:00:00Z",
        "ends_at": "2026-06-01T11:00:00Z",
    }),
    ("update_schedule_entry", {"project_id": "1", "entry_id": "99"}),
])
async def test_schedule_tool_auth_error_path_accepts_ctx(tool_name, kwargs):
    """When credentials are unavailable, each schedule tool returns the
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
async def test_get_schedule_happy_path_returns_schedule():
    from basecamp_fastmcp import get_schedule

    fake_client = MagicMock(name='BasecampClient')
    fake_client.get_schedule.return_value = {
        'id': 555, 'title': 'Schedule',
        'entries_url': 'https://3.basecampapi.com/.../entries.json',
    }
    fake_ctx = MagicMock(name='Context')

    with patch('basecamp_fastmcp._get_basecamp_client', return_value=fake_client):
        result = await get_schedule(fake_ctx, project_id='12345')

    assert result['status'] == 'success'
    assert result['schedule']['id'] == 555
    fake_client.get_schedule.assert_called_once_with('12345')


@pytest.mark.asyncio
async def test_get_schedule_entries_happy_path_returns_list():
    from basecamp_fastmcp import get_schedule_entries

    fake_client = MagicMock(name='BasecampClient')
    fake_client.get_schedule_entries.return_value = [
        {'id': 1, 'summary': 'Standup'},
        {'id': 2, 'summary': 'Demo'},
    ]
    fake_ctx = MagicMock(name='Context')

    with patch('basecamp_fastmcp._get_basecamp_client', return_value=fake_client):
        result = await get_schedule_entries(fake_ctx, project_id='12345')

    assert result['status'] == 'success'
    assert len(result['entries']) == 2
    assert result['count'] == 2
    fake_client.get_schedule_entries.assert_called_once_with('12345', schedule_id=None)


@pytest.mark.asyncio
async def test_get_schedule_entry_happy_path_returns_entry():
    from basecamp_fastmcp import get_schedule_entry

    fake_client = MagicMock(name='BasecampClient')
    fake_client.get_schedule_entry.return_value = {
        'id': 99, 'summary': 'Standup',
        'starts_at': '2026-06-01T10:00:00Z',
        'ends_at': '2026-06-01T11:00:00Z',
    }
    fake_ctx = MagicMock(name='Context')

    with patch('basecamp_fastmcp._get_basecamp_client', return_value=fake_client):
        result = await get_schedule_entry(
            fake_ctx, project_id='12345', entry_id='99',
        )

    assert result['status'] == 'success'
    assert result['entry']['id'] == 99
    fake_client.get_schedule_entry.assert_called_once_with('12345', '99')


@pytest.mark.asyncio
async def test_create_schedule_entry_happy_path_returns_entry():
    from basecamp_fastmcp import create_schedule_entry

    fake_client = MagicMock(name='BasecampClient')
    fake_client.create_schedule_entry.return_value = {
        'id': 999, 'summary': 'Demo',
        'starts_at': '2026-06-01T10:00:00Z',
        'ends_at': '2026-06-01T11:00:00Z',
    }
    fake_ctx = MagicMock(name='Context')

    with patch('basecamp_fastmcp._get_basecamp_client', return_value=fake_client):
        result = await create_schedule_entry(
            fake_ctx,
            project_id='12345',
            summary='Demo',
            starts_at='2026-06-01T10:00:00Z',
            ends_at='2026-06-01T11:00:00Z',
            description='<div>Quarterly demo</div>',
            participant_ids=[111, 222],
            all_day=False,
            notify=True,
        )

    assert result['status'] == 'success'
    assert result['entry']['id'] == 999
    fake_client.create_schedule_entry.assert_called_once_with(
        '12345',
        summary='Demo',
        starts_at='2026-06-01T10:00:00Z',
        ends_at='2026-06-01T11:00:00Z',
        description='<div>Quarterly demo</div>',
        participant_ids=[111, 222],
        all_day=False,
        notify=True,
    )


@pytest.mark.asyncio
async def test_update_schedule_entry_happy_path_returns_entry():
    from basecamp_fastmcp import update_schedule_entry

    fake_client = MagicMock(name='BasecampClient')
    fake_client.update_schedule_entry.return_value = {
        'id': 99, 'summary': 'Renamed',
        'starts_at': '2026-06-01T10:00:00Z',
        'ends_at': '2026-06-01T11:00:00Z',
    }
    fake_ctx = MagicMock(name='Context')

    with patch('basecamp_fastmcp._get_basecamp_client', return_value=fake_client):
        result = await update_schedule_entry(
            fake_ctx, project_id='12345', entry_id='99', summary='Renamed',
        )

    assert result['status'] == 'success'
    assert result['entry']['summary'] == 'Renamed'
    fake_client.update_schedule_entry.assert_called_once_with(
        '12345', '99',
        summary='Renamed', description=None,
        starts_at=None, ends_at=None,
        participant_ids=None, all_day=None, notify=None,
    )


@pytest.mark.asyncio
async def test_update_schedule_entry_propagates_client_errors():
    """Errors from the BC client (e.g. 404 not found) must come back as
    {'error': 'Execution error', 'message': ...}, matching the existing
    pattern in get_project / create_todo / update_project."""
    from basecamp_fastmcp import update_schedule_entry

    fake_client = MagicMock(name='BasecampClient')
    fake_client.update_schedule_entry.side_effect = Exception(
        'Failed to update schedule entry: 404 - Not Found',
    )
    fake_ctx = MagicMock(name='Context')

    with patch('basecamp_fastmcp._get_basecamp_client', return_value=fake_client):
        result = await update_schedule_entry(
            fake_ctx, project_id='12345', entry_id='99', summary='nope',
        )

    assert result.get('error') == 'Execution error'
    assert '404' in result['message']


@pytest.mark.asyncio
async def test_create_schedule_entry_propagates_client_errors():
    """Same error-propagation contract for create."""
    from basecamp_fastmcp import create_schedule_entry

    fake_client = MagicMock(name='BasecampClient')
    fake_client.create_schedule_entry.side_effect = Exception(
        'Failed to create schedule entry: 422 - Validation failed',
    )
    fake_ctx = MagicMock(name='Context')

    with patch('basecamp_fastmcp._get_basecamp_client', return_value=fake_client):
        result = await create_schedule_entry(
            fake_ctx, project_id='12345',
            summary='', starts_at='', ends_at='',
        )

    assert result.get('error') == 'Execution error'
    assert '422' in result['message']
