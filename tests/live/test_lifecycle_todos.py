"""Live BC3 lifecycle tests for todos. Marked @pytest.mark.live — default
suite (`pytest`) skips this file via pytest.ini. Run with `make test-live`
or `pytest tests/live/test_lifecycle_todos.py -m live`.

Reference: ~/projects/basecamp-mcp-server/src/test/live/todos.live.test.ts.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.live


@pytest.fixture(scope='module')
def todolist_id(live_client, sandbox_project_id, prefix, id_store):
    """Reuse the first existing todolist in the sandbox, OR create one
    (and record its id so the cleanup script trashes it)."""
    lists = live_client.get_todolists(sandbox_project_id)
    if lists:
        return str(lists[0]['id'])
    project = live_client.get_project(sandbox_project_id)
    todoset = next((d for d in project['dock'] if d['name'] == 'todoset'), None)
    assert todoset, 'Sandbox project has no todoset.'
    created = live_client.create_todolist(
        sandbox_project_id, todoset['id'], f'{prefix} sandbox list',
    )
    id_store(created['id'], sandbox_project_id, 'Todolist')
    return str(created['id'])


def test_create_todo_then_get_round_trips_content(live_client, sandbox_project_id, todolist_id, prefix, id_store):
    created = live_client.create_todo(
        sandbox_project_id, todolist_id,
        content=f'{prefix} create-then-trash',
        description='initial',
        due_on='2026-12-31',
    )
    id_store(created['id'], sandbox_project_id, 'Todo')
    fetched = live_client.get_todo(sandbox_project_id, created['id'])
    assert fetched['content'].startswith('[mcp-test-')
    assert fetched['due_on'] == '2026-12-31'


def test_update_todo_full_merge_preserves_description(
    live_client, sandbox_project_id, todolist_id, prefix, id_store,
):
    created = live_client.create_todo(
        sandbox_project_id, todolist_id, content=f'{prefix} update-test', description='initial',
    )
    id_store(created['id'], sandbox_project_id, 'Todo')
    live_client.update_todo(
        sandbox_project_id, created['id'], content=f'{prefix} updated content',
    )
    fetched = live_client.get_todo(sandbox_project_id, created['id'])
    assert 'updated content' in fetched['content']
    assert fetched['description'] == 'initial', 'merge should preserve description'


def test_complete_then_uncomplete_round_trip(
    live_client, sandbox_project_id, todolist_id, prefix, id_store,
):
    created = live_client.create_todo(sandbox_project_id, todolist_id, content=f'{prefix} complete-test')
    id_store(created['id'], sandbox_project_id, 'Todo')
    live_client.complete_todo(sandbox_project_id, created['id'])
    assert live_client.get_todo(sandbox_project_id, created['id'])['completed'] is True
    live_client.uncomplete_todo(sandbox_project_id, created['id'])
    assert live_client.get_todo(sandbox_project_id, created['id'])['completed'] is False


def test_trash_todo_marks_status(live_client, sandbox_project_id, todolist_id, prefix, id_store):
    created = live_client.create_todo(sandbox_project_id, todolist_id, content=f'{prefix} trash-test')
    id_store(created['id'], sandbox_project_id, 'Todo')
    live_client.delete_todo(sandbox_project_id, created['id'])
    # BC3 returns a 'status':'trashed' (or returns 204 with the item soft-gone).
    # The contract-level assertion is "get_todo no longer surfaces the item as
    # active"; let the BasecampClient's own get_todo result shape decide.
    fetched = live_client.get_todo(sandbox_project_id, created['id'])
    assert fetched.get('status') == 'trashed' or fetched.get('trashed') is True


def test_trash_todo_is_idempotent(live_client, sandbox_project_id, todolist_id, prefix, id_store):
    created = live_client.create_todo(sandbox_project_id, todolist_id, content=f'{prefix} idem-trash')
    id_store(created['id'], sandbox_project_id, 'Todo')
    live_client.delete_todo(sandbox_project_id, created['id'])
    # Re-trash must not raise; spec contract from Node port.
    live_client.delete_todo(sandbox_project_id, created['id'])
