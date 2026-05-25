"""Live BC3 lifecycle tests for project create/update/trash/access.

Marked @pytest.mark.live. These hit a real Basecamp 3 sandbox and require
BASECAMP_TEST_REFRESH_TOKEN + sandbox-guard env vars (see tests/live/conftest.py).
The default `pytest` run excludes them via the `live` marker filter in
pytest.ini.

Safety contract:
  - Every test that creates a project MUST trash it in `finally`. The
    `id_store` recorder is for crash recovery; happy-path cleanup is the
    finally block's job.
  - Names MUST start with the `prefix` fixture value so leftovers are
    trivially identifiable (e.g. for the scripts/test_live_cleanup.py
    sweeper).
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.live


def test_create_then_trash_project(live_client, prefix, id_store):
    """Round-trip the project-create endpoint and immediately trash to
    avoid leaving sandbox cruft."""
    created = live_client.create_project(
        name=f'{prefix} created via mcp-test',
    )
    assert 'id' in created, f"create_project returned no id: {created!r}"
    project_id = created['id']
    # Record BEFORE trashing as belt-and-suspenders for the cleanup sweeper.
    id_store(project_id, project_id, 'Project')
    try:
        assert created['name'] == f'{prefix} created via mcp-test'
    finally:
        live_client.trash_project(project_id)


def test_update_project_changes_description_preserves_name(live_client, prefix, id_store):
    """The fetch-then-merge behaviour means a description-only patch MUST
    preserve the original name. If BC3's PUT requirement changes or the
    merge layer drops `name`, BC3 will 422 OR silently rename the project."""
    created = live_client.create_project(
        name=f'{prefix} merge-test',
    )
    project_id = created['id']
    id_store(project_id, project_id, 'Project')
    try:
        updated = live_client.update_project(
            project_id, description='new description from mcp-test',
        )
        assert updated['name'] == f'{prefix} merge-test', \
            f"update_project lost original name; got {updated['name']!r}"
        assert 'new description from mcp-test' in (updated.get('description') or '')
    finally:
        live_client.trash_project(project_id)


def test_update_project_access_grant_then_revoke(live_client, prefix, id_store):
    """Grant access to a second person (any account person other than the
    project creator), assert they show up in `granted`, then revoke and
    assert they show up in `revoked`. Skips if the sandbox account has
    only one person."""
    created = live_client.create_project(name=f'{prefix} access-test')
    project_id = created['id']
    id_store(project_id, project_id, kind='project')
    try:
        creator_id = (created.get('creator') or {}).get('id')
        people = live_client.get_people()
        candidate = next(
            (p for p in people if creator_id is not None and p['id'] != creator_id),
            None,
        )
        if candidate is None:
            pytest.skip("sandbox has no non-creator person to grant/revoke against")
        candidate_id = int(candidate['id'])
        granted = live_client.update_project_access(project_id, grant=[candidate_id])
        granted_ids = {int(p['id']) for p in granted.get('granted', [])}
        assert candidate_id in granted_ids, \
            f"Expected {candidate_id} in granted; got {granted!r}"
        revoked = live_client.update_project_access(project_id, revoke=[candidate_id])
        revoked_ids = {int(p['id']) for p in revoked.get('revoked', [])}
        assert candidate_id in revoked_ids, \
            f"Expected {candidate_id} in revoked; got {revoked!r}"
    finally:
        try:
            live_client.trash_project(project_id)
        except Exception:
            pass
