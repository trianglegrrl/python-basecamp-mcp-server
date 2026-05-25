"""People + my-profile tools: get_my_profile / get_people / get_project_people.

Ports the identity / membership surface from the deprecated Node MCP repo to
the Python upstream. Mocked unit tests only — live tests live in
tests/live/test_lifecycle_people.py and are gated by the `live` marker.

Includes a pagination regression test for the new paginated client methods
(`get_people`, `get_project_people`) which loop via the BC3 `Link` header —
the same shape A1.2 added to `get_schedule_entries`.
"""
import inspect
from unittest.mock import MagicMock, patch

import pytest


PEOPLE_TOOL_NAMES = [
    'get_my_profile',
    'get_people',
    'get_project_people',
]


def test_every_people_tool_takes_ctx_first():
    """Every migrated tool MUST take ctx as its first positional parameter so
    the FastMCP dispatcher can supply the request context. See PR T3."""
    import basecamp_fastmcp as bc
    for name in PEOPLE_TOOL_NAMES:
        fn = getattr(bc, name)
        params = list(inspect.signature(fn).parameters.values())
        assert params[0].name == 'ctx', \
            f"{name}: first param should be 'ctx', got {params[0].name!r}"


@pytest.mark.asyncio
@pytest.mark.parametrize("tool_name,kwargs", [
    ("get_my_profile",     {}),
    ("get_people",         {}),
    ("get_project_people", {"project_id": "1"}),
])
async def test_people_tool_auth_error_path_accepts_ctx(tool_name, kwargs):
    """When credentials are unavailable, each people tool returns the
    auth-error dict via _get_auth_error_response(ctx) — the helper must
    accept ctx, not TypeError."""
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
async def test_get_my_profile_happy_path_returns_profile():
    from basecamp_fastmcp import get_my_profile

    fake_client = MagicMock(name='BasecampClient')
    fake_client.get_my_profile.return_value = {
        'id': 1234567,
        'name': 'Token Owner',
        'email_address': 'owner@example.com',
    }
    fake_ctx = MagicMock(name='Context')

    with patch('basecamp_fastmcp._get_basecamp_client', return_value=fake_client):
        result = await get_my_profile(fake_ctx)

    assert result['status'] == 'success'
    assert result['profile']['id'] == 1234567
    assert result['profile']['email_address'] == 'owner@example.com'
    fake_client.get_my_profile.assert_called_once_with()


@pytest.mark.asyncio
async def test_get_my_profile_propagates_client_errors():
    from basecamp_fastmcp import get_my_profile

    fake_client = MagicMock(name='BasecampClient')
    fake_client.get_my_profile.side_effect = Exception(
        'Failed to get profile: 500 - Internal Server Error',
    )
    fake_ctx = MagicMock(name='Context')

    with patch('basecamp_fastmcp._get_basecamp_client', return_value=fake_client):
        result = await get_my_profile(fake_ctx)

    assert result.get('error') == 'Execution error'
    assert '500' in result['message']


@pytest.mark.asyncio
async def test_get_people_happy_path_returns_list():
    from basecamp_fastmcp import get_people

    fake_client = MagicMock(name='BasecampClient')
    fake_client.get_people.return_value = [
        {'id': 1, 'name': 'Alice', 'email_address': 'alice@example.com'},
        {'id': 2, 'name': 'Bob', 'email_address': 'bob@example.com'},
    ]
    fake_ctx = MagicMock(name='Context')

    with patch('basecamp_fastmcp._get_basecamp_client', return_value=fake_client):
        result = await get_people(fake_ctx)

    assert result['status'] == 'success'
    assert len(result['people']) == 2
    assert result['count'] == 2
    fake_client.get_people.assert_called_once_with()


@pytest.mark.asyncio
async def test_get_people_propagates_client_errors():
    from basecamp_fastmcp import get_people

    fake_client = MagicMock(name='BasecampClient')
    fake_client.get_people.side_effect = Exception(
        'Failed to get people: 403 - Forbidden',
    )
    fake_ctx = MagicMock(name='Context')

    with patch('basecamp_fastmcp._get_basecamp_client', return_value=fake_client):
        result = await get_people(fake_ctx)

    assert result.get('error') == 'Execution error'
    assert '403' in result['message']


@pytest.mark.asyncio
async def test_get_project_people_happy_path_returns_list():
    from basecamp_fastmcp import get_project_people

    fake_client = MagicMock(name='BasecampClient')
    fake_client.get_project_people.return_value = [
        {'id': 1, 'name': 'Alice', 'email_address': 'alice@example.com'},
    ]
    fake_ctx = MagicMock(name='Context')

    with patch('basecamp_fastmcp._get_basecamp_client', return_value=fake_client):
        result = await get_project_people(fake_ctx, project_id='12345')

    assert result['status'] == 'success'
    assert len(result['people']) == 1
    assert result['count'] == 1
    fake_client.get_project_people.assert_called_once_with('12345')


@pytest.mark.asyncio
async def test_get_project_people_propagates_client_errors():
    from basecamp_fastmcp import get_project_people

    fake_client = MagicMock(name='BasecampClient')
    fake_client.get_project_people.side_effect = Exception(
        'Failed to get project people: 404 - Project not found',
    )
    fake_ctx = MagicMock(name='Context')

    with patch('basecamp_fastmcp._get_basecamp_client', return_value=fake_client):
        result = await get_project_people(fake_ctx, project_id='99999')

    assert result.get('error') == 'Execution error'
    assert '404' in result['message']


def _paged_response(payload, *, has_next):
    """Build a MagicMock Response that looks like a paginated BC3 page.

    `has_next=True` adds a `Link: <...>; rel="next"` header; the value of
    the URL doesn't matter for the client method since it walks pages by
    incrementing the `page` param, not by chasing the Link target."""
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = payload
    if has_next:
        resp.headers = {"Link": '<https://example.invalid/page=2>; rel="next"'}
    else:
        resp.headers = {}
    resp.text = ''
    return resp


def test_get_people_walks_pagination_until_no_next_link():
    """Regression: get_people must aggregate pages until the Link header no
    longer advertises rel="next". A1.2 added this exact loop to
    get_schedule_entries; the pre-existing get_people lacked it and silently
    truncated at 15 entries on large accounts. This test pins the new
    paginated behaviour."""
    from basecamp_client import BasecampClient

    page1 = [{'id': i} for i in range(1, 16)]
    page2 = [{'id': 16}, {'id': 17}]

    client = BasecampClient.__new__(BasecampClient)  # skip __init__
    client.get = MagicMock(side_effect=[
        _paged_response(page1, has_next=True),
        _paged_response(page2, has_next=False),
    ])

    people = client.get_people()

    assert [p['id'] for p in people] == list(range(1, 18))
    assert client.get.call_count == 2
    # Both calls hit the same endpoint with a page param.
    first_call, second_call = client.get.call_args_list
    assert first_call.args[0] == 'people.json'
    assert first_call.kwargs == {'params': {'page': 1}}
    assert second_call.args[0] == 'people.json'
    assert second_call.kwargs == {'params': {'page': 2}}


def test_get_project_people_walks_pagination_until_no_next_link():
    """Same regression as get_people, but for the per-project endpoint."""
    from basecamp_client import BasecampClient

    page1 = [{'id': i} for i in range(100, 115)]
    page2 = [{'id': 115}]

    client = BasecampClient.__new__(BasecampClient)
    client.get = MagicMock(side_effect=[
        _paged_response(page1, has_next=True),
        _paged_response(page2, has_next=False),
    ])

    people = client.get_project_people('42')

    assert [p['id'] for p in people] == list(range(100, 116))
    assert client.get.call_count == 2
    first_call, second_call = client.get.call_args_list
    assert first_call.args[0] == 'projects/42/people.json'
    assert first_call.kwargs == {'params': {'page': 1}}
    assert second_call.args[0] == 'projects/42/people.json'
    assert second_call.kwargs == {'params': {'page': 2}}
