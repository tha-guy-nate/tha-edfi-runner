from unittest.mock import patch

import pytest

from tha_edfi_runner.auth import BearerAuth, OAuth2Auth
from tha_edfi_runner.base import ThaEdfiBase
from tha_edfi_runner.errors import EdfiError

BASE_URL = "https://edfi.example.com/api"


def test_bearer_auth_wired():
    base = ThaEdfiBase(base_url=BASE_URL, bearer_token="tok123")
    assert isinstance(base._auth, BearerAuth)
    assert base._auth.get_token() == "tok123"


def test_oauth2_auth_wired():
    base = ThaEdfiBase(base_url=BASE_URL, client_id="id", client_secret="secret")
    assert isinstance(base._auth, OAuth2Auth)


def test_oauth2_default_token_url():
    base = ThaEdfiBase(base_url=BASE_URL, client_id="id", client_secret="secret")
    assert base._auth._token_url == f"{BASE_URL}/oauth/token"


def test_oauth2_custom_token_url():
    base = ThaEdfiBase(
        base_url=BASE_URL,
        client_id="id",
        client_secret="secret",
        token_url="https://auth.example.com/token",
    )
    assert base._auth._token_url == "https://auth.example.com/token"


def test_no_auth_construction_succeeds():
    base = ThaEdfiBase(base_url=BASE_URL)
    assert base._auth is None


def test_no_auth_session_raises():
    base = ThaEdfiBase(base_url=BASE_URL)
    with pytest.raises(EdfiError, match="No auth configured"):
        base._session()


def test_basic_auth_raises():
    with pytest.raises(EdfiError, match="BasicAuth is not yet implemented"):
        ThaEdfiBase(base_url=BASE_URL, username="u", password="p")


def test_base_url_trailing_slash_stripped():
    base = ThaEdfiBase(base_url=BASE_URL + "/", bearer_token="tok")
    assert base.base_url == BASE_URL


def test_session_stamps_auth_header():
    base = ThaEdfiBase(base_url=BASE_URL, bearer_token="mytoken")
    session = base._session()
    assert session.headers["Authorization"] == "Bearer mytoken"


# --- fetch_token (single) ---


def test_fetch_token_success():
    base = ThaEdfiBase(base_url=BASE_URL, client_id="id", client_secret="secret")
    with patch.object(base._auth, "get_token", return_value="tok"):
        result = base.fetch_token()
    assert result == {"token": "tok", "status": None, "message": None}


def test_fetch_token_no_auth_returns_error():
    base = ThaEdfiBase(base_url=BASE_URL)
    result = base.fetch_token()
    assert result["status"] == "error"
    assert result["token"] is None


def test_fetch_token_401_returns_auth_error():
    base = ThaEdfiBase(base_url=BASE_URL, client_id="id", client_secret="secret")
    with patch.object(
        base._auth, "get_token", side_effect=EdfiError("token fetch failed (code 401)")
    ):
        result = base.fetch_token()
    assert result["status"] == "error"
    assert result["message"] == "auth error: HTTP 401"


def test_fetch_token_edfi_error_non_401():
    base = ThaEdfiBase(base_url=BASE_URL, client_id="id", client_secret="secret")
    with patch.object(base._auth, "get_token", side_effect=EdfiError("timeout")):
        result = base.fetch_token()
    assert result["status"] == "error"
    assert "timeout" in result["message"]


def test_fetch_token_unexpected_exception():
    base = ThaEdfiBase(base_url=BASE_URL, client_id="id", client_secret="secret")
    with patch.object(base._auth, "get_token", side_effect=RuntimeError("connection refused")):
        result = base.fetch_token()
    assert result["status"] == "error"
    assert "auth failed" in result["message"]


# --- batch_fetch_tokens ---

OAUTH_ENDPOINT = "/oauth/token"
KEY = "client_key"
SECRET = "client_secret"


def _make_auth_row(account="100", url=BASE_URL, key=KEY, secret=SECRET, status=""):
    return {
        "row status": status,
        "District BK": account,
        "targetUrl": url,
        "oAuthKey": key,
        "oAuthSecret": secret,
    }


def _make_base():
    return ThaEdfiBase()


def test_batch_fetch_tokens_success():
    rows = [_make_auth_row()]
    with patch("tha_edfi_runner.base.OAuth2Auth") as MockOAuth:
        MockOAuth.return_value.get_token.return_value = "fresh_token"
        result = _make_base().batch_fetch_tokens(
            rows, oauth_endpoint=OAUTH_ENDPOINT, account_col="District BK"
        )
    assert len(result) == 1
    assert result[0]["EdFi Token"] == "fresh_token"
    assert result[0]["row status"] is None
    assert result[0]["message"] is None


def test_batch_fetch_tokens_returns_account_creds_token():
    rows = [_make_auth_row()]
    with patch("tha_edfi_runner.base.OAuth2Auth") as MockOAuth:
        MockOAuth.return_value.get_token.return_value = "tok"
        result = _make_base().batch_fetch_tokens(
            rows, oauth_endpoint=OAUTH_ENDPOINT, account_col="District BK"
        )
    record = result[0]
    assert record["District BK"] == "100"
    assert record["oAuthKey"] == KEY
    assert record["oAuthSecret"] == SECRET
    assert record["EdFi Token"] == "tok"


def test_batch_fetch_tokens_deduplicates_by_account():
    rows = [_make_auth_row("100"), _make_auth_row("100"), _make_auth_row("200")]
    with patch("tha_edfi_runner.base.OAuth2Auth") as MockOAuth:
        MockOAuth.return_value.get_token.return_value = "tok"
        result = _make_base().batch_fetch_tokens(
            rows, oauth_endpoint=OAUTH_ENDPOINT, account_col="District BK"
        )
    assert len(result) == 2
    assert MockOAuth.call_count == 2


def test_batch_fetch_tokens_skips_error_warning_by_default():
    rows = [
        _make_auth_row("100", status="error"),
        _make_auth_row("200", status="warning"),
        _make_auth_row("300"),
    ]
    with patch("tha_edfi_runner.base.OAuth2Auth") as MockOAuth:
        MockOAuth.return_value.get_token.return_value = "tok"
        result = _make_base().batch_fetch_tokens(
            rows, oauth_endpoint=OAUTH_ENDPOINT, account_col="District BK"
        )
    assert len(result) == 1
    assert result[0]["District BK"] == "300"


def test_batch_fetch_tokens_missing_url_sets_error():
    rows = [_make_auth_row(url="")]
    with patch("tha_edfi_runner.base.OAuth2Auth"):
        result = _make_base().batch_fetch_tokens(
            rows, oauth_endpoint=OAUTH_ENDPOINT, account_col="District BK"
        )
    assert result[0]["row status"] == "error"
    assert "targetUrl" in result[0]["message"]
    assert result[0]["EdFi Token"] is None


def test_batch_fetch_tokens_missing_key_sets_error():
    rows = [_make_auth_row(key="")]
    with patch("tha_edfi_runner.base.OAuth2Auth"):
        result = _make_base().batch_fetch_tokens(
            rows, oauth_endpoint=OAUTH_ENDPOINT, account_col="District BK"
        )
    assert result[0]["row status"] == "error"
    assert "oAuthKey" in result[0]["message"]


def test_batch_fetch_tokens_missing_secret_sets_error():
    rows = [_make_auth_row(secret="")]
    with patch("tha_edfi_runner.base.OAuth2Auth"):
        result = _make_base().batch_fetch_tokens(
            rows, oauth_endpoint=OAUTH_ENDPOINT, account_col="District BK"
        )
    assert result[0]["row status"] == "error"
    assert "oAuthSecret" in result[0]["message"]


def test_batch_fetch_tokens_401_sets_auth_error_message():
    rows = [_make_auth_row()]
    with patch("tha_edfi_runner.base.OAuth2Auth") as MockOAuth:
        MockOAuth.return_value.get_token.side_effect = EdfiError("token fetch failed (code 401)")
        result = _make_base().batch_fetch_tokens(
            rows, oauth_endpoint=OAUTH_ENDPOINT, account_col="District BK"
        )
    assert result[0]["row status"] == "error"
    assert result[0]["message"] == "auth error: HTTP 401"


def test_batch_fetch_tokens_edfi_error_non_401():
    rows = [_make_auth_row()]
    with patch("tha_edfi_runner.base.OAuth2Auth") as MockOAuth:
        MockOAuth.return_value.get_token.side_effect = EdfiError(
            "OAuth2 token fetch failed: timeout"
        )
        result = _make_base().batch_fetch_tokens(
            rows, oauth_endpoint=OAUTH_ENDPOINT, account_col="District BK"
        )
    assert result[0]["row status"] == "error"
    assert "timeout" in result[0]["message"]


def test_batch_fetch_tokens_unexpected_exception():
    rows = [_make_auth_row()]
    with patch("tha_edfi_runner.base.OAuth2Auth") as MockOAuth:
        MockOAuth.return_value.get_token.side_effect = RuntimeError("connection refused")
        result = _make_base().batch_fetch_tokens(
            rows, oauth_endpoint=OAUTH_ENDPOINT, account_col="District BK"
        )
    assert result[0]["row status"] == "error"
    assert "auth failed" in result[0]["message"]


def test_batch_fetch_tokens_includes_expires_col():
    rows = [_make_auth_row()]
    with patch("tha_edfi_runner.base.OAuth2Auth") as MockOAuth:
        MockOAuth.return_value.get_token.return_value = "tok"
        result = _make_base().batch_fetch_tokens(
            rows, oauth_endpoint=OAUTH_ENDPOINT, account_col="District BK"
        )
    assert "token_expires_at" in result[0]


def test_batch_fetch_tokens_error_rows_include_expires_col():
    rows = [_make_auth_row(url="")]
    with patch("tha_edfi_runner.base.OAuth2Auth"):
        result = _make_base().batch_fetch_tokens(
            rows, oauth_endpoint=OAUTH_ENDPOINT, account_col="District BK"
        )
    assert "token_expires_at" in result[0]
    assert result[0]["token_expires_at"] is None


# --- api_version / _data_url ---


def test_data_url_with_api_version():
    base = ThaEdfiBase(base_url=BASE_URL, api_version="v3", bearer_token="tok")
    assert base._data_url == f"{BASE_URL}/data/v3"


def test_data_url_without_api_version():
    base = ThaEdfiBase(base_url=BASE_URL, bearer_token="tok")
    assert base._data_url == BASE_URL


def test_data_url_strips_version_slashes():
    base = ThaEdfiBase(base_url=BASE_URL, api_version="/v3/", bearer_token="tok")
    assert base._data_url == f"{BASE_URL}/data/v3"


def test_batch_fetch_tokens_sets_self_rows():
    base = _make_base()
    rows = [_make_auth_row()]
    with patch("tha_edfi_runner.base.OAuth2Auth") as MockOAuth:
        MockOAuth.return_value.get_token.return_value = "tok"
        result = base.batch_fetch_tokens(
            rows, oauth_endpoint=OAUTH_ENDPOINT, account_col="District BK"
        )
    assert base.rows is result
