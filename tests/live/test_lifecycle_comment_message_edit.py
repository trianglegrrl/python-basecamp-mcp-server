"""Live BC3 lifecycle tests for the comment / message edit surface.

Marked @pytest.mark.live. These hit a real Basecamp 3 sandbox and require
BASECAMP_TEST_REFRESH_TOKEN + sandbox-guard env vars (see tests/live/conftest.py).
The default `pytest` run excludes them via the `live` marker filter in
pytest.ini.

Safety contract:
  - Every test that creates a recording MUST trash it in `finally`.
  - Subjects / content prefixes MUST start with the `prefix` fixture value so
    leftovers are trivially identifiable (e.g. for the
    scripts/test_live_cleanup.py sweeper).
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.live


def test_create_then_update_then_trash_message(
    live_client, sandbox_project_id, prefix, id_store,
):
    """End-to-end: create message -> update content-only -> assert that the
    original `subject` survived the fetch-then-merge (this is the BC3 quirk
    we're guarding against — the PUT requires `subject`, so if the merge
    layer drops it either BC3 422s or the wrapper has to inject it from the
    current message)."""
    original_subject = f'{prefix} message lifecycle'
    created = live_client.create_message(
        sandbox_project_id,
        subject=original_subject,
        content='<p>original body</p>',
    )
    assert 'id' in created, f"create_message returned no id: {created!r}"
    message_id = created['id']
    id_store(message_id, sandbox_project_id, 'Message::Post')
    try:
        assert created['subject'] == original_subject

        # Update only the content; subject must survive the merge.
        updated = live_client.update_message(
            sandbox_project_id, message_id,
            content='<p>updated body via test</p>',
        )
        assert updated['subject'] == original_subject, \
            f"subject lost during merge; got {updated.get('subject')!r}"

        # Round-trip GET confirms the persisted state matches what the PUT
        # returned (BC3 returns the post-update representation in the PUT
        # response, but a follow-up GET verifies persistence).
        fetched = live_client.get_message(sandbox_project_id, message_id)
        assert fetched['subject'] == original_subject
        assert 'updated body via test' in fetched.get('content', '')
    finally:
        try:
            live_client.trash_recording(sandbox_project_id, message_id)
        except Exception:
            pass


def test_create_then_update_then_trash_comment(
    live_client, sandbox_project_id, prefix, id_store,
):
    """End-to-end: create a message to comment on, post a comment, update
    the comment's content via the new tool's client method, verify the
    update round-trips, then trash both recordings. Comments use a partial
    PUT (single-field patch on `content`) — no fetch-then-merge."""
    # Need a target recording first — use a message.
    msg = live_client.create_message(
        sandbox_project_id,
        subject=f'{prefix} comment-edit target',
        content='<p>target body</p>',
    )
    message_id = msg['id']
    id_store(message_id, sandbox_project_id, 'Message::Post')
    try:
        comment = live_client.create_comment(
            message_id, sandbox_project_id,
            content=f'<p>{prefix} original comment</p>',
        )
        comment_id = comment['id']
        id_store(comment_id, sandbox_project_id, 'Comment')
        try:
            # Pre-existing client method takes (comment_id, bucket_id, content)
            # — that's the quirk the tool wrapper bridges. Call it in the
            # quirk order here.
            updated = live_client.update_comment(
                comment_id, sandbox_project_id,
                f'<p>{prefix} edited comment</p>',
            )
            assert 'edited comment' in updated.get('content', ''), \
                f"update_comment didn't return updated content: {updated!r}"

            # Round-trip GET via get_comment to verify persistence.
            fetched = live_client.get_comment(comment_id, sandbox_project_id)
            assert 'edited comment' in fetched.get('content', ''), \
                f"get_comment didn't surface the update: {fetched!r}"
        finally:
            try:
                live_client.trash_recording(sandbox_project_id, comment_id)
            except Exception:
                pass
    finally:
        try:
            live_client.trash_recording(sandbox_project_id, message_id)
        except Exception:
            pass
