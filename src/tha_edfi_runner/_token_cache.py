from __future__ import annotations

import math
import threading
import time
from dataclasses import dataclass, field
from typing import Any, NamedTuple

from tha_edfi_runner._backend import Backend
from tha_edfi_runner.base import ThaEdfiBase

_MAX_REFRESH_FAILURES = 3
_REFRESH_BACKOFF = 1.0  # seconds before the 2nd attempt, doubled after each failure
DEFAULT_REFRESH_COOLDOWN = 30.0  # seconds a client's refreshes pause after hitting the cap


class TokenFetch(NamedTuple):
    token: str | None
    expires_at: float | None  # wall-clock epoch, buffer applied; None if unknown
    error: str | None


def fetch_token(
    base_url: str, client_id: str, secret: str, oauth_endpoint: str, backend: Backend
) -> TokenFetch:
    """Fetch one token with client credentials, returning the failure reason instead of None."""
    instance = ThaEdfiBase(
        base_url=base_url,
        client_id=client_id,
        client_secret=secret,
        token_url=f"{base_url}/{oauth_endpoint.lstrip('/')}",
        backend=backend,
    )
    try:
        result = instance.fetch_token()
        exp = getattr(instance._auth, "expires_at", None)
    finally:
        auth_req = getattr(instance._auth, "_req", None)
        if auth_req is not None:
            auth_req.close_session()
    if result["status"] is not None or not result["token"]:
        return TokenFetch(None, None, result["message"] or "token fetch failed")
    return TokenFetch(result["token"], exp if isinstance(exp, float) and exp > 0 else None, None)


def is_expired(value: Any) -> bool:
    """True if ``value`` is an epoch time (number or numeric string) that has passed."""
    if value is None or isinstance(value, bool):
        return False
    try:
        return time.time() >= float(value)
    except (TypeError, ValueError):
        return False


@dataclass
class _Client:
    lock: threading.Lock = field(default_factory=threading.Lock)
    token: str | None = None
    expires_at: float | None = None
    rejected: set[str] = field(default_factory=set)
    failures: int = 0
    last_error: str = "token fetch failed"
    paused_until: float = 0.0  # time.monotonic()
    pauses: int = 0  # pauses in a row; >1 means the attempt after a pause failed too

    def fresh(self) -> str | None:
        if self.token is None:
            return None
        if self.expires_at is not None and time.time() >= self.expires_at:
            return None
        return self.token

    def fail(self, reason: str, cooldown: float) -> None:
        self.failures += 1
        self.last_error = reason
        if self.failures >= _MAX_REFRESH_FAILURES:
            self.paused_until = time.monotonic() + cooldown
            self.pauses += 1


class TokenCache:
    """Refreshed tokens for one batch call, shared by all its workers.

    Keyed by ``(base_url, client_id)``. A per-client lock makes a refresh single-flight:
    a worker that waited on the lock re-checks the cache and reuses another worker's
    token. Consecutive failures back off (1s, 2s); at the cap, refreshes for that client
    pause for ``cooldown`` seconds, then one attempt is allowed again (``cooldown=0``
    never pauses: every refresh after the cap is attempted). During
    the first pause, rows wait it out (outside the lock) instead of failing, so a short
    throttle delays the batch without losing rows; if the attempt after that pause fails
    too, later pauses fail rows fast. The count resets only when the ODS accepts a
    refreshed token (``confirm``), so credentials that get tokens the ODS rejects still
    hit the cap.
    """

    def __init__(
        self,
        *,
        oauth_endpoint: str,
        backend: Backend,
        cooldown: float = DEFAULT_REFRESH_COOLDOWN,
    ) -> None:
        self._oauth_endpoint = oauth_endpoint
        self._backend = backend
        self._cooldown = cooldown
        self._guard = threading.Lock()
        self._clients: dict[tuple[str, str], _Client] = {}

    def _client(self, base_url: str, client_id: str) -> _Client:
        with self._guard:
            return self._clients.setdefault((base_url, client_id), _Client())

    def current(self, base_url: str, client_id: str) -> str | None:
        """A cached, unexpired token for this client, or None."""
        client = self._client(base_url, client_id)
        with client.lock:
            return client.fresh()

    def is_rejected(self, base_url: str, client_id: str, token: str) -> bool:
        """True if ``token`` already got a 401 for this client in this batch."""
        client = self._client(base_url, client_id)
        with client.lock:
            return token in client.rejected

    def reject(self, base_url: str, client_id: str, token: str) -> None:
        """Record that ``token`` got a 401.

        A rejected *refreshed* token counts as a failed refresh (the credentials give
        tokens the ODS won't accept), but only once however many workers report it.
        """
        client = self._client(base_url, client_id)
        with client.lock:
            client.rejected.add(token)
            if client.token == token:
                client.token = None
                client.fail("refreshed token was rejected: auth error: HTTP 401", self._cooldown)

    def confirm(self, base_url: str, client_id: str, token: str) -> None:
        """Record that the ODS accepted the cached ``token``; resets the failure count."""
        client = self._client(base_url, client_id)
        with client.lock:
            if client.token == token:
                client.failures, client.paused_until, client.pauses = 0, 0.0, 0

    def refresh(
        self, base_url: str, client_id: str, secret: str, *, stale_token: str
    ) -> tuple[str | None, str | None]:
        """Return ``(token, None)`` or ``(None, reason)``.

        ``stale_token`` is the token that expired or got a 401; a cached token that
        differs from it was refreshed by another worker and is returned without a fetch.
        """
        client = self._client(base_url, client_id)
        waited = False
        while True:
            with client.lock:
                cached = client.fresh()
                if cached is not None and cached != stale_token and cached not in client.rejected:
                    return cached, None
                wait = client.paused_until - time.monotonic()
                if wait <= 0:
                    return self._attempt(client, base_url, client_id, secret)
                if waited or client.pauses > 1:
                    return None, (
                        f"{client.last_error} (refresh paused {math.ceil(wait)}s after "
                        f"{client.failures} failed refreshes)"
                    )
            # First pause: wait it out without holding the lock, then re-check (another
            # worker may have refreshed by then, or this worker makes the next attempt).
            time.sleep(wait)
            waited = True

    def _attempt(
        self, client: _Client, base_url: str, client_id: str, secret: str
    ) -> tuple[str | None, str | None]:
        """One token request, with back-off. Called with ``client.lock`` held."""
        if 0 < client.failures < _MAX_REFRESH_FAILURES:
            time.sleep(_REFRESH_BACKOFF * 2 ** (client.failures - 1))
        got = fetch_token(base_url, client_id, secret, self._oauth_endpoint, self._backend)
        if got.token is None:
            client.fail(got.error or "token fetch failed", self._cooldown)
            return None, client.last_error
        client.token, client.expires_at = got.token, got.expires_at
        return got.token, None
