"""Live BC3 lifecycle tests for messages + comments. Marked @pytest.mark.live."""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.live


@pytest.fixture(scope='module')
def message_board_id(live_client, sandbox_project_id):
    """Resolve the sandbox project's message board."""
    project = live_client.get_project(sandbox_project_id)
    mb = next((d for d in project['dock'] if d['name'] == 'message_board'), None)
    if not mb:
        pytest.skip('Sandbox project has no message board')
    return str(mb['id'])


def test_create_message_round_trips_subject(
    live_client, sandbox_project_id, message_board_id, prefix, id_store,
):
    msg = live_client.create_message(
        sandbox_project_id, subject=f'{prefix} hello world', content='<p>body</p>',
    )
    id_store(msg['id'], sandbox_project_id, 'Message::Post')
    fetched = live_client.get_message(sandbox_project_id, msg['id'])
    assert fetched['subject'] == f'{prefix} hello world'


def test_create_comment_on_message(
    live_client, sandbox_project_id, message_board_id, prefix, id_store,
):
    msg = live_client.create_message(
        sandbox_project_id, subject=f'{prefix} comment-target', content='<p>body</p>',
    )
    id_store(msg['id'], sandbox_project_id, 'Message::Post')
    cm = live_client.create_comment(msg['id'], sandbox_project_id, content='<p>reply</p>')
    id_store(cm['id'], sandbox_project_id, 'Comment')
    comments = live_client.get_comments(msg['id'], sandbox_project_id)
    assert any(c['id'] == cm['id'] for c in comments)
