import time
from unittest.mock import patch

from tha_edfi_runner.base import ThaEdfiBase
from tha_edfi_runner.resources.student_assessment.runner import (
    ThaStudentAssessment,
    _refetch_token,
)

BASE_URL = "https://edfi.example.com/api"
TOKEN = "test_token"

PATCH_RUNNER = "tha_edfi_runner.resources.student_assessment.runner.ThaStudentAssessment"
PATCH_REFETCH = "tha_edfi_runner.resources.student_assessment.runner._refetch_token"


def make_runner():
    return ThaStudentAssessment(base_url=BASE_URL, bearer_token=TOKEN)


def _ok(data=None, code=200):
    return {"status": None, "code": code, "data": data, "message": None, "raw_response": None}


def _err(code=400, message="bad request", data=None):
    return {"status": "error", "code": code, "data": data, "message": message, "raw_response": None}


# --- _refetch_token ---


def test_refetch_token_returns_none_without_oauth_endpoint():
    assert _refetch_token(BASE_URL, "key", "secret", None) is None


def test_refetch_token_returns_token_on_success():
    with patch.object(
        ThaEdfiBase, "fetch_token", return_value={"token": "new-tok", "status": None}
    ):
        result = _refetch_token(BASE_URL, "key", "secret", "oauth/token")
    assert result == "new-tok"


def test_refetch_token_returns_none_on_failure():
    with patch.object(
        ThaEdfiBase,
        "fetch_token",
        return_value={"token": None, "status": "error", "message": "bad creds"},
    ):
        result = _refetch_token(BASE_URL, "key", "secret", "oauth/token")
    assert result is None


# --- post_payload (single) ---


def test_post_payload_dry_run():
    runner = make_runner()
    result = runner.post_payload({"studentAssessmentIdentifier": "A1"}, key="dist-1", commit=False)
    assert result == {"key": "dist-1", "status": "dry_run", "message": None}


def test_post_payload_success_dict():
    runner = make_runner()
    with patch.object(runner._req, "safe_call", return_value=_ok(code=201)):
        result = runner.post_payload(
            {"studentAssessmentIdentifier": "A1"}, key="dist-1", commit=True
        )
    assert result == {"key": "dist-1", "status": None, "message": None}


def test_post_payload_success_str():
    runner = make_runner()
    with patch.object(runner._req, "safe_call", return_value=_ok(code=201)):
        result = runner.post_payload(
            '{"studentAssessmentIdentifier": "A1"}', key="dist-1", commit=True
        )
    assert result == {"key": "dist-1", "status": None, "message": None}


def test_post_payload_invalid_json_str():
    runner = make_runner()
    result = runner.post_payload("not-json", key="dist-1", commit=True)
    assert result["status"] == "error"
    assert "invalid JSON" in result["message"]
    assert result["key"] == "dist-1"


def test_post_payload_error_response():
    runner = make_runner()
    with patch.object(runner._req, "safe_call", return_value=_err(400, "Validation failed")):
        result = runner.post_payload({"id": "1"}, key="dist-1", commit=True)
    assert result["status"] == "error"
    assert "Validation failed" in result["message"]
    assert result["key"] == "dist-1"


def test_post_payload_error_includes_api_detail():
    runner = make_runner()
    err = _err(400, "HTTP 400", data={"message": "identifier required"})
    with patch.object(runner._req, "safe_call", return_value=err):
        result = runner.post_payload({"id": "1"}, key="dist-1", commit=True)
    assert "identifier required" in result["message"]


def test_post_payload_401_message():
    runner = make_runner()
    with patch.object(runner._req, "safe_call", return_value=_err(401, "unauthorized")):
        result = runner.post_payload({"id": "1"}, key="dist-1", commit=True)
    assert result["status"] == "error"
    assert "401" in result["message"]


# --- batch_post_payload ---


def _make_post_row(key="dist-1", url=BASE_URL, token=TOKEN, payload=None, status=""):
    return {
        "row status": status,
        "District BK": key,
        "targetUrl": url,
        "EdFi Token": token,
        "payload": payload or '{"studentAssessmentIdentifier": "A1"}',
    }


def test_batch_post_payload_dry_run():
    runner = make_runner()
    rows = [_make_post_row("dist-1"), _make_post_row("dist-2")]
    result = runner.batch_post_payload(
        rows, payload_col="payload", key_col="District BK", commit=False
    )
    assert len(result) == 2
    assert all(r["status"] == "dry_run" for r in result)
    assert result[0]["key"] == "dist-1"
    assert result[0]["message"] is None


def test_batch_post_payload_dry_run_sets_self_rows():
    runner = make_runner()
    rows = [_make_post_row()]
    result = runner.batch_post_payload(
        rows, payload_col="payload", key_col="District BK", commit=False
    )
    assert runner.rows is result


def test_batch_post_payload_success():
    runner = make_runner()
    rows = [_make_post_row("dist-1")]
    with patch(PATCH_RUNNER) as MockCls:
        MockCls.return_value.post_payload.return_value = {
            "key": "dist-1",
            "status": None,
            "message": None,
        }
        result = runner.batch_post_payload(
            rows, payload_col="payload", key_col="District BK", commit=True
        )
    assert result[0] == {"key": "dist-1", "status": None, "message": None}


def test_batch_post_payload_missing_url_error():
    runner = make_runner()
    rows = [_make_post_row(url="")]
    result = runner.batch_post_payload(
        rows, payload_col="payload", key_col="District BK", commit=True
    )
    assert result[0]["status"] == "error"
    assert "targetUrl" in result[0]["message"]


def test_batch_post_payload_missing_token_error():
    runner = make_runner()
    rows = [_make_post_row(token="")]
    result = runner.batch_post_payload(
        rows, payload_col="payload", key_col="District BK", commit=True
    )
    assert result[0]["status"] == "error"
    assert "EdFi Token" in result[0]["message"]


def test_batch_post_payload_missing_payload_col_error():
    runner = make_runner()
    rows = [{"row status": "", "District BK": "dist-1", "targetUrl": BASE_URL, "EdFi Token": TOKEN}]
    result = runner.batch_post_payload(
        rows, payload_col="payload", key_col="District BK", commit=True
    )
    assert result[0]["status"] == "error"
    assert "missing column" in result[0]["message"]


def test_batch_post_payload_invalid_json_error():
    runner = make_runner()
    rows = [_make_post_row(payload="not-json")]
    result = runner.batch_post_payload(
        rows, payload_col="payload", key_col="District BK", commit=True
    )
    assert result[0]["status"] == "error"
    assert "invalid JSON" in result[0]["message"]


def test_batch_post_payload_accepts_non_string_payload():
    runner = make_runner()
    rows = [_make_post_row(payload={"studentAssessmentIdentifier": "A1"})]
    with patch(PATCH_RUNNER) as MockCls:
        MockCls.return_value.post_payload.return_value = {
            "key": "dist-1",
            "status": None,
            "message": None,
        }
        result = runner.batch_post_payload(
            rows, payload_col="payload", key_col="District BK", commit=True
        )
    assert result[0]["status"] is None


def test_batch_post_payload_expired_token_proactive_refresh():
    runner = make_runner()
    rows = [_with_creds({**_make_post_row(), "token_expires_at": time.time() - 10})]
    ok = {"key": "dist-1", "status": None, "message": None}
    with (
        patch(PATCH_RUNNER) as MockCls,
        patch(PATCH_REFETCH, return_value="fresh_tok") as mock_refetch,
    ):
        MockCls.return_value.post_payload.return_value = ok
        runner.batch_post_payload(
            rows,
            payload_col="payload",
            key_col="District BK",
            url_col="targetUrl",
            token_col="EdFi Token",
            auth_key_col="oAuthKey",
            auth_secret_col="oAuthSecret",
            oauth_endpoint=OAUTH_EP,
            expires_col="token_expires_at",
            commit=True,
        )
    mock_refetch.assert_called_once()


def test_batch_post_payload_skip_statuses_default():
    runner = make_runner()
    rows = [
        _make_post_row("dist-1", status="error"),
        _make_post_row("dist-2", status="warning"),
        _make_post_row("dist-3"),
    ]
    with patch(PATCH_RUNNER) as MockCls:
        MockCls.return_value.post_payload.return_value = {
            "key": "dist-3",
            "status": None,
            "message": None,
        }
        result = runner.batch_post_payload(
            rows, payload_col="payload", key_col="District BK", commit=True
        )
    assert len(result) == 1
    assert result[0]["key"] == "dist-3"


def test_batch_post_payload_sets_self_rows():
    runner = make_runner()
    rows = [_make_post_row()]
    with patch(PATCH_RUNNER) as MockCls:
        MockCls.return_value.post_payload.return_value = {
            "key": "dist-1",
            "status": None,
            "message": None,
        }
        result = runner.batch_post_payload(
            rows, payload_col="payload", key_col="District BK", commit=True
        )
    assert runner.rows is result


# --- get_by_id ---


def test_get_by_id_returns_data():
    runner = make_runner()
    resource = {"id": "abc-123", "studentAssessmentIdentifier": "A1"}
    with patch.object(runner._req, "safe_call", return_value=_ok(data=resource)):
        result = runner.get_by_id("abc-123")
    assert result["id"] == "abc-123"
    assert result["status"] is None
    assert result["message"] is None
    assert result["data"] == resource


def test_get_by_id_returns_error_on_404():
    runner = make_runner()
    with patch.object(runner._req, "safe_call", return_value=_err(code=404, message="not found")):
        result = runner.get_by_id("missing-id")
    assert result["id"] == "missing-id"
    assert result["status"] == "error"
    assert result["message"] == "not found"
    assert result["data"] is None


def test_get_by_id_returns_error_on_other_error():
    runner = make_runner()
    err = _err(code=500, message="server error")
    with patch.object(runner._req, "safe_call", return_value=err):
        result = runner.get_by_id("abc-123")
    assert result["id"] == "abc-123"
    assert result["status"] == "error"
    assert result["data"] is None


# --- batch_get_by_id ---


def _make_get_row(resource_id="rid-1", url=BASE_URL, token=TOKEN, status=""):
    return {"row status": status, "id": resource_id, "targetUrl": url, "EdFi Token": token}


def test_batch_get_by_id_success():
    runner = make_runner()
    rows = [_make_get_row("rid-1")]
    resource = {"id": "rid-1", "studentAssessmentIdentifier": "A1"}
    with patch(PATCH_RUNNER) as MockRunner:
        MockRunner.return_value.get_by_id.return_value = {
            "id": "rid-1",
            "status": None,
            "message": None,
            "data": resource,
        }
        result = runner.batch_get_by_id(rows, id_col="id")
    assert result[0]["status"] is None
    assert result[0]["data"] == resource
    assert result[0]["id"] == "rid-1"


def test_batch_get_by_id_404():
    runner = make_runner()
    rows = [_make_get_row("missing")]
    with patch(PATCH_RUNNER) as MockRunner:
        MockRunner.return_value.get_by_id.return_value = {
            "id": "missing",
            "status": "error",
            "message": "not found",
            "data": None,
        }
        result = runner.batch_get_by_id(rows, id_col="id")
    assert result[0]["status"] == "error"
    assert result[0]["message"] == "not found"
    assert result[0]["data"] is None


def test_batch_get_by_id_missing_id_col():
    runner = make_runner()
    rows = [{"row status": "", "targetUrl": BASE_URL, "EdFi Token": TOKEN}]
    result = runner.batch_get_by_id(rows, id_col="id")
    assert result[0]["status"] == "error"
    assert "missing column" in result[0]["message"]
    assert result[0]["id"] is None


def test_batch_get_by_id_missing_url():
    runner = make_runner()
    rows = [{"row status": "", "id": "rid-1", "targetUrl": "", "EdFi Token": TOKEN}]
    result = runner.batch_get_by_id(rows, id_col="id")
    assert result[0]["status"] == "error"
    assert "targetUrl" in result[0]["message"]


def test_batch_get_by_id_missing_token():
    runner = make_runner()
    rows = [{"row status": "", "id": "rid-1", "targetUrl": BASE_URL, "EdFi Token": ""}]
    result = runner.batch_get_by_id(rows, id_col="id")
    assert result[0]["status"] == "error"
    assert "EdFi Token" in result[0]["message"]


def test_batch_get_by_id_skips_error_warning():
    runner = make_runner()
    rows = [
        _make_get_row("rid-1", status="error"),
        _make_get_row("rid-2", status="warning"),
        _make_get_row("rid-3"),
    ]
    with patch(PATCH_RUNNER) as MockRunner:
        MockRunner.return_value.get_by_id.return_value = {
            "id": "rid-3",
            "status": None,
            "message": None,
            "data": {},
        }
        result = runner.batch_get_by_id(rows, id_col="id")
    assert len(result) == 1
    assert result[0]["id"] == "rid-3"


def test_batch_get_by_id_custom_col_names():
    runner = make_runner()
    rows = [{"row status": "", "rid": "abc", "url": BASE_URL, "tok": TOKEN}]
    with patch(PATCH_RUNNER) as MockRunner:
        MockRunner.return_value.get_by_id.return_value = {
            "id": "abc",
            "status": None,
            "message": None,
            "data": {},
        }
        result = runner.batch_get_by_id(rows, id_col="rid", url_col="url", token_col="tok")
    assert result[0]["status"] is None


def test_batch_get_by_id_sets_self_rows():
    runner = make_runner()
    rows = [_make_get_row()]
    with patch(PATCH_RUNNER) as MockRunner:
        MockRunner.return_value.get_by_id.return_value = {
            "id": "rid-1",
            "status": None,
            "message": None,
            "data": {},
        }
        result = runner.batch_get_by_id(rows, id_col="id")
    assert runner.rows is result


# --- get_all ---


def test_get_all_returns_normalized_shape():
    runner = make_runner()
    page = [{"id": "1"}, {"id": "2"}]
    with patch.object(runner._req, "safe_call", return_value=_ok(data=page)):
        result = runner.get_all(key="100")
    assert result["status"] is None
    assert result["message"] is None
    assert result["key"] == "100"
    assert len(result["data"]) == 2


def test_get_all_paginates():
    runner = make_runner()
    page1 = [{"id": str(i)} for i in range(2)]
    page2 = [{"id": "99"}]
    responses = [_ok(data=page1), _ok(data=page2)]
    with patch.object(runner._req, "safe_call", side_effect=responses):
        result = runner.get_all(key="100", limit=2)
    assert len(result["data"]) == 3


def test_get_all_returns_error_on_api_failure():
    runner = make_runner()
    with patch.object(runner._req, "safe_call", return_value=_err(500, "server error")):
        result = runner.get_all(key="100")
    assert result["status"] == "error"
    assert result["data"] is None
    assert result["key"] == "100"


def test_get_all_returns_error_on_401():
    runner = make_runner()
    with patch.object(runner._req, "safe_call", return_value=_err(401, "unauthorized")):
        result = runner.get_all(key="100")
    assert result["status"] == "error"
    assert "401" in result["message"]


def test_get_all_sets_self_rows():
    runner = make_runner()
    with patch.object(runner._req, "safe_call", return_value=_ok(data=[{"id": "1"}])):
        result = runner.get_all(key="100")
    assert runner.rows is result["data"]


def test_get_all_passes_query_params():
    runner = make_runner()
    with patch.object(runner._req, "safe_call", return_value=_ok(data=[])) as mock_call:
        runner.get_all(key="100", params={"assessmentIdentifier": "A1"})
    _, kwargs = mock_call.call_args
    assert kwargs["params"]["assessmentIdentifier"] == "A1"


def test_get_all_show_progress_paginates():
    runner = make_runner()
    page1 = [{"id": str(i)} for i in range(2)]
    page2 = [{"id": "99"}]
    responses = [_ok(data=page1), _ok(data=page2)]
    with patch.object(runner._req, "safe_call", side_effect=responses):
        result = runner.get_all(key="100", limit=2, show_progress=True)
    assert len(result["data"]) == 3


def test_get_all_show_progress_closes_on_error():
    runner = make_runner()
    with patch.object(runner._req, "safe_call", return_value=_err(500, "server error")):
        result = runner.get_all(key="100", show_progress=True)
    assert result["status"] == "error"


def _get_all_ok(key, records):
    return {"key": key, "status": None, "message": None, "data": records}


def _get_all_err(key, message="api error"):
    return {"key": key, "status": "error", "message": message, "data": None}


def _batch_row(key_val="100", url=BASE_URL, token=TOKEN, status=""):
    return {"row status": status, "District BK": key_val, "targetUrl": url, "EdFi Token": token}


# --- batch_get_all ---


def test_batch_get_all_returns_flat_list():
    runner = make_runner()
    rows = [_batch_row("100")]
    records = [{"id": "a1"}, {"id": "a2"}]
    with patch(PATCH_RUNNER) as Mock:
        Mock.return_value.get_all.return_value = _get_all_ok("100", records)
        result = runner.batch_get_all(rows, key_col="District BK")
    assert len(result) == 2


def test_batch_get_all_injects_key_col():
    runner = make_runner()
    rows = [_batch_row("100")]
    records = [{"id": "a1"}]
    with patch(PATCH_RUNNER) as Mock:
        Mock.return_value.get_all.return_value = _get_all_ok("100", records)
        result = runner.batch_get_all(rows, key_col="District BK")
    assert result[0]["District BK"] == "100"


def test_batch_get_all_multiple_accounts_flat():
    runner = make_runner()
    rows = [_batch_row("100"), _batch_row("200", url="https://other.example.com/api")]
    mock_instances = [
        type("M", (), {"get_all": lambda self, **kw: _get_all_ok("100", [{"id": "a"}])})(),
        type(
            "M", (), {"get_all": lambda self, **kw: _get_all_ok("200", [{"id": "b"}, {"id": "c"}])}
        )(),
    ]
    with patch(PATCH_RUNNER, side_effect=mock_instances):
        result = runner.batch_get_all(rows, key_col="District BK")
    assert len(result) == 3


def test_batch_get_all_deduplicates_by_key():
    runner = make_runner()
    rows = [_batch_row("100"), _batch_row("100")]
    with patch(PATCH_RUNNER) as Mock:
        Mock.return_value.get_all.return_value = _get_all_ok("100", [{"id": "a"}])
        result = runner.batch_get_all(rows, key_col="District BK")
    assert Mock.call_count == 1
    assert len(result) == 1


def test_batch_get_all_skips_error_warning():
    runner = make_runner()
    rows = [
        _batch_row("100", status="error"),
        _batch_row("200", status="warning"),
        _batch_row("300"),
    ]
    with patch(PATCH_RUNNER) as Mock:
        Mock.return_value.get_all.return_value = _get_all_ok("300", [{"id": "a"}])
        result = runner.batch_get_all(rows, key_col="District BK")
    assert Mock.call_count == 1
    assert result[0]["District BK"] == "300"


def test_batch_get_all_api_error_adds_placeholder():
    runner = make_runner()
    rows = [_batch_row("100")]
    with patch(PATCH_RUNNER) as Mock:
        Mock.return_value.get_all.return_value = _get_all_err("100", "timed out")
        result = runner.batch_get_all(rows, key_col="District BK")
    assert len(result) == 1
    assert result[0]["status"] == "error"
    assert result[0]["District BK"] == "100"
    assert "timed out" in result[0]["message"]


def test_batch_get_all_missing_url_adds_placeholder():
    runner = make_runner()
    rows = [{"row status": "", "District BK": "100", "targetUrl": "", "EdFi Token": TOKEN}]
    result = runner.batch_get_all(rows, key_col="District BK")
    assert result[0]["status"] == "error"
    assert "targetUrl" in result[0]["message"]


def test_batch_get_all_missing_token_adds_placeholder():
    runner = make_runner()
    rows = [{"row status": "", "District BK": "100", "targetUrl": BASE_URL, "EdFi Token": ""}]
    result = runner.batch_get_all(rows, key_col="District BK")
    assert result[0]["status"] == "error"
    assert "EdFi Token" in result[0]["message"]


def test_batch_get_all_sets_self_rows():
    runner = make_runner()
    rows = [_batch_row("100")]
    with patch(PATCH_RUNNER) as Mock:
        Mock.return_value.get_all.return_value = _get_all_ok("100", [{"id": "a"}])
        result = runner.batch_get_all(rows, key_col="District BK")
    assert runner.rows is result


def test_batch_get_all_custom_col_names():
    runner = make_runner()
    rows = [{"row status": "", "org": "100", "url": BASE_URL, "tok": TOKEN}]
    with patch(PATCH_RUNNER) as Mock:
        Mock.return_value.get_all.return_value = _get_all_ok("100", [{"id": "a"}])
        result = runner.batch_get_all(rows, key_col="org", url_col="url", token_col="tok")
    assert result[0]["org"] == "100"


# --- delete_by_id ---


def test_delete_by_id_dry_run():
    runner = make_runner()
    result = runner.delete_by_id("abc-123", key="dist-1", commit=False)
    assert result["status"] == "dry_run"
    assert result["key"] == "dist-1"
    assert result["id"] == "abc-123"


def test_delete_by_id_success():
    runner = make_runner()
    with patch.object(runner._req, "safe_call", return_value=_ok(code=204)):
        result = runner.delete_by_id("abc-123", key="dist-1", commit=True)
    assert result["status"] == "deleted"
    assert result["id"] == "abc-123"
    assert result["key"] == "dist-1"


def test_delete_by_id_error():
    runner = make_runner()
    with patch.object(runner._req, "safe_call", return_value=_err(404, "not found")):
        result = runner.delete_by_id("missing", key="dist-1", commit=True)
    assert result["status"] == "error"
    assert "not found" in result["message"]
    assert result["key"] == "dist-1"


# --- batch_delete_by_id ---


def _make_delete_row(resource_id="rid-1", key="dist-1", url=BASE_URL, token=TOKEN, status=""):
    return {
        "row status": status,
        "edfi_id": resource_id,
        "District BK": key,
        "targetUrl": url,
        "EdFi Token": token,
    }


def test_batch_delete_by_id_dry_run():
    runner = make_runner()
    rows = [_make_delete_row("rid-1", "dist-1"), _make_delete_row("rid-2", "dist-2")]
    result = runner.batch_delete_by_id(rows, id_col="edfi_id", key_col="District BK", commit=False)
    assert len(result) == 2
    assert all(r["status"] == "dry_run" for r in result)
    assert result[0]["id"] == "rid-1"
    assert result[0]["key"] == "dist-1"
    assert result[0]["message"] is None


def test_batch_delete_by_id_dry_run_sets_self_rows():
    runner = make_runner()
    rows = [_make_delete_row()]
    result = runner.batch_delete_by_id(rows, id_col="edfi_id", key_col="District BK", commit=False)
    assert runner.rows is result


def test_batch_delete_by_id_success():
    runner = make_runner()
    rows = [_make_delete_row("rid-1", "dist-1")]
    with patch(PATCH_RUNNER) as MockCls:
        MockCls.return_value.delete_by_id.return_value = {
            "id": "rid-1",
            "key": "dist-1",
            "status": "deleted",
            "message": None,
        }
        result = runner.batch_delete_by_id(
            rows, id_col="edfi_id", key_col="District BK", commit=True
        )
    assert result[0]["status"] == "deleted"
    assert result[0]["id"] == "rid-1"
    assert result[0]["key"] == "dist-1"


def test_batch_delete_by_id_api_error():
    runner = make_runner()
    rows = [_make_delete_row()]
    with patch(PATCH_RUNNER) as MockCls:
        MockCls.return_value.delete_by_id.return_value = {
            "id": "rid-1",
            "key": "dist-1",
            "status": "error",
            "message": "not found",
        }
        result = runner.batch_delete_by_id(
            rows, id_col="edfi_id", key_col="District BK", commit=True
        )
    assert result[0]["status"] == "error"
    assert "not found" in result[0]["message"]


def test_batch_delete_by_id_missing_id():
    runner = make_runner()
    rows = [_make_delete_row(resource_id="")]
    with patch(PATCH_RUNNER):
        result = runner.batch_delete_by_id(
            rows, id_col="edfi_id", key_col="District BK", commit=True
        )
    assert result[0]["status"] == "error"
    assert "missing column" in result[0]["message"]
    assert result[0]["id"] is None


def test_batch_delete_by_id_missing_url():
    runner = make_runner()
    rows = [_make_delete_row(url="")]
    with patch(PATCH_RUNNER):
        result = runner.batch_delete_by_id(
            rows, id_col="edfi_id", key_col="District BK", commit=True
        )
    assert result[0]["status"] == "error"
    assert "targetUrl" in result[0]["message"]


def test_batch_delete_by_id_missing_token():
    runner = make_runner()
    rows = [_make_delete_row(token="")]
    with patch(PATCH_RUNNER):
        result = runner.batch_delete_by_id(
            rows, id_col="edfi_id", key_col="District BK", commit=True
        )
    assert result[0]["status"] == "error"
    assert "EdFi Token" in result[0]["message"]


def test_batch_delete_by_id_skip_statuses_default():
    runner = make_runner()
    rows = [
        _make_delete_row("rid-1", status="error"),
        _make_delete_row("rid-2", status="warning"),
        _make_delete_row("rid-3", status=""),
    ]
    with patch(PATCH_RUNNER) as MockCls:
        MockCls.return_value.delete_by_id.return_value = {
            "id": "rid-3",
            "key": "dist-1",
            "status": "deleted",
            "message": None,
        }
        result = runner.batch_delete_by_id(
            rows, id_col="edfi_id", key_col="District BK", commit=True
        )
    assert len(result) == 1
    assert result[0]["id"] == "rid-3"


def test_batch_delete_by_id_skip_statuses_custom():
    runner = make_runner()
    rows = [
        _make_delete_row("rid-1", status="pending"),
        _make_delete_row("rid-2", status=""),
    ]
    with patch(PATCH_RUNNER) as MockCls:
        MockCls.return_value.delete_by_id.return_value = {
            "id": "rid-2",
            "key": "dist-1",
            "status": "deleted",
            "message": None,
        }
        result = runner.batch_delete_by_id(
            rows, id_col="edfi_id", key_col="District BK", skip_statuses=["pending"], commit=True
        )
    assert len(result) == 1
    assert result[0]["id"] == "rid-2"


def test_batch_delete_by_id_skip_statuses_empty_disables():
    runner = make_runner()
    rows = [
        _make_delete_row("rid-1", status="error"),
        _make_delete_row("rid-2", status=""),
    ]
    with patch(PATCH_RUNNER) as MockCls:
        MockCls.return_value.delete_by_id.return_value = {
            "id": "x",
            "key": "dist-1",
            "status": "deleted",
            "message": None,
        }
        result = runner.batch_delete_by_id(
            rows, id_col="edfi_id", key_col="District BK", skip_statuses=[], commit=True
        )
    assert len(result) == 2


def test_batch_delete_by_id_sets_self_rows():
    runner = make_runner()
    rows = [_make_delete_row()]
    with patch(PATCH_RUNNER) as MockCls:
        MockCls.return_value.delete_by_id.return_value = {
            "id": "rid-1",
            "key": "dist-1",
            "status": "deleted",
            "message": None,
        }
        result = runner.batch_delete_by_id(
            rows, id_col="edfi_id", key_col="District BK", commit=True
        )
    assert runner.rows is result


# --- re-auth behavior (time-based refresh + 401 retry) ---

AUTH_KEY = "client_key"
AUTH_SECRET = "client_secret"
OAUTH_EP = "/oauth/token"


def _with_creds(row: dict) -> dict:
    return {**row, "oAuthKey": AUTH_KEY, "oAuthSecret": AUTH_SECRET}


# batch_get_by_id


def test_batch_get_by_id_401_retries_and_succeeds():
    runner = make_runner()
    rows = [_with_creds(_make_get_row())]
    fail = {"id": "rid-1", "status": "error", "message": "auth error: HTTP 401", "data": None}
    ok = {"id": "rid-1", "status": None, "message": None, "data": {}}
    with patch(PATCH_RUNNER) as MockCls, patch(PATCH_REFETCH, return_value="new_tok"):
        MockCls.return_value.get_by_id.side_effect = [fail, ok]
        result = runner.batch_get_by_id(
            rows,
            id_col="id",
            auth_key_col="oAuthKey",
            auth_secret_col="oAuthSecret",
            oauth_endpoint=OAUTH_EP,
        )
    assert result[0]["status"] is None


def test_batch_get_by_id_401_retry_still_fails():
    runner = make_runner()
    rows = [_with_creds(_make_get_row())]
    fail = {"id": "rid-1", "status": "error", "message": "auth error: HTTP 401", "data": None}
    with patch(PATCH_RUNNER) as MockCls, patch(PATCH_REFETCH, return_value="new_tok"):
        MockCls.return_value.get_by_id.return_value = fail
        result = runner.batch_get_by_id(
            rows,
            id_col="id",
            auth_key_col="oAuthKey",
            auth_secret_col="oAuthSecret",
            oauth_endpoint=OAUTH_EP,
        )
    assert result[0]["status"] == "error"
    assert "401" in result[0]["message"]


def test_batch_get_by_id_no_reauth_params_401_stays_error():
    runner = make_runner()
    rows = [_make_get_row()]
    fail = {"id": "rid-1", "status": "error", "message": "auth error: HTTP 401", "data": None}
    with patch(PATCH_RUNNER) as MockCls:
        MockCls.return_value.get_by_id.return_value = fail
        result = runner.batch_get_by_id(rows, id_col="id")
    assert result[0]["status"] == "error"


def test_batch_get_by_id_expired_token_proactive_refresh():
    runner = make_runner()
    rows = [_with_creds({**_make_get_row(), "token_expires_at": time.time() - 10})]
    ok = {"id": "rid-1", "status": None, "message": None, "data": {}}
    with (
        patch(PATCH_RUNNER) as MockCls,
        patch(PATCH_REFETCH, return_value="fresh_tok") as mock_refetch,
    ):
        MockCls.return_value.get_by_id.return_value = ok
        runner.batch_get_by_id(
            rows,
            id_col="id",
            auth_key_col="oAuthKey",
            auth_secret_col="oAuthSecret",
            oauth_endpoint=OAUTH_EP,
            expires_col="token_expires_at",
        )
    mock_refetch.assert_called_once()


# api_version_col


def test_batch_get_by_id_uses_row_api_version():
    runner = ThaStudentAssessment(base_url=BASE_URL, bearer_token=TOKEN, api_version="v3")
    rows = [{**_make_get_row(), "apiVer": "v5"}]
    ok = {"id": "rid-1", "status": None, "message": None, "data": {}}
    with patch(PATCH_RUNNER) as MockCls:
        MockCls.return_value.get_by_id.return_value = ok
        runner.batch_get_by_id(rows, id_col="id", api_version_col="apiVer")
    _, kwargs = MockCls.call_args
    assert kwargs["api_version"] == "v5"


def test_batch_get_by_id_falls_back_to_runner_api_version():
    runner = ThaStudentAssessment(base_url=BASE_URL, bearer_token=TOKEN, api_version="v3")
    rows = [_make_get_row()]
    ok = {"id": "rid-1", "status": None, "message": None, "data": {}}
    with patch(PATCH_RUNNER) as MockCls:
        MockCls.return_value.get_by_id.return_value = ok
        runner.batch_get_by_id(rows, id_col="id", api_version_col="apiVer")
    _, kwargs = MockCls.call_args
    assert kwargs["api_version"] == "v3"


def test_batch_get_all_uses_row_api_version():
    runner = ThaStudentAssessment(base_url=BASE_URL, bearer_token=TOKEN, api_version="v3")
    rows = [{**_batch_row(), "apiVer": "v5"}]
    with patch(PATCH_RUNNER) as Mock:
        Mock.return_value.get_all.return_value = _get_all_ok("100", [])
        runner.batch_get_all(rows, key_col="District BK", api_version_col="apiVer")
    _, kwargs = Mock.call_args
    assert kwargs["api_version"] == "v5"


def test_batch_post_payload_uses_row_api_version():
    runner = ThaStudentAssessment(base_url=BASE_URL, bearer_token=TOKEN, api_version="v3")
    rows = [{**_make_post_row(), "apiVer": "v5"}]
    with patch(PATCH_RUNNER) as MockCls:
        MockCls.return_value.post_payload.return_value = {
            "key": "dist-1",
            "status": None,
            "message": None,
        }
        runner.batch_post_payload(
            rows,
            payload_col="payload",
            key_col="District BK",
            api_version_col="apiVer",
            commit=True,
        )
    _, kwargs = MockCls.call_args
    assert kwargs["api_version"] == "v5"


def test_batch_delete_by_id_uses_row_api_version():
    runner = ThaStudentAssessment(base_url=BASE_URL, bearer_token=TOKEN, api_version="v3")
    rows = [{**_make_delete_row(), "apiVer": "v5"}]
    with patch(PATCH_RUNNER) as MockCls:
        MockCls.return_value.delete_by_id.return_value = {
            "id": "rid-1",
            "key": "dist-1",
            "status": "deleted",
            "message": None,
        }
        runner.batch_delete_by_id(
            rows, id_col="edfi_id", key_col="District BK", api_version_col="apiVer", commit=True
        )
    _, kwargs = MockCls.call_args
    assert kwargs["api_version"] == "v5"


# batch_get_all


def test_batch_get_all_401_retries_and_succeeds():
    runner = make_runner()
    rows = [_with_creds(_batch_row())]
    fail = {"key": "100", "status": "error", "message": "auth error: HTTP 401", "data": None}
    ok = {"key": "100", "status": None, "message": None, "data": [{"id": "a"}]}
    with patch(PATCH_RUNNER) as MockCls, patch(PATCH_REFETCH, return_value="new_tok"):
        MockCls.return_value.get_all.side_effect = [fail, ok]
        result = runner.batch_get_all(
            rows,
            key_col="District BK",
            auth_key_col="oAuthKey",
            auth_secret_col="oAuthSecret",
            oauth_endpoint=OAUTH_EP,
        )
    assert result[0]["District BK"] == "100"
    assert "status" not in result[0] or result[0].get("status") != "error"


def test_batch_get_all_expired_token_proactive_refresh():
    runner = make_runner()
    rows = [_with_creds({**_batch_row(), "token_expires_at": time.time() - 10})]
    ok = {"key": "100", "status": None, "message": None, "data": [{"id": "a"}]}
    with (
        patch(PATCH_RUNNER) as MockCls,
        patch(PATCH_REFETCH, return_value="fresh_tok") as mock_refetch,
    ):
        MockCls.return_value.get_all.return_value = ok
        runner.batch_get_all(
            rows,
            key_col="District BK",
            auth_key_col="oAuthKey",
            auth_secret_col="oAuthSecret",
            oauth_endpoint=OAUTH_EP,
            expires_col="token_expires_at",
        )
    mock_refetch.assert_called_once()


# batch_post_payload


def test_batch_post_payload_401_retries_and_succeeds():
    runner = make_runner()
    rows = [_with_creds(_make_post_row())]
    fail = {"key": "dist-1", "status": "error", "message": "auth error: HTTP 401"}
    ok = {"key": "dist-1", "status": None, "message": None}
    with patch(PATCH_RUNNER) as MockCls, patch(PATCH_REFETCH, return_value="new_tok"):
        MockCls.return_value.post_payload.side_effect = [fail, ok]
        result = runner.batch_post_payload(
            rows,
            payload_col="payload",
            key_col="District BK",
            auth_key_col="oAuthKey",
            auth_secret_col="oAuthSecret",
            oauth_endpoint=OAUTH_EP,
            commit=True,
        )
    assert result[0]["status"] is None


# batch_delete_by_id


def test_batch_delete_by_id_401_retries_and_succeeds():
    runner = make_runner()
    rows = [_with_creds(_make_delete_row())]
    fail = {"id": "rid-1", "key": "dist-1", "status": "error", "message": "auth error: HTTP 401"}
    ok = {"id": "rid-1", "key": "dist-1", "status": "deleted", "message": None}
    with patch(PATCH_RUNNER) as MockCls, patch(PATCH_REFETCH, return_value="new_tok"):
        MockCls.return_value.delete_by_id.side_effect = [fail, ok]
        result = runner.batch_delete_by_id(
            rows,
            id_col="edfi_id",
            key_col="District BK",
            auth_key_col="oAuthKey",
            auth_secret_col="oAuthSecret",
            oauth_endpoint=OAUTH_EP,
            commit=True,
        )
    assert result[0]["status"] == "deleted"


def test_batch_delete_by_id_expired_token_proactive_refresh():
    runner = make_runner()
    rows = [_with_creds({**_make_delete_row(), "token_expires_at": time.time() - 10})]
    ok = {"id": "rid-1", "key": "dist-1", "status": "deleted", "message": None}
    with (
        patch(PATCH_RUNNER) as MockCls,
        patch(PATCH_REFETCH, return_value="fresh_tok") as mock_refetch,
    ):
        MockCls.return_value.delete_by_id.return_value = ok
        runner.batch_delete_by_id(
            rows,
            id_col="edfi_id",
            key_col="District BK",
            auth_key_col="oAuthKey",
            auth_secret_col="oAuthSecret",
            oauth_endpoint=OAUTH_EP,
            expires_col="token_expires_at",
            commit=True,
        )
    mock_refetch.assert_called_once()
