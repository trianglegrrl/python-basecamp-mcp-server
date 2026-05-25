"""Live BC3 lifecycle tests for schedule entries.

Marked @pytest.mark.live. These hit a real Basecamp 3 sandbox and require
BASECAMP_TEST_REFRESH_TOKEN + sandbox-guard env vars (see tests/live/conftest.py).
The default `pytest` run excludes them via the `live` marker filter in
pytest.ini.

Safety contract:
  - Every test that creates a schedule entry MUST trash it in `finally`. BC3
    schedule entries are recordings, so cleanup uses the generic
    trash_recording helper (PUT .../recordings/{id}/status/trashed.json).
  - Summaries MUST start with the `prefix` fixture value so leftovers are
    trivially identifiable (e.g. for the scripts/test_live_cleanup.py sweeper).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

pytestmark = pytest.mark.live


def _isoplus(hours: int) -> str:
    """Return now+`hours` as an ISO-8601 UTC timestamp BC3 accepts."""
    return (datetime.now(timezone.utc) + timedelta(hours=hours)).strftime(
        '%Y-%m-%dT%H:%M:%S.000Z'
    )


def test_get_schedule_returns_schedule_with_entries_url(
    live_client, sandbox_project_id,
):
    """get_schedule() must dock-discover the schedule_id and return the
    BC3 schedule resource (id + entries_url at minimum)."""
    schedule = live_client.get_schedule(sandbox_project_id)
    assert 'id' in schedule, f"get_schedule missing id: {schedule!r}"
    assert 'entries_url' in schedule, \
        f"get_schedule missing entries_url: {schedule!r}"


def test_create_then_update_then_get_schedule_entry(
    live_client, sandbox_project_id, prefix, id_store,
):
    """End-to-end: create -> get -> update (summary only) -> assert that
    starts_at and ends_at survived the fetch-then-merge. If the merge layer
    drops them, BC3 either 422s or silently clears the timestamps."""
    starts_at = _isoplus(1)
    ends_at = _isoplus(2)
    created = live_client.create_schedule_entry(
        sandbox_project_id,
        summary=f'{prefix} entry created via test',
        starts_at=starts_at,
        ends_at=ends_at,
    )
    assert 'id' in created, f"create_schedule_entry returned no id: {created!r}"
    entry_id = created['id']
    id_store(entry_id, sandbox_project_id, 'ScheduleEntry')
    try:
        assert created['summary'] == f'{prefix} entry created via test'

        # Round-trip GET
        fetched = live_client.get_schedule_entry(sandbox_project_id, entry_id)
        assert fetched['id'] == entry_id
        assert fetched['summary'] == f'{prefix} entry created via test'

        # Update only the summary; starts_at/ends_at must survive the merge.
        updated = live_client.update_schedule_entry(
            sandbox_project_id, entry_id,
            summary=f'{prefix} entry renamed via test',
        )
        assert updated['summary'] == f'{prefix} entry renamed via test'
        # BC3 normalises ISO timestamps server-side; compare the date/hour prefix
        # so a normalisation diff (e.g. trailing Z vs +00:00) doesn't break the
        # invariant we actually care about: the fields weren't blanked.
        assert (updated.get('starts_at') or '')[:13] == starts_at[:13], \
            f"starts_at lost during merge; got {updated.get('starts_at')!r}"
        assert (updated.get('ends_at') or '')[:13] == ends_at[:13], \
            f"ends_at lost during merge; got {updated.get('ends_at')!r}"
    finally:
        try:
            live_client.trash_recording(sandbox_project_id, entry_id)
        except Exception:
            pass


def test_list_schedule_entries_includes_created_entry(
    live_client, sandbox_project_id, prefix, id_store,
):
    """get_schedule_entries() must list the just-created entry."""
    created = live_client.create_schedule_entry(
        sandbox_project_id,
        summary=f'{prefix} entry for list test',
        starts_at=_isoplus(3),
        ends_at=_isoplus(4),
    )
    entry_id = created['id']
    id_store(entry_id, sandbox_project_id, 'ScheduleEntry')
    try:
        entries = live_client.get_schedule_entries(sandbox_project_id)
        ids = {e['id'] for e in entries}
        assert entry_id in ids, \
            f"created entry {entry_id} not in entries list: {sorted(ids)[:10]}"
    finally:
        try:
            live_client.trash_recording(sandbox_project_id, entry_id)
        except Exception:
            pass
