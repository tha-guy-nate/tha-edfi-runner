import time
from unittest.mock import patch

import pytest

from tha_edfi_runner.auth import BasicAuth, BearerAuth, OAuth2Auth
from tha_edfi_runner.errors import EdfiError


def test_bearer_returns_token():
    auth = BearerAuth(token="abc")
    assert auth.get_token() == "abc"


def test_basic_auth_raises_not_implemented():
    with pytest.raises(NotImplementedError):
        BasicAuth(username="u", password="p")


def _make_oauth(token_url="https://example.com/token"):
    return OAuth2Auth(client_id="id", client_secret="secret", token_url=token_url)


def _token_result(token="tok", expires_in=1800):
    return {
        "status": None,
        "code": 200,
        "data": {"access_token": token, "expires_in": expires_in},
        "message": None,
        "raw_response": None,
    }


def test_oauth2_fetches_token():
    auth = _make_oauth()
    with patch.object(auth._req, "safe_call", return_value=_token_result("mytoken")):
        assert auth.get_token() == "mytoken"


def test_oauth2_caches_token():
    auth = _make_oauth()
    call_count = 0

    def fake_safe_call(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        return _token_result("tok")

    with patch.object(auth._req, "safe_call", side_effect=fake_safe_call):
        auth.get_token()
        auth.get_token()
    assert call_count == 1


def test_oauth2_refreshes_expired_token():
    auth = _make_oauth()
    auth._token = "old"
    auth._expires_at = time.monotonic() - 1

    with patch.object(auth._req, "safe_call", return_value=_token_result("new")):
        token = auth.get_token()
    assert token == "new"


def test_oauth2_raises_on_error_response():
    auth = _make_oauth()
    err = {
        "status": "error", "code": 401, "data": None,
        "message": "Unauthorized", "raw_response": None,
    }
    with patch.object(auth._req, "safe_call", return_value=err):
        with pytest.raises(EdfiError, match="OAuth2 token fetch failed"):
            auth.get_token()


def test_oauth2_raises_on_missing_access_token():
    auth = _make_oauth()
    empty = {"status": None, "code": 200, "data": {}, "message": None, "raw_response": None}
    with patch.object(auth._req, "safe_call", return_value=empty):
        with pytest.raises(EdfiError, match="missing access_token"):
            auth.get_token()
