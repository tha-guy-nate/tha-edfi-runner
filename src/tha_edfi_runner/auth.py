from __future__ import annotations

import threading
import time
from http import HTTPStatus
from typing import Any

from tha_req_runner.runner import ThaReq

from tha_edfi_runner._backend import Backend, make_req
from tha_edfi_runner.errors import EdfiError

_TOKEN_EXPIRY_BUFFER = 60  # seconds before expiry to proactively refresh


def _http_status_text(code: int) -> str:
    """``"HTTP 400 Bad Request"``; just ``"HTTP <code>"`` for a nonstandard code."""
    try:
        return f"HTTP {code} {HTTPStatus(code).phrase}"
    except ValueError:
        return f"HTTP {code}"


class OAuth2Auth:
    """Client-credentials OAuth2 — thread-safe token fetch and refresh."""

    def __init__(
        self,
        *,
        client_id: str,
        client_secret: str,
        token_url: str,
        backend: Backend = "requests",
    ) -> None:
        self._client_id = client_id
        self._client_secret = client_secret
        self._token_url = token_url
        self._req: ThaReq = make_req(backend)
        self._lock = threading.Lock()
        self._token: str | None = None
        self._expires_at: float = 0.0
        self._wall_expires_at: float = 0.0

    @property
    def expires_at(self) -> float:
        """Wall-clock epoch time when the current token expires (buffer already applied)."""
        return self._wall_expires_at

    def get_token(self) -> str:
        with self._lock:
            if self._token is None or time.monotonic() >= self._expires_at:
                self._refresh()
            assert self._token is not None
            return self._token

    def _refresh(self) -> None:
        session = self._req.get_session()
        result = self._req.safe_call(
            session.post,
            self._token_url,
            data={"grant_type": "client_credentials"},
            auth=(self._client_id, self._client_secret),
        )
        if result["status"] == "error" or not isinstance(result["data"], dict):
            code = result["code"]
            reason = _http_status_text(code) if isinstance(code, int) else result["message"]
            raise EdfiError(f"OAuth2 token fetch failed: {reason}")
        data: dict[str, Any] = result["data"]
        token = data.get("access_token")
        if not token:
            raise EdfiError("OAuth2 response missing access_token")
        expires_in = int(data.get("expires_in", 1800))
        self._token = token
        self._expires_at = time.monotonic() + expires_in - _TOKEN_EXPIRY_BUFFER
        self._wall_expires_at = time.time() + expires_in - _TOKEN_EXPIRY_BUFFER


class BearerAuth:
    """Pre-provided static bearer token — no refresh."""

    def __init__(self, *, token: str) -> None:
        self._token = token

    def get_token(self) -> str:
        return self._token


class BasicAuth:
    """Username/password basic auth — placeholder, not yet implemented."""

    def __init__(self, *, username: str, password: str) -> None:
        raise NotImplementedError("BasicAuth is not yet implemented. Use OAuth2Auth or BearerAuth.")

    def get_token(self) -> str:  # pragma: no cover
        raise NotImplementedError
