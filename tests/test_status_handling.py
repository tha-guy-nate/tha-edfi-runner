"""Opt-in retry_statuses / allow_statuses and correlation_id on ThaStudentAssessment."""

from unittest.mock import MagicMock, patch

import pytest

from tha_edfi_runner._token_cache import TokenFetch
from tha_edfi_runner.errors import EdfiError
from tha_edfi_runner.resources.student_assessment.runner import (
    ThaStudentAssessment,
    _correlation_id,
)

BASE_URL = "https://edfi.example.com/api"
TOKEN = "test_token"
PATCH_SLEEP = "tha_edfi_runner.resources.student_assessment.runner.time.sleep"
CID = "0f8fad5b-d9cb-469f-a165-70867728950e"


def make_runner():
    return ThaStudentAssessment(base_url=BASE_URL, bearer_token=TOKEN)


def _ok(code=201):
    return {"status": None, "code": code, "data": None, "message": None, "raw_response": None}


def _err(code=400, data=None, raw_response=None):
    return {
        "status": "error",
        "code": code,
        "data": data,
        "message": f"{code} Client Error",
        "raw_response": raw_response,
    }


def _row(key="dist-1"):
    return {
        "row status": "",
        "District BK": key,
        "targetUrl": BASE_URL,
        "EdFi Token": TOKEN,
        "payload": '{"studentAssessmentIdentifier": "A1"}',
    }


# --- defaults unchanged ---


def test_400_is_error_and_not_retried_by_default():
    runner = make_runner()
    with patch.object(runner._req, "safe_call", return_value=_err(400)) as call:
        result = runner.post_payload({"id": "1"}, key="dist-1", commit=True)
    assert call.call_count == 1
    assert result["status"] == "error"
    assert result["http_status"] == 400


# --- retry_statuses ---


def test_retry_then_success():
    runner = make_runner()
    with (
        patch.object(runner._req, "safe_call", side_effect=[_err(400), _ok()]) as call,
        patch(PATCH_SLEEP) as sleep,
    ):
        result = runner.post_payload({"id": "1"}, key="dist-1", commit=True, retry_statuses={400})
    assert call.call_count == 2
    sleep.assert_called_once_with(1.0)
    assert result["status"] is None
    assert result["http_status"] == 201
    assert result["correlation_id"] is None


def test_retry_exhausted_reports_retry_count_and_backoff_doubles():
    runner = make_runner()
    with (
        patch.object(runner._req, "safe_call", return_value=_err(400)) as call,
        patch(PATCH_SLEEP) as sleep,
    ):
        result = runner.post_payload(
            {"id": "1"},
            key="dist-1",
            commit=True,
            retry_statuses={400},
            max_retries=3,
            retry_backoff=0.5,
        )
    assert call.call_count == 4
    assert [c.args[0] for c in sleep.call_args_list] == [0.5, 1.0, 2.0]
    assert result["status"] == "error"
    assert result["message"].endswith("(retries attempted: 3)")


def test_retry_exhausted_message_lists_correlation_id_and_retries():
    runner = make_runner()
    err = _err(400, data={"message": "bad", "correlationId": CID})
    with patch.object(runner._req, "safe_call", return_value=err), patch(PATCH_SLEEP):
        result = runner.post_payload(
            {"id": "1"}, key="dist-1", commit=True, retry_statuses={400}, max_retries=1
        )
    assert result["status"] == "error"
    assert result["correlation_id"] == CID
    assert result["message"] == (
        f"HTTP 400 Bad Request: bad (correlation id: {CID}; retries attempted: 1)"
    )


def test_retry_ignores_unlisted_status():
    runner = make_runner()
    with (
        patch.object(runner._req, "safe_call", return_value=_err(409)) as call,
        patch(PATCH_SLEEP) as sleep,
    ):
        result = runner.post_payload({"id": "1"}, key="dist-1", commit=True, retry_statuses={400})
    assert call.call_count == 1
    sleep.assert_not_called()
    assert result["status"] == "error"
    assert result["message"].endswith("(retries attempted: 0)")


def test_max_retries_zero_never_retries():
    runner = make_runner()
    with patch.object(runner._req, "safe_call", return_value=_err(400)) as call:
        runner.post_payload(
            {"id": "1"}, key="dist-1", commit=True, retry_statuses={400}, max_retries=0
        )
    assert call.call_count == 1


@pytest.mark.parametrize("kwargs", [{"max_retries": -1}, {"retry_backoff": -0.1}])
def test_invalid_retry_config_raises(kwargs):
    runner = make_runner()
    with pytest.raises(EdfiError):
        runner.post_payload({"id": "1"}, key="dist-1", commit=False, **kwargs)
    with pytest.raises(EdfiError):
        runner.batch_post_payload([_row()], payload_col="payload", key_col="District BK", **kwargs)


# --- allow_statuses ---


@pytest.mark.parametrize(("code", "expected"), [(400, "warning"), (409, None), (500, "error")])
def test_allow_statuses_maps_status_per_code(code, expected):
    runner = make_runner()
    err = _err(code, data={"detail": "Data validation failed."})
    with patch.object(runner._req, "safe_call", return_value=err):
        result = runner.post_payload(
            {"id": "1"},
            key="dist-1",
            commit=True,
            allow_statuses={400: "warning", 409: None},
        )
    assert result["status"] == expected
    assert result["http_status"] == code
    assert "Data validation failed." in result["message"]


def test_failed_retry_stays_error_even_when_allowed():
    runner = make_runner()
    with (
        patch.object(runner._req, "safe_call", return_value=_err(400)) as call,
        patch(PATCH_SLEEP),
    ):
        result = runner.post_payload(
            {"id": "1"},
            key="dist-1",
            commit=True,
            retry_statuses={400},
            max_retries=1,
            allow_statuses={400: "warning"},
        )
    assert call.call_count == 2
    assert result["status"] == "error"
    assert result["message"].endswith("(retries attempted: 1)")


def test_allow_does_not_apply_to_client_side_errors():
    runner = make_runner()
    result = runner.post_payload("not-json", key="dist-1", commit=True, allow_statuses={400: None})
    assert result["status"] == "error"


# --- batch_post_payload pass-through ---


def test_batch_post_payload_failed_retry_is_error_with_correlation_id():
    runner = make_runner()
    responses = [_err(400), _err(400, data={"correlationId": CID})]
    with (
        patch("tha_req_runner.runner.ThaReq.safe_call", side_effect=responses) as call,
        patch(PATCH_SLEEP),
    ):
        result = runner.batch_post_payload(
            [_row()],
            payload_col="payload",
            key_col="District BK",
            commit=True,
            retry_statuses={400},
            max_retries=1,
            allow_statuses={400: "warning"},
        )
    assert call.call_count == 2
    assert result[0]["status"] == "error"
    assert result[0]["correlation_id"] == CID
    assert result[0]["row_index"] == 0


def test_allowed_401_does_not_reauth():
    runner = make_runner()
    row = {**_row(), "key": "k", "secret": "s"}
    with (
        patch("tha_req_runner.runner.ThaReq.safe_call", return_value=_err(401)) as call,
        patch(
            "tha_edfi_runner._token_cache.fetch_token",
            return_value=TokenFetch("fresh", None, None),
        ) as refetch,
    ):
        result = runner.batch_post_payload(
            [row],
            payload_col="payload",
            key_col="District BK",
            commit=True,
            auth_key_col="key",
            auth_secret_col="secret",
            oauth_endpoint="oauth/token",
            allow_statuses={401: "warning"},
        )
    refetch.assert_not_called()
    assert call.call_count == 1
    assert result[0]["status"] == "warning"


def test_401_in_retry_statuses_raises():
    runner = make_runner()
    with pytest.raises(EdfiError, match="401"):
        runner.post_payload({"id": "1"}, key="dist-1", retry_statuses={400, 401})
    with pytest.raises(EdfiError, match="401"):
        runner.batch_post_payload(
            [_row()], payload_col="payload", key_col="District BK", retry_statuses={401}
        )


def test_batch_post_payload_early_error_has_correlation_key():
    runner = make_runner()
    row = {**_row(), "targetUrl": ""}
    result = runner.batch_post_payload(
        [row], payload_col="payload", key_col="District BK", commit=True
    )
    assert result[0]["correlation_id"] is None


def test_batch_post_payload_dry_run_has_correlation_key():
    runner = make_runner()
    result = runner.batch_post_payload([_row()], payload_col="payload", key_col="District BK")
    assert result[0]["correlation_id"] is None


# --- correlation_id ---


def test_correlation_id_from_problem_details_body():
    runner = make_runner()
    body = {"detail": "Data validation failed.", "status": 400, "correlationId": CID}
    with patch.object(runner._req, "safe_call", return_value=_err(400, data=body)):
        result = runner.post_payload({"id": "1"}, key="dist-1", commit=True)
    assert result["correlation_id"] == CID
    assert result["message"] == "HTTP 400 Bad Request: Data validation failed."


def test_correlation_id_from_header_fallback():
    raw = MagicMock()
    raw.headers = {"correlation-id": CID}
    assert _correlation_id(_err(400, raw_response=raw)) == CID


def test_correlation_id_body_wins_over_header():
    raw = MagicMock()
    raw.headers = {"correlation-id": "from-header"}
    assert _correlation_id(_err(400, data={"correlationId": CID}, raw_response=raw)) == CID


def test_correlation_id_ignores_non_string_header_values():
    # A MagicMock response's headers.get() returns a MagicMock, not a str.
    assert _correlation_id(_err(400, raw_response=MagicMock())) is None


def test_correlation_id_none_without_body_or_headers():
    assert _correlation_id(_err(400)) is None


def test_get_by_id_error_carries_correlation_id():
    runner = make_runner()
    err = _err(404, data={"correlationId": CID})
    with patch.object(runner._req, "safe_call", return_value=err):
        result = runner.get_by_id("missing")
    assert result["correlation_id"] == CID


def test_get_by_id_success_correlation_id_none():
    runner = make_runner()
    with patch.object(runner._req, "safe_call", return_value=_ok(200)):
        result = runner.get_by_id("abc")
    assert result["correlation_id"] is None


def test_delete_by_id_error_carries_correlation_id():
    runner = make_runner()
    err = _err(409, data={"correlationId": CID})
    with patch.object(runner._req, "safe_call", return_value=err):
        result = runner.delete_by_id("abc", key="dist-1", commit=True)
    assert result["correlation_id"] == CID


def test_get_all_error_carries_correlation_id_into_batch_placeholder():
    runner = make_runner()
    err = _err(500, data={"correlationId": CID})
    with patch("tha_req_runner.runner.ThaReq.safe_call", return_value=err):
        flat = runner.batch_get_all([_row()], key_col="District BK")
    assert flat == [
        {
            "District BK": "dist-1",
            "status": "error",
            "message": flat[0]["message"],
            "correlation_id": CID,
        }
    ]


def test_multiline_validation_400_body():
    # Ed-Fi validation messages are tab-indented lines with trailing newlines.
    body = {
        "message": (
            "Validation of 'Widget' failed.\n"
            "\tValidation of 'WidgetParts' failed.\n"
            "\tWidgetPart[0]: SampleDescriptorId is required.\n\n\n"
        ),
        "correlationId": CID,
    }
    runner = make_runner()
    with patch.object(runner._req, "safe_call", return_value=_err(400, data=body)):
        result = runner.post_payload(
            {"id": "1"}, key="dist-1", commit=True, allow_statuses={400: "warning"}
        )
    assert result["status"] == "warning"
    assert result["http_status"] == 400
    assert result["correlation_id"] == CID
    assert result["message"].startswith("HTTP 400 Bad Request: Validation of 'Widget' failed.")
    assert result["message"].endswith("SampleDescriptorId is required.")


# --- retry_messages ---

_TRANSIENT = {"message": "Unable to resolve value 'X' to an existing 'Student' resource."}
_PERMANENT = {"message": "SampleDescriptorId is required."}


def test_retry_messages_retries_only_matching_message():
    runner = make_runner()
    with (
        patch.object(
            runner._req, "safe_call", side_effect=[_err(400, data=_TRANSIENT), _ok()]
        ) as call,
        patch(PATCH_SLEEP),
    ):
        result = runner.post_payload(
            {"id": "1"},
            key="dist-1",
            commit=True,
            retry_statuses={400},
            retry_messages=["unable to resolve"],
        )
    assert call.call_count == 2
    assert result["status"] is None
    assert result["message"] is None


def test_retry_messages_skips_non_matching_message():
    runner = make_runner()
    with (
        patch.object(runner._req, "safe_call", return_value=_err(400, data=_PERMANENT)) as call,
        patch(PATCH_SLEEP) as sleep,
    ):
        result = runner.post_payload(
            {"id": "1"},
            key="dist-1",
            commit=True,
            retry_statuses={400},
            retry_messages=["unable to resolve"],
        )
    assert call.call_count == 1
    sleep.assert_not_called()
    assert result["status"] == "error"
    assert result["message"].endswith("(retries attempted: 0)")


def test_retry_messages_still_requires_status_match():
    runner = make_runner()
    with patch.object(runner._req, "safe_call", return_value=_err(409, data=_TRANSIENT)) as call:
        runner.post_payload(
            {"id": "1"},
            key="dist-1",
            commit=True,
            retry_statuses={400},
            retry_messages=["unable to resolve"],
        )
    assert call.call_count == 1


def test_batch_post_payload_passes_retry_messages():
    runner = make_runner()
    responses = [_err(400, data=_TRANSIENT), _ok()]
    with (
        patch("tha_req_runner.runner.ThaReq.safe_call", side_effect=responses) as call,
        patch(PATCH_SLEEP),
    ):
        result = runner.batch_post_payload(
            [_row()],
            payload_col="payload",
            key_col="District BK",
            commit=True,
            retry_statuses={400},
            retry_messages=["Unable to resolve"],
        )
    assert call.call_count == 2
    assert result[0]["status"] is None


def test_allow_applies_to_error_not_eligible_for_retry():
    runner = make_runner()
    with patch.object(runner._req, "safe_call", return_value=_err(400, data=_PERMANENT)):
        result = runner.post_payload(
            {"id": "1"},
            key="dist-1",
            commit=True,
            retry_statuses={400},
            retry_messages=["unable to resolve"],
            allow_statuses={400: "warning"},
        )
    assert result["status"] == "warning"
