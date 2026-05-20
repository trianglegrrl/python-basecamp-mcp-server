"""HeaderCredentialProvider: streamable-http mode (per-request from headers)."""
from unittest.mock import MagicMock

import pytest
from starlette.datastructures import Headers   # FastMCP's underlying ASGI lib

from auth.provider import Credentials, HeaderCredentialProvider


def _make_ctx(headers: dict[str, str]) -> MagicMock:
    """Construct a fake FastMCP Context whose request_context.request.headers
    returns a starlette Headers (the case-insensitive shape the production
    SDK gives us). Tests pass plain dicts for readability; the helper wraps."""
    ctx = MagicMock()
    ctx.request_context.request.headers = Headers(headers)
    return ctx


def test_returns_credentials_when_both_headers_present():
    ctx = _make_ctx({
        'authorization': 'Bearer tok_xyz',
        'x-basecamp-account-id': '42',
    })
    creds = HeaderCredentialProvider().credentials_for(ctx)
    assert creds == Credentials(access_token='tok_xyz', account_id='42')


def test_returns_none_when_authorization_missing():
    ctx = _make_ctx({'x-basecamp-account-id': '42'})
    assert HeaderCredentialProvider().credentials_for(ctx) is None


def test_returns_none_when_account_id_missing():
    ctx = _make_ctx({'authorization': 'Bearer tok_xyz'})
    assert HeaderCredentialProvider().credentials_for(ctx) is None


def test_returns_none_when_authorization_lacks_bearer_prefix():
    ctx = _make_ctx({
        'authorization': 'Basic dXNlcjpwYXNz',
        'x-basecamp-account-id': '42',
    })
    assert HeaderCredentialProvider().credentials_for(ctx) is None


def test_returns_none_when_bearer_token_empty_after_prefix():
    ctx = _make_ctx({
        'authorization': 'Bearer ',
        'x-basecamp-account-id': '42',
    })
    assert HeaderCredentialProvider().credentials_for(ctx) is None


def test_returns_none_when_account_id_empty_string():
    ctx = _make_ctx({
        'authorization': 'Bearer tok_xyz',
        'x-basecamp-account-id': '',
    })
    assert HeaderCredentialProvider().credentials_for(ctx) is None


def test_accepts_non_digit_account_id_string():
    """We don't validate account_id format — BC's API will surface a bad id
    via 404/401 at call time. Both FileCredentialProvider and
    HeaderCredentialProvider treat account_id as opaque-string-but-non-empty
    to keep the two paths symmetrical (the FileProvider doesn't validate either)."""
    ctx = _make_ctx({
        'authorization': 'Bearer tok_xyz',
        'x-basecamp-account-id': 'pn-bc-uat',  # nonstandard but not our problem
    })
    creds = HeaderCredentialProvider().credentials_for(ctx)
    assert creds == Credentials(access_token='tok_xyz', account_id='pn-bc-uat')


def test_handles_case_insensitive_header_lookup():
    ctx = _make_ctx({
        'Authorization': 'Bearer tok_case',
        'X-Basecamp-Account-Id': '42',
    })
    creds = HeaderCredentialProvider().credentials_for(ctx)
    assert creds == Credentials(access_token='tok_case', account_id='42')


def test_returns_none_when_ctx_lacks_request_context():
    """A ctx with no request_context (an SDK shape change, or a malformed
    ctx) is handled gracefully: the AttributeError is caught and
    credentials_for returns None rather than propagating."""
    ctx = MagicMock(spec=[])  # spec=[] → any attribute access raises AttributeError
    assert HeaderCredentialProvider().credentials_for(ctx) is None


def test_raises_type_error_when_ctx_is_none():
    """After Chunk 4 cleanup, HeaderCredentialProvider rejects ctx=None as a
    contract violation — no production call site can pass None anymore."""
    p = HeaderCredentialProvider()
    with pytest.raises(TypeError, match='ctx'):
        p.credentials_for(None)  # type: ignore[arg-type]
