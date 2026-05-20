"""Batch A migration: projects + search tools accept ctx and route via provider."""
import inspect
from unittest.mock import MagicMock, patch

import pytest

from auth.provider import Credentials


@pytest.mark.asyncio
async def test_get_projects_routes_via_ctx_provider():
    """get_projects(ctx) reads the provider from
    ctx.request_context.lifespan_context['provider'] and routes through it."""
    from basecamp_fastmcp import get_projects

    provider = MagicMock(name='CredentialProvider')
    provider.credentials_for.return_value = Credentials(
        access_token='tok_batch_a', account_id='42',
    )
    fake_ctx = MagicMock(name='Context')
    fake_ctx.request_context.lifespan_context = {"provider": provider}

    with patch('basecamp_fastmcp.BasecampClient') as ClientCls:
        ClientCls.return_value.get_projects.return_value = [
            {'id': 1, 'name': 'sandbox'},
        ]
        result = await get_projects(fake_ctx)

    assert result == {
        'status': 'success',
        'projects': [{'id': 1, 'name': 'sandbox'}],
        'count': 1,
    }
    provider.credentials_for.assert_called_once_with(fake_ctx)


def test_search_basecamp_ctx_is_first_positional():
    """search_basecamp(ctx, query, project_id=None) — ctx is first."""
    from basecamp_fastmcp import search_basecamp

    params = list(inspect.signature(search_basecamp).parameters.values())
    assert params[0].name == 'ctx', \
        f"first param should be 'ctx', got {params[0].name!r}"
    assert params[1].name == 'query'
