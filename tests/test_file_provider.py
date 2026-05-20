"""FileCredentialProvider: stdio mode (legacy path)."""
from unittest.mock import patch

import pytest

from auth.provider import Credentials, FileCredentialProvider


@patch('auth.provider.token_storage')
@patch('auth.provider.auth_manager')
def test_file_provider_returns_credentials_when_token_valid(mock_auth, mock_storage):
    with patch.dict('os.environ', {'BASECAMP_ACCOUNT_ID': '99'}, clear=True):
        mock_storage.get_token.return_value = {
            'access_token': 'tok_legacy',
            'refresh_token': 'rt_legacy',
            'account_id': '99',
            'expires_at': None,
        }
        mock_auth.ensure_authenticated.return_value = True
        p = FileCredentialProvider()
        creds = p.credentials_for(ctx=None)  # ctx is unused in this provider
        assert creds == Credentials(access_token='tok_legacy', account_id='99')


@patch('auth.provider.token_storage')
@patch('auth.provider.auth_manager')
def test_file_provider_returns_none_when_no_token(mock_auth, mock_storage):
    mock_storage.get_token.return_value = None
    mock_auth.ensure_authenticated.return_value = False
    p = FileCredentialProvider()
    assert p.credentials_for(ctx=None) is None


@patch('auth.provider.token_storage')
@patch('auth.provider.auth_manager')
def test_file_provider_returns_none_when_refresh_fails(mock_auth, mock_storage):
    mock_storage.get_token.return_value = {'access_token': 'tok_old'}
    mock_auth.ensure_authenticated.return_value = False  # refresh failed
    p = FileCredentialProvider()
    assert p.credentials_for(ctx=None) is None


@patch('auth.provider.token_storage')
@patch('auth.provider.auth_manager')
def test_file_provider_returns_none_when_account_id_missing(mock_auth, mock_storage):
    with patch.dict('os.environ', {}, clear=True):
        mock_storage.get_token.return_value = {
            'access_token': 'tok_legacy',
            'account_id': None,  # missing in token AND in env
        }
        mock_auth.ensure_authenticated.return_value = True
        p = FileCredentialProvider()
        assert p.credentials_for(ctx=None) is None


@patch('auth.provider.token_storage')
@patch('auth.provider.auth_manager')
def test_file_provider_returns_refreshed_token_after_successful_refresh(mock_auth, mock_storage):
    """The double get_token() exists so a refreshed token is picked up: the
    first read sees the stale token, the second (post-refresh) read sees the
    fresh one — and the fresh token is what ends up in Credentials."""
    stale = {'access_token': 'tok_stale', 'account_id': '42'}
    fresh = {'access_token': 'tok_fresh', 'account_id': '42'}
    mock_storage.get_token.side_effect = [stale, fresh]
    mock_auth.ensure_authenticated.return_value = True
    p = FileCredentialProvider()
    creds = p.credentials_for(ctx=None)
    assert creds == Credentials(access_token='tok_fresh', account_id='42')
    assert mock_storage.get_token.call_count == 2


@patch('auth.provider.token_storage')
@patch('auth.provider.auth_manager')
def test_file_provider_resolves_account_id_from_env_when_token_lacks_it(mock_auth, mock_storage):
    """account_id falls back to BASECAMP_ACCOUNT_ID when the token omits it."""
    with patch.dict('os.environ', {'BASECAMP_ACCOUNT_ID': '77'}, clear=True):
        mock_storage.get_token.return_value = {
            'access_token': 'tok_envid',
            'account_id': None,
        }
        mock_auth.ensure_authenticated.return_value = True
        p = FileCredentialProvider()
        creds = p.credentials_for(ctx=None)
        assert creds == Credentials(access_token='tok_envid', account_id='77')


@patch('auth.provider.token_storage')
@patch('auth.provider.auth_manager')
def test_file_provider_returns_none_when_token_unreadable_after_refresh(mock_auth, mock_storage):
    """If the post-refresh re-read returns nothing (refresh reported success
    but the token is unreadable), return None rather than crash."""
    valid = {'access_token': 'tok_valid', 'account_id': '42'}
    mock_storage.get_token.side_effect = [valid, None]
    mock_auth.ensure_authenticated.return_value = True
    p = FileCredentialProvider()
    assert p.credentials_for(ctx=None) is None
