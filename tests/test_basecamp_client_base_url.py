"""BasecampClient honors BASECAMP_API_BASE_URL for test/sandbox redirection."""
from unittest.mock import patch

from basecamp_client import BasecampClient


def test_default_base_url_is_production():
    with patch.dict('os.environ', {}, clear=True):
        c = BasecampClient(access_token='tok', account_id='42', user_agent='ua', auth_mode='oauth')
        assert c.base_url == 'https://3.basecampapi.com/42'


def test_env_var_overrides_default():
    with patch.dict('os.environ', {'BASECAMP_API_BASE_URL': 'http://localhost:9999'}, clear=True):
        c = BasecampClient(access_token='tok', account_id='42', user_agent='ua', auth_mode='oauth')
        assert c.base_url == 'http://localhost:9999'


def test_explicit_base_url_kwarg_wins_over_env():
    with patch.dict('os.environ', {'BASECAMP_API_BASE_URL': 'http://env:1111'}, clear=True):
        c = BasecampClient(
            access_token='tok', account_id='42', user_agent='ua', auth_mode='oauth',
            base_url='http://explicit:2222',
        )
        assert c.base_url == 'http://explicit:2222'


def test_empty_env_var_falls_through_to_default():
    """An empty BASECAMP_API_BASE_URL is treated as unset — the or-chain
    falls through to the production default rather than producing an empty
    base URL. Documents the intended empty-string behavior."""
    with patch.dict('os.environ', {'BASECAMP_API_BASE_URL': ''}, clear=True):
        c = BasecampClient(access_token='tok', account_id='42', user_agent='ua', auth_mode='oauth')
        assert c.base_url == 'https://3.basecampapi.com/42'
