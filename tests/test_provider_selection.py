"""Transport flag parsing + lifespan provider selection."""
import pytest
from unittest.mock import MagicMock, patch

from auth.provider import FileCredentialProvider, HeaderCredentialProvider


def test_parse_args_defaults_to_stdio():
    from basecamp_fastmcp import parse_args
    args = parse_args([])
    assert args.transport == 'stdio'


def test_parse_args_accepts_streamable_http():
    from basecamp_fastmcp import parse_args
    args = parse_args(['--transport', 'streamable-http', '--port', '8090', '--host', '127.0.0.1'])
    assert args.transport == 'streamable-http'
    assert args.port == 8090
    assert args.host == '127.0.0.1'


def test_parse_args_rejects_unknown_transport():
    from basecamp_fastmcp import parse_args
    with pytest.raises(SystemExit):
        parse_args(['--transport', 'sse'])


@pytest.mark.asyncio
async def test_lifespan_yields_file_provider_for_stdio():
    """The lifespan factory bound to 'stdio' yields a FileCredentialProvider
    under the 'provider' key of the lifespan context."""
    from basecamp_fastmcp import make_lifespan
    lifespan = make_lifespan('stdio')
    async with lifespan(object()) as ctx_dict:   # arg is the FastMCP app; unused
        assert isinstance(ctx_dict['provider'], FileCredentialProvider)


@pytest.mark.asyncio
async def test_lifespan_yields_header_provider_for_streamable_http():
    from basecamp_fastmcp import make_lifespan
    lifespan = make_lifespan('streamable-http')
    async with lifespan(object()) as ctx_dict:
        assert isinstance(ctx_dict['provider'], HeaderCredentialProvider)


@pytest.mark.asyncio
async def test_module_lifespan_honours_transport_mode():
    """_module_lifespan reads the module-level _transport_mode at startup — the
    seam that lets __main__ choose the transport after import time. Exercises
    both transports through the actual module global, which the make_lifespan
    tests above (called with an explicit arg) do not."""
    import basecamp_fastmcp
    original = basecamp_fastmcp._transport_mode
    try:
        basecamp_fastmcp._transport_mode = 'streamable-http'
        async with basecamp_fastmcp._module_lifespan(object()) as ctx_dict:
            assert isinstance(ctx_dict['provider'], HeaderCredentialProvider)
        basecamp_fastmcp._transport_mode = 'stdio'
        async with basecamp_fastmcp._module_lifespan(object()) as ctx_dict:
            assert isinstance(ctx_dict['provider'], FileCredentialProvider)
    finally:
        basecamp_fastmcp._transport_mode = original


def _make_ctx_with_provider(provider) -> MagicMock:
    """Build a fake FastMCP Context whose lifespan context carries `provider`.
    Branch-a: tools reach the provider via
    ctx.request_context.lifespan_context["provider"]. lifespan_context MUST be
    a real dict — a bare MagicMock there defeats _provider_from_ctx's
    isinstance(..., dict) check."""
    ctx = MagicMock(name='Context')
    ctx.request_context.lifespan_context = {"provider": provider}
    return ctx


def test_get_basecamp_client_uses_provider_from_lifespan_context():
    """_get_basecamp_client(ctx) reads the provider out of the lifespan
    context and builds a BasecampClient from the creds it returns."""
    from basecamp_fastmcp import _get_basecamp_client
    from auth.provider import Credentials

    provider = MagicMock()
    provider.credentials_for.return_value = Credentials(
        access_token='tok_provider',
        account_id='42',
    )
    ctx = _make_ctx_with_provider(provider)
    client = _get_basecamp_client(ctx)

    assert client is not None
    assert client.access_token == 'tok_provider'
    assert client.account_id == '42'
    provider.credentials_for.assert_called_once_with(ctx)


def test_get_basecamp_client_returns_none_when_provider_returns_none():
    from basecamp_fastmcp import _get_basecamp_client

    provider = MagicMock()
    provider.credentials_for.return_value = None
    ctx = _make_ctx_with_provider(provider)
    assert _get_basecamp_client(ctx) is None


def test_get_basecamp_client_no_args_legacy_path_still_works():
    """Backward compat during migration: _get_basecamp_client() with no args
    falls back to the file/env path. Removed in Chunk 4."""
    from basecamp_fastmcp import _get_basecamp_client
    with patch('basecamp_fastmcp.token_storage') as mock_storage, \
         patch('basecamp_fastmcp.auth_manager') as mock_auth, \
         patch('basecamp_fastmcp.os.environ', {'BASECAMP_ACCOUNT_ID': '42'}):
        mock_storage.get_token.return_value = {
            'access_token': 'tok_legacy',
            'account_id': '42',
        }
        mock_auth.ensure_authenticated.return_value = True
        client = _get_basecamp_client()  # no ctx — legacy entry point
        assert client is not None
        assert client.access_token == 'tok_legacy'


def test_get_basecamp_client_legacy_path_resolves_account_id_from_env():
    """Legacy no-args path: when the token omits account_id, it falls back to
    the BASECAMP_ACCOUNT_ID env var (the `or os.getenv(...)` branch)."""
    from basecamp_fastmcp import _get_basecamp_client
    with patch('basecamp_fastmcp.token_storage') as mock_storage, \
         patch('basecamp_fastmcp.auth_manager') as mock_auth, \
         patch('basecamp_fastmcp.os.environ', {'BASECAMP_ACCOUNT_ID': '77'}):
        mock_storage.get_token.return_value = {
            'access_token': 'tok_legacy',
            # account_id intentionally absent — env must supply it
        }
        mock_auth.ensure_authenticated.return_value = True
        client = _get_basecamp_client()  # no ctx — legacy entry point
        assert client is not None
        assert client.account_id == '77'
