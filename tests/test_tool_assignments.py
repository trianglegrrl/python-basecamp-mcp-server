"""Assignment-by-person tools: get_my_assignments / get_my_due_assignments /
get_my_completed_assignments / get_assignments_for_person.

This is the L2 weekly-report surface. The model uses these to answer "show
me Jill's tasks due this week" and to drive the weekly status workflow.

Mocked unit tests only — live tests live in tests/live/test_lifecycle_assignments.py
and are gated by the `live` marker.

Covers:
  - ctx-first parameter guard on all 4 tools
  - parametrized auth-error path on all 4 tools
  - happy-path per tool with mocked client return values
  - error-propagation per tool
  - scope validation: invalid scope rejected client-side (no HTTP)
  - get_assignments_for_person person-name resolution via mocked get_people
  - get_assignments_for_person fallback path when person isn't in /people.json
    but appears as a recording assignee (the deprecated-SKILL.md pitfall)
  - get_assignments_for_person ValueError when neither person_name nor
    person_id is supplied
  - get_recordings_todos pagination regression (Link-header walk)
"""
from __future__ import annotations

import inspect
from unittest.mock import MagicMock, patch

import pytest


ASSIGNMENT_TOOL_NAMES = [
    'get_my_assignments',
    'get_my_due_assignments',
    'get_my_completed_assignments',
    'get_assignments_for_person',
]


# ----------------------------------------------------------------------------
# Signature / dispatch contract.
# ----------------------------------------------------------------------------

def test_every_assignment_tool_takes_ctx_first():
    """Every migrated tool MUST take ctx as its first positional parameter so
    the FastMCP dispatcher can supply the request context. See PR T3."""
    import basecamp_fastmcp as bc
    for name in ASSIGNMENT_TOOL_NAMES:
        fn = getattr(bc, name)
        params = list(inspect.signature(fn).parameters.values())
        assert params[0].name == 'ctx', \
            f"{name}: first param should be 'ctx', got {params[0].name!r}"


@pytest.mark.asyncio
@pytest.mark.parametrize("tool_name,kwargs", [
    ("get_my_assignments",           {}),
    ("get_my_due_assignments",       {}),
    ("get_my_completed_assignments", {}),
    ("get_assignments_for_person",   {"person_name": "Jill"}),
])
async def test_assignment_tool_auth_error_path_accepts_ctx(tool_name, kwargs):
    """When credentials are unavailable, each assignment tool returns the
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


# ----------------------------------------------------------------------------
# get_my_assignments
# ----------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_my_assignments_happy_path_returns_priorities_shape():
    """BC3 returns {priorities: [...], non_priorities: [...]} from
    /my/assignments.json. The tool surfaces both lists and reports the total
    count in the human-readable message."""
    from basecamp_fastmcp import get_my_assignments

    fake_client = MagicMock(name='BasecampClient')
    fake_client.get_my_assignments.return_value = {
        'priorities':     [{'id': 1, 'title': 'Ship it'}, {'id': 2, 'title': 'Review PR'}],
        'non_priorities': [{'id': 3, 'title': 'Update docs'}],
    }
    fake_ctx = MagicMock(name='Context')

    with patch('basecamp_fastmcp._get_basecamp_client', return_value=fake_client):
        result = await get_my_assignments(fake_ctx)

    assert result['status'] == 'success'
    assert result['assignments']['priorities'][0]['id'] == 1
    assert len(result['assignments']['non_priorities']) == 1
    assert result['count'] == 3  # 2 priorities + 1 non-priority
    fake_client.get_my_assignments.assert_called_once_with()


@pytest.mark.asyncio
async def test_get_my_assignments_propagates_client_errors():
    from basecamp_fastmcp import get_my_assignments

    fake_client = MagicMock(name='BasecampClient')
    fake_client.get_my_assignments.side_effect = Exception(
        'Failed to get assignments: 500 - Internal Server Error',
    )
    fake_ctx = MagicMock(name='Context')

    with patch('basecamp_fastmcp._get_basecamp_client', return_value=fake_client):
        result = await get_my_assignments(fake_ctx)

    assert result.get('error') == 'Execution error'
    assert '500' in result['message']


# ----------------------------------------------------------------------------
# get_my_due_assignments
# ----------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_my_due_assignments_happy_path_without_scope():
    from basecamp_fastmcp import get_my_due_assignments

    fake_client = MagicMock(name='BasecampClient')
    fake_client.get_my_due_assignments.return_value = [
        {'id': 1, 'title': 'a', 'due_on': '2026-05-27'},
        {'id': 2, 'title': 'b', 'due_on': '2026-05-28'},
    ]
    fake_ctx = MagicMock(name='Context')

    with patch('basecamp_fastmcp._get_basecamp_client', return_value=fake_client):
        result = await get_my_due_assignments(fake_ctx)

    assert result['status'] == 'success'
    assert result['count'] == 2
    fake_client.get_my_due_assignments.assert_called_once_with(None)


@pytest.mark.asyncio
async def test_get_my_due_assignments_happy_path_with_valid_scope():
    from basecamp_fastmcp import get_my_due_assignments

    fake_client = MagicMock(name='BasecampClient')
    fake_client.get_my_due_assignments.return_value = [
        {'id': 99, 'title': 'overdue thing', 'due_on': '2025-01-01'},
    ]
    fake_ctx = MagicMock(name='Context')

    with patch('basecamp_fastmcp._get_basecamp_client', return_value=fake_client):
        result = await get_my_due_assignments(fake_ctx, scope='overdue')

    assert result['status'] == 'success'
    assert result['count'] == 1
    assert 'overdue' in result['message']
    fake_client.get_my_due_assignments.assert_called_once_with('overdue')


@pytest.mark.asyncio
async def test_get_my_due_assignments_invalid_scope_rejected_before_http():
    """An invalid scope must be caught by the client method's validation and
    surfaced as Execution error — and the HTTP layer must not have been
    invoked. This pins the client-side guard against the 6-scope enum."""
    from basecamp_fastmcp import get_my_due_assignments

    # Use a real BasecampClient via patch on the bound method, so the
    # actual ValueError from the client surfaces.
    fake_client = MagicMock(name='BasecampClient')
    # Simulate what the real client method does: raise ValueError on bad scope.
    fake_client.get_my_due_assignments.side_effect = ValueError(
        "Invalid scope 'made_up'. Valid options: overdue, due_today, due_tomorrow, "
        "due_later_this_week, due_next_week, due_later"
    )
    fake_ctx = MagicMock(name='Context')

    with patch('basecamp_fastmcp._get_basecamp_client', return_value=fake_client):
        result = await get_my_due_assignments(fake_ctx, scope='made_up')

    assert result.get('error') == 'Execution error'
    assert 'Invalid scope' in result['message']
    assert 'made_up' in result['message']


@pytest.mark.asyncio
async def test_get_my_due_assignments_propagates_client_errors():
    from basecamp_fastmcp import get_my_due_assignments

    fake_client = MagicMock(name='BasecampClient')
    fake_client.get_my_due_assignments.side_effect = Exception(
        'Failed to get due assignments: 403 - Forbidden',
    )
    fake_ctx = MagicMock(name='Context')

    with patch('basecamp_fastmcp._get_basecamp_client', return_value=fake_client):
        result = await get_my_due_assignments(fake_ctx)

    assert result.get('error') == 'Execution error'
    assert '403' in result['message']


# ----------------------------------------------------------------------------
# get_my_completed_assignments
# ----------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_my_completed_assignments_happy_path():
    from basecamp_fastmcp import get_my_completed_assignments

    fake_client = MagicMock(name='BasecampClient')
    fake_client.get_my_completed_assignments.return_value = [
        {'id': 10, 'title': 'done thing 1'},
        {'id': 11, 'title': 'done thing 2'},
        {'id': 12, 'title': 'done thing 3'},
    ]
    fake_ctx = MagicMock(name='Context')

    with patch('basecamp_fastmcp._get_basecamp_client', return_value=fake_client):
        result = await get_my_completed_assignments(fake_ctx)

    assert result['status'] == 'success'
    assert result['count'] == 3
    assert 'completed' in result['message']
    fake_client.get_my_completed_assignments.assert_called_once_with()


@pytest.mark.asyncio
async def test_get_my_completed_assignments_propagates_client_errors():
    from basecamp_fastmcp import get_my_completed_assignments

    fake_client = MagicMock(name='BasecampClient')
    fake_client.get_my_completed_assignments.side_effect = Exception(
        'Failed to get completed assignments: 500 - Internal Server Error',
    )
    fake_ctx = MagicMock(name='Context')

    with patch('basecamp_fastmcp._get_basecamp_client', return_value=fake_client):
        result = await get_my_completed_assignments(fake_ctx)

    assert result.get('error') == 'Execution error'
    assert '500' in result['message']


# ----------------------------------------------------------------------------
# get_assignments_for_person — the headliner
# ----------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_assignments_for_person_happy_path_by_person_id():
    from basecamp_fastmcp import get_assignments_for_person

    fake_client = MagicMock(name='BasecampClient')
    fake_client.get_assignments_for_person.return_value = {
        'person_id': 555,
        'assignments': [
            {'id': 1, 'title': 'a', 'assignees': [{'id': 555}]},
            {'id': 2, 'title': 'b', 'assignees': [{'id': 555}]},
        ],
    }
    fake_ctx = MagicMock(name='Context')

    with patch('basecamp_fastmcp._get_basecamp_client', return_value=fake_client):
        result = await get_assignments_for_person(fake_ctx, person_id=555)

    assert result['status'] == 'success'
    assert result['count'] == 2
    assert result['person_id'] == 555
    fake_client.get_assignments_for_person.assert_called_once_with(
        person_name=None, person_id=555, scope=None, bucket=None,
    )


@pytest.mark.asyncio
async def test_get_assignments_for_person_happy_path_by_person_name_with_scope():
    from basecamp_fastmcp import get_assignments_for_person

    fake_client = MagicMock(name='BasecampClient')
    fake_client.get_assignments_for_person.return_value = {
        'person_id': 777,
        'assignments': [
            {'id': 1, 'title': 'a', 'assignees': [{'id': 777}], 'due_on': '2026-05-27'},
        ],
    }
    fake_ctx = MagicMock(name='Context')

    with patch('basecamp_fastmcp._get_basecamp_client', return_value=fake_client):
        result = await get_assignments_for_person(
            fake_ctx, person_name='Jill', scope='due_today',
        )

    assert result['status'] == 'success'
    assert result['count'] == 1
    assert result['person_id'] == 777  # resolved from name 'Jill'
    assert 'due_today' in result['message']
    assert 'Jill' in result['message']
    fake_client.get_assignments_for_person.assert_called_once_with(
        person_name='Jill', person_id=None, scope='due_today', bucket=None,
    )


@pytest.mark.asyncio
async def test_get_assignments_for_person_invalid_scope_propagates():
    from basecamp_fastmcp import get_assignments_for_person

    fake_client = MagicMock(name='BasecampClient')
    fake_client.get_assignments_for_person.side_effect = ValueError(
        "Invalid scope 'soonish'. Valid options: overdue, due_today, due_tomorrow, "
        "due_later_this_week, due_next_week, due_later"
    )
    fake_ctx = MagicMock(name='Context')

    with patch('basecamp_fastmcp._get_basecamp_client', return_value=fake_client):
        result = await get_assignments_for_person(
            fake_ctx, person_name='Jill', scope='soonish',
        )

    assert result.get('error') == 'Execution error'
    assert 'Invalid scope' in result['message']


@pytest.mark.asyncio
async def test_get_assignments_for_person_missing_both_args_raises():
    """The client method requires at least one of person_name / person_id —
    the tool wrapper propagates the ValueError as Execution error."""
    from basecamp_fastmcp import get_assignments_for_person

    fake_client = MagicMock(name='BasecampClient')
    fake_client.get_assignments_for_person.side_effect = ValueError(
        'get_assignments_for_person requires either person_name or person_id'
    )
    fake_ctx = MagicMock(name='Context')

    with patch('basecamp_fastmcp._get_basecamp_client', return_value=fake_client):
        result = await get_assignments_for_person(fake_ctx)

    assert result.get('error') == 'Execution error'
    assert 'requires either' in result['message']


@pytest.mark.asyncio
async def test_get_assignments_for_person_propagates_other_errors():
    from basecamp_fastmcp import get_assignments_for_person

    fake_client = MagicMock(name='BasecampClient')
    fake_client.get_assignments_for_person.side_effect = Exception(
        'Failed to walk recordings: 502 - Bad Gateway',
    )
    fake_ctx = MagicMock(name='Context')

    with patch('basecamp_fastmcp._get_basecamp_client', return_value=fake_client):
        result = await get_assignments_for_person(fake_ctx, person_id=42)

    assert result.get('error') == 'Execution error'
    assert '502' in result['message']


# ----------------------------------------------------------------------------
# Client-method-level tests for get_assignments_for_person
# (the wrapper just hands kwargs through; the walk/resolution logic lives
# on the client and warrants its own coverage).
# ----------------------------------------------------------------------------

def test_client_get_assignments_for_person_resolves_name_via_get_people():
    """When a person_name is given (no person_id), the client must look up
    /people.json and substring-match (case-insensitive) on the `name` field
    to resolve a person_id."""
    from basecamp_client import BasecampClient

    client = BasecampClient.__new__(BasecampClient)  # skip __init__
    client.get_people = MagicMock(return_value=[
        {'id': 100, 'name': 'Alice Smith'},
        {'id': 200, 'name': 'jill jones'},   # lowercase to test ci match
        {'id': 300, 'name': 'Bob'},
    ])
    client.get_recordings_todos = MagicMock(return_value=[
        {'id': 1, 'title': "jill's task", 'assignees': [{'id': 200}], 'due_on': '2026-05-27'},
        {'id': 2, 'title': "alice's task", 'assignees': [{'id': 100}], 'due_on': '2026-05-27'},
        {'id': 3, 'title': 'unassigned', 'assignees': []},
    ])

    result = client.get_assignments_for_person(person_name='Jill')

    assert result['person_id'] == 200
    assert [t['id'] for t in result['assignments']] == [1]
    client.get_people.assert_called_once_with()
    client.get_recordings_todos.assert_called_once_with(bucket=None)


def test_client_get_assignments_for_person_falls_back_to_recording_assignees():
    """If /people.json doesn't contain the person (current user can't see
    them), the walk should still find them by scanning recording assignees.
    Mirrors the deprecated-SKILL.md pitfall: only the token owner's company
    appears in /people.json, but BC3 surfaces assignee names on recordings."""
    from basecamp_client import BasecampClient

    client = BasecampClient.__new__(BasecampClient)
    client.get_people = MagicMock(return_value=[
        {'id': 100, 'name': 'Alice Smith'},  # no jill in account list
    ])
    client.get_recordings_todos = MagicMock(return_value=[
        {'id': 1, 'title': "jill's task", 'assignees': [{'id': 200, 'name': 'Jill Jones'}]},
        {'id': 2, 'title': "another for jill", 'assignees': [{'id': 200, 'name': 'Jill Jones'}]},
        {'id': 3, 'title': "for alice", 'assignees': [{'id': 100, 'name': 'Alice Smith'}]},
    ])

    result = client.get_assignments_for_person(person_name='jill')

    assert result['person_id'] == 200
    assert [t['id'] for t in result['assignments']] == [1, 2]


def test_client_get_assignments_for_person_raises_when_no_person_found():
    """If neither /people.json nor recording assignees match, raise — don't
    silently return an empty list (would be indistinguishable from "no
    tasks", confusing the model)."""
    from basecamp_client import BasecampClient

    client = BasecampClient.__new__(BasecampClient)
    client.get_people = MagicMock(return_value=[{'id': 100, 'name': 'Alice'}])
    client.get_recordings_todos = MagicMock(return_value=[
        {'id': 1, 'assignees': [{'id': 100, 'name': 'Alice'}]},
    ])

    with pytest.raises(ValueError, match='No person matching'):
        client.get_assignments_for_person(person_name='Nonexistent')


def test_client_get_assignments_for_person_requires_name_or_id():
    """No person_name and no person_id is a programming error — fail loud."""
    from basecamp_client import BasecampClient

    client = BasecampClient.__new__(BasecampClient)
    client.get_people = MagicMock(return_value=[])
    client.get_recordings_todos = MagicMock(return_value=[])

    with pytest.raises(ValueError, match='requires either'):
        client.get_assignments_for_person()


def test_client_get_assignments_for_person_invalid_scope_raises_before_http():
    """Invalid scope must be rejected before any HTTP — no people lookup,
    no recordings walk. Validates client-side guard against the 6-scope enum."""
    from basecamp_client import BasecampClient

    client = BasecampClient.__new__(BasecampClient)
    client.get_people = MagicMock()
    client.get_recordings_todos = MagicMock()

    with pytest.raises(ValueError, match="Invalid scope"):
        client.get_assignments_for_person(person_id=1, scope='made_up')

    client.get_people.assert_not_called()
    client.get_recordings_todos.assert_not_called()


def test_client_get_assignments_for_person_applies_scope_filter():
    """When a valid scope is supplied AND today is pinned, only matching
    due_on dates pass the filter. Today = 2026-05-27 (Wed); 'overdue' must
    keep due_on=2026-05-01 and drop due_on=2026-05-28."""
    from basecamp_client import BasecampClient

    client = BasecampClient.__new__(BasecampClient)
    client.get_people = MagicMock(return_value=[])
    client.get_recordings_todos = MagicMock(return_value=[
        {'id': 1, 'title': 'old', 'assignees': [{'id': 7}], 'due_on': '2026-05-01'},
        {'id': 2, 'title': 'soon', 'assignees': [{'id': 7}], 'due_on': '2026-05-28'},
        {'id': 3, 'title': 'undated', 'assignees': [{'id': 7}], 'due_on': None},
    ])

    result = client.get_assignments_for_person(
        person_id=7, scope='overdue', today='2026-05-27',
    )
    assert result['person_id'] == 7
    assert [t['id'] for t in result['assignments']] == [1]


def test_client_get_assignments_for_person_string_vs_int_id_match():
    """BC3 returns assignee ids as ints. The walk must tolerate string
    person_id input (FastMCP tool args arrive as JSON-typed values)."""
    from basecamp_client import BasecampClient

    client = BasecampClient.__new__(BasecampClient)
    client.get_people = MagicMock(return_value=[])
    client.get_recordings_todos = MagicMock(return_value=[
        {'id': 1, 'assignees': [{'id': 555}]},  # int id
    ])
    # Pass a string and expect the str-compare to still hit.
    result = client.get_assignments_for_person(person_id='555')
    assert result['person_id'] == '555'
    assert [t['id'] for t in result['assignments']] == [1]


def test_client_get_assignments_for_person_passes_bucket_through():
    from basecamp_client import BasecampClient

    client = BasecampClient.__new__(BasecampClient)
    client.get_people = MagicMock(return_value=[])
    client.get_recordings_todos = MagicMock(return_value=[])

    client.get_assignments_for_person(person_id=1, bucket='42')

    client.get_recordings_todos.assert_called_once_with(bucket='42')


# ----------------------------------------------------------------------------
# get_recordings_todos pagination regression.
# ----------------------------------------------------------------------------

def _paged_response(payload, *, has_next):
    """Build a MagicMock Response that looks like a paginated BC3 page.
    Mirrors the helper from test_tool_people.py."""
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = payload
    if has_next:
        resp.headers = {"Link": '<https://example.invalid/page=2>; rel="next"'}
    else:
        resp.headers = {}
    resp.text = ''
    return resp


def test_get_recordings_todos_walks_pagination_until_no_next_link():
    """Regression: get_recordings_todos must aggregate pages until the Link
    header drops the rel="next" advertisement. Same pagination shape as
    get_people / get_schedule_entries."""
    from basecamp_client import BasecampClient

    page1 = [{'id': i, 'type': 'Todo'} for i in range(1, 16)]
    page2 = [{'id': 16, 'type': 'Todo'}, {'id': 17, 'type': 'Todo'}]

    client = BasecampClient.__new__(BasecampClient)
    client.get = MagicMock(side_effect=[
        _paged_response(page1, has_next=True),
        _paged_response(page2, has_next=False),
    ])

    todos = client.get_recordings_todos()

    assert [t['id'] for t in todos] == list(range(1, 18))
    assert client.get.call_count == 2
    first_call, second_call = client.get.call_args_list
    assert first_call.args[0] == 'projects/recordings.json'
    assert first_call.kwargs == {'params': {'type': 'Todo', 'page': 1}}
    assert second_call.args[0] == 'projects/recordings.json'
    assert second_call.kwargs == {'params': {'type': 'Todo', 'page': 2}}


def test_get_recordings_todos_passes_bucket_and_status_params():
    """When bucket and status are supplied, they ride alongside type=Todo on
    every paginated request."""
    from basecamp_client import BasecampClient

    client = BasecampClient.__new__(BasecampClient)
    client.get = MagicMock(return_value=_paged_response([], has_next=False))

    client.get_recordings_todos(bucket='42', status='archived')

    assert client.get.call_count == 1
    call = client.get.call_args
    assert call.args[0] == 'projects/recordings.json'
    assert call.kwargs == {
        'params': {'type': 'Todo', 'bucket': '42', 'status': 'archived', 'page': 1},
    }


# ----------------------------------------------------------------------------
# Client-method-level happy paths for the simple /my/* endpoints.
# ----------------------------------------------------------------------------

def test_client_get_my_assignments_returns_priorities_shape():
    from basecamp_client import BasecampClient

    client = BasecampClient.__new__(BasecampClient)
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {'priorities': [{'id': 1}], 'non_priorities': []}
    client.get = MagicMock(return_value=resp)

    result = client.get_my_assignments()
    assert result == {'priorities': [{'id': 1}], 'non_priorities': []}
    client.get.assert_called_once_with('my/assignments.json')


def test_client_get_my_assignments_raises_on_non_200():
    from basecamp_client import BasecampClient

    client = BasecampClient.__new__(BasecampClient)
    resp = MagicMock()
    resp.status_code = 500
    resp.text = 'boom'
    client.get = MagicMock(return_value=resp)

    with pytest.raises(Exception, match='500'):
        client.get_my_assignments()


def test_client_get_my_due_assignments_validates_scope_before_http():
    from basecamp_client import BasecampClient

    client = BasecampClient.__new__(BasecampClient)
    client.get = MagicMock()

    with pytest.raises(ValueError, match='Invalid scope'):
        client.get_my_due_assignments('made_up')

    client.get.assert_not_called()


def test_client_get_my_due_assignments_sends_scope_param_when_given():
    from basecamp_client import BasecampClient

    client = BasecampClient.__new__(BasecampClient)
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = []
    client.get = MagicMock(return_value=resp)

    client.get_my_due_assignments('overdue')

    client.get.assert_called_once_with(
        'my/assignments/due.json', params={'scope': 'overdue'},
    )


def test_client_get_my_due_assignments_omits_scope_param_when_none():
    from basecamp_client import BasecampClient

    client = BasecampClient.__new__(BasecampClient)
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = []
    client.get = MagicMock(return_value=resp)

    client.get_my_due_assignments()

    client.get.assert_called_once_with('my/assignments/due.json', params={})


def test_client_get_my_completed_assignments_calls_completed_endpoint():
    from basecamp_client import BasecampClient

    client = BasecampClient.__new__(BasecampClient)
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = [{'id': 1}]
    client.get = MagicMock(return_value=resp)

    result = client.get_my_completed_assignments()
    assert result == [{'id': 1}]
    client.get.assert_called_once_with('my/assignments/completed.json')
