"""Live BC3 lifecycle tests for cards (kanban). Marked @pytest.mark.live."""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.live


@pytest.fixture(scope='module')
def card_table(live_client, sandbox_project_id):
    """Resolve the sandbox project's card table via the project dock."""
    project = live_client.get_project(sandbox_project_id)
    ct = next((d for d in project['dock'] if d['name'] == 'kanban_board'), None)
    if not ct:
        pytest.skip('Sandbox project has no kanban board')
    return ct


@pytest.fixture(scope='module')
def column_id(live_client, sandbox_project_id, card_table, prefix, id_store):
    """Use the first existing column in the table, or create one."""
    columns = live_client.get_columns(sandbox_project_id, str(card_table['id']))
    if columns:
        return str(columns[0]['id'])
    col = live_client.create_column(
        sandbox_project_id, str(card_table['id']), title=f'{prefix} sandbox column',
    )
    id_store(col['id'], sandbox_project_id, 'Column')
    return str(col['id'])


def test_create_card_round_trips_title(live_client, sandbox_project_id, column_id, prefix, id_store):
    card = live_client.create_card(
        sandbox_project_id, column_id, title=f'{prefix} create-test', content='initial',
    )
    id_store(card['id'], sandbox_project_id, 'CardTableCard')
    fetched = live_client.get_card(sandbox_project_id, card['id'])
    assert fetched['title'] == f'{prefix} create-test'


def test_update_card_changes_title(live_client, sandbox_project_id, column_id, prefix, id_store):
    card = live_client.create_card(sandbox_project_id, column_id, title=f'{prefix} update-test')
    id_store(card['id'], sandbox_project_id, 'CardTableCard')
    live_client.update_card(sandbox_project_id, card['id'], title=f'{prefix} updated')
    fetched = live_client.get_card(sandbox_project_id, card['id'])
    assert fetched['title'] == f'{prefix} updated'


@pytest.mark.xfail(
    reason="BC3 card-table cards expose no working completion endpoint: the card "
    "record's completion_url 404s on POST, the card-update PUT 400s on a "
    "`completed` field, and no completion route is documented in the BC3 API "
    "reference. complete_card/uncomplete_card cannot function — see "
    "docs/operations/follow-ups.md (pn-ai-portal).",
)
def test_complete_then_uncomplete_card(live_client, sandbox_project_id, column_id, prefix, id_store):
    card = live_client.create_card(sandbox_project_id, column_id, title=f'{prefix} complete-test')
    id_store(card['id'], sandbox_project_id, 'CardTableCard')
    live_client.complete_card(sandbox_project_id, card['id'])
    assert live_client.get_card(sandbox_project_id, card['id'])['completed'] is True
    live_client.uncomplete_card(sandbox_project_id, card['id'])
    assert live_client.get_card(sandbox_project_id, card['id'])['completed'] is False


def test_create_card_step_then_complete(live_client, sandbox_project_id, column_id, prefix, id_store):
    card = live_client.create_card(sandbox_project_id, column_id, title=f'{prefix} step-test')
    id_store(card['id'], sandbox_project_id, 'CardTableCard')
    step = live_client.create_card_step(sandbox_project_id, card['id'], title=f'{prefix} step 1')
    id_store(step['id'], sandbox_project_id, 'CardStep')
    live_client.complete_card_step(sandbox_project_id, step['id'])
    fetched = live_client.get_card_step(sandbox_project_id, step['id'])
    assert fetched['completed'] is True
