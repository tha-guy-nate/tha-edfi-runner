"""Shared token refresh for batch methods (TokenCache + _call_with_reauth)."""

import threading
import time
from unittest.mock import MagicMock, patch

import pytest

from tha_edfi_runner._token_cache import TokenCache, TokenFetch, fetch_token, is_expired
from tha_edfi_runner.base import ThaEdfiBase
from tha_edfi_runner.resources.student_assessment.runner import ThaStudentAssessment

BASE_URL = "https://edfi.example.com/api"
OAUTH_EP = "oauth/token"
PATCH_RUNNER = "tha_edfi_runner.resources.student_assessment.runner.ThaStudentAssessment"
PATCH_FETCH = "tha_edfi_runner._token_cache.fetch_token"
PATCH_SLEEP = "tha_edfi_runner._token_cache.time.sleep"
REAUTH = {"auth_key_col": "oAuthKey", "auth_secret_col": "oAuthSecret", "oauth_endpoint": OAUTH_EP}
OK = {"key": "dist-1", "status": None, "message": None, "http_status": 201}
UNAUTHORIZED = {"key": "dist-1", "status": "error", "message": "auth error: HTTP 401"}


def _row(token="old", url=BASE_URL, key="client-1", **extra):
    return {
        "row status": "",
        "District BK": "dist-1",
        "targetUrl": url,
        "EdFi Token": token,
        "payload": '{"id": "1"}',
        "oAuthKey": key,
        "oAuthSecret": "secret",
        **extra,
    }


class FakeServer:
    """Stands in for ThaStudentAssessment: accepts only tokens in ``valid``."""

    def __init__(self, valid, barrier=None):
        self.valid = set(valid)
        self.barrier = barrier
        self.tokens_used = []
        self._lock = threading.Lock()

    def __call__(self, **kwargs):
        token = kwargs["bearer_token"]
        instance = MagicMock()

        def post_payload(*_args, **_kwargs):
            with self._lock:
                self.tokens_used.append(token)
            if self.barrier is not None and token not in self.valid:
                self.barrier.wait(timeout=5)
            return dict(OK) if token in self.valid else dict(UNAUTHORIZED)

        instance.post_payload.side_effect = post_payload
        return instance


def _post(rows, workers=1, **kwargs):
    runner = ThaStudentAssessment(base_url=BASE_URL, bearer_token="unused")
    return runner.batch_post_payload(
        rows,
        payload_col="payload",
        key_col="District BK",
        commit=True,
        workers=workers,
        **{**REAUTH, **kwargs},
    )


def _slow_fetch(token="new", expires_in=1800.0):
    def fetch(*_args):
        time.sleep(0.05)  # widen the window for concurrent refreshes
        return TokenFetch(token, time.time() + expires_in, None)

    return fetch


# --- the failure from the notes: token expires mid-batch ---


def test_expiry_mid_batch_refreshes_once_and_later_rows_use_new_token():
    server = FakeServer(valid={"new"})
    rows = [_row() for _ in range(40)]
    with (
        patch(PATCH_RUNNER, side_effect=server),
        patch(PATCH_FETCH, side_effect=_slow_fetch()) as fetch,
    ):
        results = _post(rows, workers=4)
    assert fetch.call_count == 1
    assert all(r["status"] is None for r in results)
    # After the first 401s, rows go straight to the cached token.
    assert server.tokens_used.count("old") <= 4
    assert server.tokens_used.count("new") == 40


def test_simultaneous_401s_from_n_workers_make_one_token_request():
    n = 4
    server = FakeServer(valid={"new"}, barrier=threading.Barrier(n))
    with (
        patch(PATCH_RUNNER, side_effect=server),
        patch(PATCH_FETCH, side_effect=_slow_fetch()) as fetch,
    ):
        results = _post([_row() for _ in range(n)], workers=n)
    assert fetch.call_count == 1
    assert server.tokens_used.count("old") == n
    assert all(r["status"] is None for r in results)


def test_proactive_refresh_shared_across_rows():
    server = FakeServer(valid={"new"})
    rows = [_row(token_expires_at=time.time() - 5) for _ in range(10)]
    with (
        patch(PATCH_RUNNER, side_effect=server),
        patch(PATCH_FETCH, side_effect=_slow_fetch()) as fetch,
    ):
        results = _post(rows, workers=4, expires_col="token_expires_at")
    assert fetch.call_count == 1
    assert "old" not in server.tokens_used
    assert all(r["status"] is None for r in results)


def test_cached_token_that_expires_is_refreshed_again():
    server = FakeServer(valid={"t1", "t2"})
    tokens = iter([TokenFetch("t1", time.time() - 1, None), TokenFetch("t2", None, None)])
    with (
        patch(PATCH_RUNNER, side_effect=server),
        patch(PATCH_FETCH, side_effect=lambda *_: next(tokens)) as fetch,
    ):
        results = _post(
            [_row(token_expires_at=0), _row(token_expires_at=0)],
            expires_col="token_expires_at",
        )
    assert fetch.call_count == 2
    assert server.tokens_used == ["t1", "t2"]
    assert all(r["status"] is None for r in results)


def test_each_client_gets_its_own_token():
    server = FakeServer(valid={"tok-client-1", "tok-client-2"})

    def fetch(_url, client_id, *_args):
        return TokenFetch(f"tok-{client_id}", None, None)

    rows = [_row(key="client-1"), _row(key="client-2"), _row(key="client-1")]
    with patch(PATCH_RUNNER, side_effect=server), patch(PATCH_FETCH, side_effect=fetch) as f:
        results = _post(rows)
    assert f.call_count == 2
    assert all(r["status"] is None for r in results)


# --- refresh failures are reported, not silent ---


def test_failed_refresh_after_401_reports_reason():
    server = FakeServer(valid=set())
    with (
        patch(PATCH_RUNNER, side_effect=server),
        patch(
            PATCH_FETCH, return_value=TokenFetch(None, None, "OAuth2 token fetch failed: HTTP 429")
        ),
    ):
        result = _post([_row()])[0]
    assert result["status"] == "error"
    assert result["message"] == (
        "auth error: HTTP 401; auth refresh failed: OAuth2 token fetch failed: HTTP 429"
    )
    assert server.tokens_used == ["old"]  # no retry with the stale token


def test_failed_proactive_refresh_skips_the_request():
    server = FakeServer(valid={"old"})
    with (
        patch(PATCH_RUNNER, side_effect=server),
        patch(PATCH_FETCH, return_value=TokenFetch(None, None, "auth error: HTTP 401")),
    ):
        result = _post([_row(token_expires_at=time.time() - 5)], expires_col="token_expires_at")[0]
    assert result["status"] == "error"
    assert result["message"] == "auth refresh failed: auth error: HTTP 401"
    assert server.tokens_used == []


def test_throttled_token_endpoint_pauses_after_cap():
    server = FakeServer(valid=set())
    with (
        patch(PATCH_RUNNER, side_effect=server),
        patch(PATCH_FETCH, return_value=TokenFetch(None, None, "HTTP 429")) as fetch,
        patch(PATCH_SLEEP) as sleep,
    ):
        results = _post([_row() for _ in range(10)])
    assert fetch.call_count == 3
    assert [c.args[0] for c in sleep.call_args_list] == [1.0, 2.0]
    assert results[0]["message"] == "auth error: HTTP 401; auth refresh failed: HTTP 429"
    assert results[1]["message"] == "auth refresh failed: HTTP 429"
    assert results[-1]["message"] == (
        "auth refresh failed: HTTP 429 (refresh paused 30s after 3 failed refreshes)"
    )
    # Only the first row sends the stale token; later rows know it was rejected.
    assert server.tokens_used == ["old"]


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def _paused_cache(clock):
    cache = TokenCache(oauth_endpoint=OAUTH_EP, backend="requests")
    with patch(PATCH_FETCH, return_value=TokenFetch(None, None, "HTTP 429")), patch(PATCH_SLEEP):
        for _ in range(3):
            cache.refresh(BASE_URL, "c", "s", stale_token="old")
    return cache


def test_refresh_resumes_after_cooldown_and_success_resets():
    clock = Clock()
    with patch("tha_edfi_runner._token_cache.time.monotonic", clock):
        cache = _paused_cache(clock)
        with patch(PATCH_FETCH) as fetch:
            _, reason = cache.refresh(BASE_URL, "c", "s", stale_token="old")
            fetch.assert_not_called()
        assert reason.endswith("(refresh paused 30s after 3 failed refreshes)")

        clock.now += 31
        with (
            patch(PATCH_FETCH, return_value=TokenFetch("t1", None, None)) as fetch,
            patch(PATCH_SLEEP) as sleep,
        ):
            assert cache.refresh(BASE_URL, "c", "s", stale_token="old") == ("t1", None)
        sleep.assert_not_called()  # the probe after a cool-down doesn't back off again
        assert fetch.call_count == 1
        cache.confirm(BASE_URL, "c", "t1")  # the ODS accepted it

        with (
            patch(PATCH_FETCH, return_value=TokenFetch(None, None, "x")),
            patch(PATCH_SLEEP) as sleep,
        ):
            assert cache.refresh(BASE_URL, "c", "s", stale_token="t1") == (None, "x")
        sleep.assert_not_called()  # count was reset: first failure has no back-off


def test_failed_probe_pauses_again():
    clock = Clock()
    with patch("tha_edfi_runner._token_cache.time.monotonic", clock):
        cache = _paused_cache(clock)
        clock.now += 31
        with patch(PATCH_FETCH, return_value=TokenFetch(None, None, "HTTP 429")) as fetch:
            assert cache.refresh(BASE_URL, "c", "s", stale_token="old") == (None, "HTTP 429")
            _, reason = cache.refresh(BASE_URL, "c", "s", stale_token="old")
        assert fetch.call_count == 1
        assert reason == "HTTP 429 (refresh paused 30s after 4 failed refreshes)"


def test_batch_recovers_mid_run_once_cooldown_ends():
    class SteppingClock:
        """Each call is 11s later, so the 30s pause ends a few rows after it starts."""

        def __init__(self):
            self.now = 1000.0

        def __call__(self):
            self.now += 11
            return self.now

    server = FakeServer(valid={"new"})
    fetches = [TokenFetch(None, None, "HTTP 429")] * 3 + [TokenFetch("new", None, None)]
    with (
        patch("tha_edfi_runner._token_cache.time.monotonic", SteppingClock()),
        patch(PATCH_RUNNER, side_effect=server),
        patch(PATCH_FETCH, side_effect=fetches) as fetch,
        patch(PATCH_SLEEP),
    ):
        results = _post([_row() for _ in range(10)])
    assert fetch.call_count == 4
    assert [r["status"] for r in results] == ["error"] * 5 + [None] * 5
    assert "refresh paused" in results[4]["message"]
    assert server.tokens_used == ["old"] + ["new"] * 5


def test_rejected_refreshed_token_counts_as_failure():
    server = FakeServer(valid=set())
    tokens = (TokenFetch(f"t{n}", None, None) for n in range(1, 100))
    with (
        patch(PATCH_RUNNER, side_effect=server),
        patch(PATCH_FETCH, side_effect=lambda *_: next(tokens)) as fetch,
        patch(PATCH_SLEEP),
    ):
        results = _post([_row() for _ in range(10)])
    # Credentials give tokens the ODS rejects: capped, not one token request per row.
    assert fetch.call_count == 3
    assert results[-1]["message"] == (
        "auth refresh failed: refreshed token was rejected: auth error: HTTP 401 "
        "(refresh paused 30s after 3 failed refreshes)"
    )


def test_same_rejected_token_from_many_workers_counts_once():
    cache = TokenCache(oauth_endpoint=OAUTH_EP, backend="requests")
    with patch(PATCH_FETCH, return_value=TokenFetch("t1", None, None)):
        cache.refresh(BASE_URL, "c", "s", stale_token="old")
    for _ in range(5):
        cache.reject(BASE_URL, "c", "t1")
    assert cache._clients[(BASE_URL, "c")].failures == 1
    assert cache.is_rejected(BASE_URL, "c", "t1")
    assert cache.current(BASE_URL, "c") is None


def test_only_an_accepted_token_resets_the_failure_count():
    cache = TokenCache(oauth_endpoint=OAUTH_EP, backend="requests")
    fetches = iter(
        [
            TokenFetch(None, None, "boom"),
            TokenFetch("t1", None, None),
            TokenFetch(None, None, "x"),
            TokenFetch("t2", None, None),
            TokenFetch(None, None, "y"),
        ]
    )
    with patch(PATCH_FETCH, side_effect=lambda *_: next(fetches)), patch(PATCH_SLEEP) as sleep:
        assert cache.refresh(BASE_URL, "c", "s", stale_token="old") == (None, "boom")
        assert cache.refresh(BASE_URL, "c", "s", stale_token="old") == ("t1", None)
        # t1 was never confirmed, so this failure is the 2nd in a row.
        assert cache.refresh(BASE_URL, "c", "s", stale_token="t1") == (None, "x")
        assert cache.refresh(BASE_URL, "c", "s", stale_token="t1") == ("t2", None)
        cache.confirm(BASE_URL, "c", "t2")
        assert cache.refresh(BASE_URL, "c", "s", stale_token="t2") == (None, "y")
    # Back-off before attempts 2 (1 failure), 3 (still 1: t1 didn't reset it) and
    # 4 (2 failures); none before attempt 5, after confirm reset the count.
    assert [c.args[0] for c in sleep.call_args_list] == [1.0, 1.0, 2.0]


def test_confirm_ignores_a_token_that_is_not_cached():
    cache = TokenCache(oauth_endpoint=OAUTH_EP, backend="requests")
    with patch(PATCH_FETCH, return_value=TokenFetch(None, None, "boom")):
        cache.refresh(BASE_URL, "c", "s", stale_token="old")
    cache.confirm(BASE_URL, "c", "old")
    assert cache._clients[(BASE_URL, "c")].failures == 1


# --- expiry parsing ---


def test_numeric_string_expiry_triggers_proactive_refresh():
    server = FakeServer(valid={"new"})
    with (
        patch(PATCH_RUNNER, side_effect=server),
        patch(PATCH_FETCH, return_value=TokenFetch("new", None, None)) as fetch,
    ):
        _post([_row(token_expires_at=str(time.time() - 5))], expires_col="token_expires_at")
    assert fetch.call_count == 1
    assert server.tokens_used == ["new"]


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, False),
        ("", False),
        ("not-a-time", False),
        (True, False),
        (0, True),
        ("1.5", True),
        (time.time() + 600, False),
        (str(time.time() + 600), False),
    ],
)
def test_is_expired(value, expected):
    assert is_expired(value) is expected


# --- re-auth not possible ---


@pytest.mark.parametrize(
    ("row_overrides", "kwargs", "hint"),
    [
        ({"oAuthKey": ""}, {}, "empty 'oAuthKey'"),
        ({"oAuthSecret": " "}, {}, "empty 'oAuthSecret'"),
        ({}, {"oauth_endpoint": None}, "oauth_endpoint not set"),
        ({}, {"auth_secret_col": None}, "auth_secret_col not set"),
    ],
)
def test_401_says_why_reauth_was_not_possible(row_overrides, kwargs, hint):
    server = FakeServer(valid=set())
    with patch(PATCH_RUNNER, side_effect=server), patch(PATCH_FETCH) as fetch:
        result = _post([{**_row(), **row_overrides}], **kwargs)[0]
    fetch.assert_not_called()
    assert result["message"] == f"auth error: HTTP 401 (re-auth not possible: {hint})"


def test_401_without_reauth_configured_is_plain():
    server = FakeServer(valid=set())
    runner = ThaStudentAssessment(base_url=BASE_URL, bearer_token="unused")
    with patch(PATCH_RUNNER, side_effect=server):
        result = runner.batch_post_payload(
            [_row()], payload_col="payload", key_col="District BK", commit=True
        )[0]
    assert result["message"] == "auth error: HTTP 401"


# --- fetch_token ---


def test_fetch_token_returns_token_and_expiry():
    with patch.object(ThaEdfiBase, "fetch_token", return_value={"token": "t", "status": None}):
        got = fetch_token(BASE_URL, "c", "s", OAUTH_EP, "requests")
    assert got.token == "t"
    assert got.error is None


def test_fetch_token_returns_reason_on_failure():
    failure = {"token": None, "status": "error", "message": "auth error: HTTP 401"}
    with patch.object(ThaEdfiBase, "fetch_token", return_value=failure):
        got = fetch_token(BASE_URL, "c", "s", OAUTH_EP, "requests")
    assert got == TokenFetch(None, None, "auth error: HTTP 401")


def test_fetch_token_reads_wall_clock_expiry_from_oauth():
    with patch("tha_edfi_runner._token_cache.ThaEdfiBase") as MockBase:
        MockBase.return_value.fetch_token.return_value = {"token": "t", "status": None}
        MockBase.return_value._auth.expires_at = 1234.5
        got = fetch_token(BASE_URL, "c", "s", "/oauth/token", "httpx2")
    assert got == TokenFetch("t", 1234.5, None)
    assert MockBase.call_args.kwargs["token_url"] == f"{BASE_URL}/oauth/token"
