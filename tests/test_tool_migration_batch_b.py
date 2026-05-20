"""Batch B migration: todo + todolist tools accept ctx and route via provider."""
import inspect
from unittest.mock import MagicMock, patch

import pytest

from auth.provider import Credentials


@pytest.mark.asyncio
async def test_get_todolists_routes_via_ctx_provider():
    """get_todolists(ctx, ...) reads the provider from the lifespan ctx."""
    from basecamp_fastmcp import get_todolists

    provider = MagicMock(name='CredentialProvider')
    provider.credentials_for.return_value = Credentials(
        access_token='tok_b', account_id='42',
    )
    fake_ctx = MagicMock(name='Context')
    fake_ctx.request_context.lifespan_context = {"provider": provider}

    with patch('basecamp_fastmcp.BasecampClient') as ClientCls:
        ClientCls.return_value.get_todolists.return_value = [{'id': 7}]
        result = await get_todolists(fake_ctx, project_id='1')

    assert result['status'] == 'success'
    provider.credentials_for.assert_called_once_with(fake_ctx)


def test_create_todo_ctx_is_first_positional():
    """create_todo has many kwargs — ctx must slot before all of them."""
    from basecamp_fastmcp import create_todo

    params = list(inspect.signature(create_todo).parameters.values())
    assert params[0].name == 'ctx'
    assert params[1].name == 'project_id'
    assert params[2].name == 'todolist_id'
    assert params[3].name == 'content'


def test_every_batch_b_tool_takes_ctx_first():
    """Spot-check across all 17 tools."""
    names = [
        'get_todolists', 'get_todos', 'get_todo', 'create_todo',
        'update_todo', 'delete_todo', 'complete_todo', 'uncomplete_todo',
        'archive_todo', 'reposition_todo',
        'get_todolist', 'create_todolist', 'update_todolist',
        'trash_todolist', 'get_todolist_groups', 'create_todolist_group',
        'reposition_todolist_group',
    ]
    import basecamp_fastmcp as bc
    for name in names:
        fn = getattr(bc, name)
        params = list(inspect.signature(fn).parameters.values())
        assert params[0].name == 'ctx', \
            f"{name}: first param should be 'ctx', got {params[0].name!r}"
