"""Credentials dataclass (frozen, hashable) + CredentialProvider ABC contract."""
import pytest
from auth.provider import CredentialProvider, Credentials


def test_credentials_holds_token_and_account_id():
    c = Credentials(access_token="tok_abc", account_id="42")
    assert c.access_token == "tok_abc"
    assert c.account_id == "42"


def test_credentials_is_frozen():
    c = Credentials(access_token="tok_abc", account_id="42")
    with pytest.raises(AttributeError):
        c.access_token = "tok_xyz"  # type: ignore[misc]


def test_credentials_is_hashable():
    a = Credentials(access_token="tok_abc", account_id="42")
    b = Credentials(access_token="tok_abc", account_id="42")
    assert hash(a) == hash(b)
    assert {a, b} == {a}


def test_credential_provider_is_abstract():
    """CredentialProvider is an ABC — instantiating it directly must fail.
    This is the one guarantee the ABC makes; the concrete providers in
    Tasks 1.3/1.4 are what get instantiated."""
    with pytest.raises(TypeError):
        CredentialProvider()  # type: ignore[abstract]
