"""Live BC3 read-only tests for the people/identity surface.

Marked @pytest.mark.live. These hit a real Basecamp 3 sandbox and require
BASECAMP_TEST_REFRESH_TOKEN + sandbox-guard env vars (see tests/live/conftest.py).
The default `pytest` run excludes them via the `live` marker filter in
pytest.ini.

These tests are READ-ONLY: get_my_profile / get_people / get_project_people
do not mutate state, so no id_store / cleanup is required (unlike the
schedule / cards / messages lifecycle tests).
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.live


def test_get_my_profile_returns_token_owner(live_client):
    """get_my_profile() must return a Person resource identifying the token
    owner — id, email_address, and name are the minimum BC3 fields downstream
    tools (get_my_assignments, etc.) rely on."""
    profile = live_client.get_my_profile()
    assert isinstance(profile, dict), \
        f"get_my_profile must return a dict; got {type(profile).__name__}"
    assert 'id' in profile, f"get_my_profile missing id: {profile!r}"
    assert 'email_address' in profile, \
        f"get_my_profile missing email_address: {profile!r}"
    assert 'name' in profile, f"get_my_profile missing name: {profile!r}"


def test_get_people_includes_token_owner(live_client):
    """The account-wide people list must contain the token owner. This is
    the cross-check that BC3 is treating the same identity consistently
    across /my/profile.json and /people.json."""
    profile = live_client.get_my_profile()
    people = live_client.get_people()
    assert isinstance(people, list), \
        f"get_people must return a list; got {type(people).__name__}"
    ids = {p['id'] for p in people}
    assert profile['id'] in ids, (
        f"token owner {profile['id']} not in get_people() ids "
        f"({len(ids)} people): {sorted(ids)[:10]}..."
    )


def test_get_project_people_is_subset_of_people(
    live_client, sandbox_project_id,
):
    """Every member of the sandbox project must also appear in the
    account-wide people list. Proves the per-project endpoint returns
    consistent identities (same `id`s) as the account-wide endpoint."""
    project_people = live_client.get_project_people(sandbox_project_id)
    assert isinstance(project_people, list), (
        f"get_project_people must return a list; "
        f"got {type(project_people).__name__}"
    )
    assert project_people, \
        f"sandbox project {sandbox_project_id} has no members — fixture broken?"

    account_people = live_client.get_people()
    account_ids = {p['id'] for p in account_people}
    project_ids = {p['id'] for p in project_people}
    missing = project_ids - account_ids
    assert not missing, (
        f"project members not found in account-wide people list: {missing}. "
        f"Expected project_ids ⊆ account_ids."
    )
