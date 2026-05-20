"""Batch F migration: documents / uploads / webhooks / events."""
import inspect
from unittest.mock import MagicMock, patch

import pytest


def test_every_batch_f_tool_takes_ctx_first():
    names = [
        'get_events', 'get_webhooks', 'create_webhook', 'delete_webhook',
        'get_documents', 'get_document', 'create_document', 'update_document',
        'trash_document', 'get_uploads', 'get_upload',
    ]
    import basecamp_fastmcp as bc
    for name in names:
        fn = getattr(bc, name)
        params = list(inspect.signature(fn).parameters.values())
        assert params[0].name == 'ctx', \
            f"{name}: first param should be 'ctx', got {params[0].name!r}"


@pytest.mark.asyncio
async def test_migrated_tool_auth_error_path_accepts_ctx():
    """Regression guard for the 75 ctx-migrated call sites: when credentials
    are unavailable a migrated tool returns the auth-error dict via
    `_get_auth_error_response(ctx)` — the helper must accept ctx, not TypeError."""
    from basecamp_fastmcp import get_projects

    provider = MagicMock(name='CredentialProvider')
    provider.credentials_for.return_value = None  # no creds -> client is None
    fake_ctx = MagicMock(name='Context')
    fake_ctx.request_context.lifespan_context = {"provider": provider}

    with patch('basecamp_fastmcp.token_storage') as mock_storage:
        mock_storage.is_token_expired.return_value = True
        result = await get_projects(fake_ctx)

    assert isinstance(result, dict)
    assert 'error' in result
