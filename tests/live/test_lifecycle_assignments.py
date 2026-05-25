"""Live BC3 read-only tests for the assignment-by-person surface.

Marked @pytest.mark.live. These hit a real Basecamp 3 sandbox and require
BASECAMP_TEST_REFRESH_TOKEN + sandbox-guard env vars (see tests/live/conftest.py).
The default `pytest` run excludes them via the `live` marker filter in
pytest.ini.

These tests are READ-ONLY: get_my_assignments / get_my_due_assignments /
get_my_completed_assignments / get_assignments_for_person do not mutate
state, so no id_store / cleanup is required.

The find-by-person test (test_get_assignments_for_person_finds_token_owner)
uses the token owner's own id from /my/profile.json so it never depends on
any specific human being in the sandbox; if the token owner has no
assignments at all in the sandbox, the test skips rather than failing.
"""
from __future__ import annotations

from datetime import date

import pytest

pytestmark = pytest.mark.live


def test_get_my_assignments_returns_priorities_shape(live_client):
    """BC3 /my/assignments.json must return a dict with `priorities` and
    `non_priorities` keys; both must be lists (possibly empty). This is the
    shape the get_my_assignments tool surfaces unchanged."""
    result = live_client.get_my_assignments()
    assert isinstance(result, dict), (
        f"get_my_assignments must return a dict; got {type(result).__name__}"
    )
    assert 'priorities' in result, \
        f"get_my_assignments missing 'priorities' key: {result!r}"
    assert 'non_priorities' in result, \
        f"get_my_assignments missing 'non_priorities' key: {result!r}"
    assert isinstance(result['priorities'], list), \
        f"priorities must be a list; got {type(result['priorities']).__name__}"
    assert isinstance(result['non_priorities'], list), \
        f"non_priorities must be a list; got {type(result['non_priorities']).__name__}"


def test_get_my_due_assignments_with_overdue_scope(live_client):
    """When scope='overdue', BC3 filters server-side. Every returned entry
    with a due_on must satisfy due_on < today — a sanity check that BC3 is
    honoring the scope param (and that our client passes it through).

    Entries without due_on should not exist in this response (overdue means
    has-a-past-due-date) but we tolerate None defensively rather than
    failing the suite on a server-side quirk."""
    today = date.today().isoformat()
    entries = live_client.get_my_due_assignments(scope='overdue')
    assert isinstance(entries, list), \
        f"get_my_due_assignments must return a list; got {type(entries).__name__}"
    for entry in entries:
        due_on = entry.get('due_on')
        if due_on is None:
            continue  # defensive — BC3 should not return undated here
        assert due_on < today, (
            f"overdue scope returned entry with due_on={due_on!r} >= today={today!r}; "
            f"entry id={entry.get('id')!r}"
        )


def test_get_my_due_assignments_invalid_scope_raises(live_client):
    """Client-side guard: an invalid scope must raise ValueError BEFORE any
    HTTP request. Validates the client-side enum check is wired up."""
    with pytest.raises(ValueError, match='Invalid scope'):
        live_client.get_my_due_assignments(scope='invalid_scope')


def test_get_assignments_for_person_finds_token_owner(live_client):
    """Cross-check: resolve token owner's id from /my/profile.json, then
    find their assignments via the multi-step walk. If the result is empty,
    skip (token owner may simply have no assignments in the sandbox);
    otherwise assert the resolved person_id matches and every returned todo
    lists the owner as an assignee."""
    profile = live_client.get_my_profile()
    owner_id = profile['id']

    result = live_client.get_assignments_for_person(person_id=owner_id)
    assert isinstance(result, dict), \
        f"get_assignments_for_person must return a dict; got {type(result).__name__}"
    assert 'person_id' in result, f"missing person_id key: {result!r}"
    assert 'assignments' in result, f"missing assignments key: {result!r}"
    assert str(result['person_id']) == str(owner_id), (
        f"resolved person_id {result['person_id']!r} != owner id {owner_id!r}"
    )

    assignments = result['assignments']
    if not assignments:
        pytest.skip(
            f"token owner (id={owner_id}) has no assignments in this sandbox — "
            f"nothing to cross-check"
        )

    for todo in assignments:
        assignee_ids = {str(a.get('id')) for a in (todo.get('assignees') or [])}
        assert str(owner_id) in assignee_ids, (
            f"todo id={todo.get('id')!r} returned by get_assignments_for_person "
            f"but owner id {owner_id} not in assignees: {assignee_ids}"
        )
