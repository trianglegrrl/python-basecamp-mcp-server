"""Transport flag parsing + lifespan provider selection."""
import pytest

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
