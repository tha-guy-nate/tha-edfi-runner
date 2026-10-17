"""HTTP backend selection: "requests" (default) or "httpx2"."""

from unittest.mock import patch

import httpx2
import pytest
import requests

from tha_edfi_runner import ThaEdfiBase, ThaStudentAssessment
from tha_edfi_runner.errors import EdfiError
from tha_edfi_runner.resources.student_assessment.runner import _refetch_token

BASE_URL = "https://edfi.example.com/api"
TOKEN = "test_token"
PATCH_RUNNER = "tha_edfi_runner.resources.student_assessment.runner.ThaStudentAssessment"


def _row():
    return {
        "row status": "",
        "District BK": "dist-1",
        "targetUrl": BASE_URL,
        "EdFi Token": TOKEN,
        "payload": '{"id": "1"}',
        "id": "abc",
    }


def test_default_backend_is_requests():
    base = ThaEdfiBase(base_url=BASE_URL, bearer_token=TOKEN)
    assert base.backend == "requests"
    assert isinstance(base._session(), requests.Session)


def test_httpx2_backend_uses_httpx2_client():
    base = ThaEdfiBase(base_url=BASE_URL, bearer_token=TOKEN, backend="httpx2")
    session = base._session()
    assert isinstance(session, httpx2.Client)
    assert session.headers["Authorization"] == f"Bearer {TOKEN}"


def test_oauth_uses_selected_backend():
    base = ThaEdfiBase(base_url=BASE_URL, client_id="id", client_secret="secret", backend="httpx2")
    assert base._auth._req._backend == "httpx2"


def test_unknown_backend_raises():
    with pytest.raises(EdfiError, match="backend must be one of"):
        ThaEdfiBase(base_url=BASE_URL, backend="urllib")


def test_missing_httpx2_extra_gives_install_hint():
    with (
        patch("tha_req_runner.runner._HTTPX_AVAILABLE", False),
        pytest.raises(ImportError, match=r"tha-edfi-runner\[httpx2\]"),
    ):
        ThaEdfiBase(base_url=BASE_URL, backend="httpx2")


def test_httpx2_post_payload_end_to_end():
    def handler(request: httpx2.Request) -> httpx2.Response:
        assert request.headers["Authorization"] == f"Bearer {TOKEN}"
        return httpx2.Response(400, json={"message": "bad", "correlationId": "cid-1"})

    runner = ThaStudentAssessment(base_url=BASE_URL, bearer_token=TOKEN, backend="httpx2")
    client = httpx2.Client(transport=httpx2.MockTransport(handler))
    with patch.object(runner._req, "get_session", return_value=client):
        result = runner.post_payload({"id": "1"}, key="dist-1", commit=True)
    assert result["status"] == "error"
    assert result["http_status"] == 400
    assert result["correlation_id"] == "cid-1"
    assert "bad" in result["message"]


@pytest.mark.parametrize(
    ("method", "kwargs"),
    [
        (
            "batch_post_payload",
            {"payload_col": "payload", "key_col": "District BK", "commit": True},
        ),
        ("batch_get_by_id", {"id_col": "id"}),
        ("batch_get_all", {"key_col": "District BK"}),
        ("batch_delete_by_id", {"id_col": "id", "key_col": "District BK", "commit": True}),
    ],
)
def test_batch_methods_pass_backend_to_row_instances(method, kwargs):
    runner = ThaStudentAssessment(base_url=BASE_URL, bearer_token=TOKEN, backend="httpx2")
    with patch(PATCH_RUNNER) as MockCls:
        getattr(runner, method)([_row()], **kwargs)
    assert MockCls.call_args.kwargs["backend"] == "httpx2"


def test_refetch_token_uses_backend():
    with patch("tha_edfi_runner.resources.student_assessment.runner.ThaEdfiBase") as MockBase:
        MockBase.return_value.fetch_token.return_value = {"token": "t", "status": None}
        _refetch_token(BASE_URL, "k", "s", "oauth/token", "httpx2")
    assert MockBase.call_args.kwargs["backend"] == "httpx2"


def test_batch_fetch_tokens_passes_backend():
    base = ThaEdfiBase(base_url=BASE_URL, backend="httpx2")
    row = {"account": "a1", "targetUrl": BASE_URL, "oAuthKey": "k", "oAuthSecret": "s"}
    with patch("tha_edfi_runner.base.ThaEdfiBase") as MockBase:
        MockBase.return_value.fetch_token.return_value = {"token": "t", "status": None}
        base.batch_fetch_tokens([row], oauth_endpoint="oauth/token", account_col="account")
    assert MockBase.call_args.kwargs["backend"] == "httpx2"
