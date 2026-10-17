from __future__ import annotations

import threading
import time
from typing import Any, NamedTuple

from tha_edfi_runner._backend import Backend
from tha_edfi_runner.base import ThaEdfiBase

_MAX_REFRESH_FAILURES = 3
_REFRESH_BACKOFF = 1.0  # seconds before the 2nd attempt, doubled after each failure


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
    result = instance.fetch_token()
    if result["status"] is not None or not result["token"]:
        return TokenFetch(None, None, result["message"] or "token fetch failed")
    exp = getattr(instance._auth, "expires_at", None)
    return TokenFetch(result["token"], exp if isinstance(exp, float) and exp > 0 else None, None)


def is_expired(value: Any) -> bool:
    """True if ``value`` is an epoch time (number or numeric string) that has passed."""
    if value is None or isinstance(value, bool):
        return False
    try:
        return time.time() >= float(value)
    except (TypeError, ValueError):
        return False


class TokenCache:
    """Refreshed tokens for one batch call, shared by all its workers.

    Keyed by ``(base_url, client_id)``. A per-key lock makes a refresh single-flight: a
    worker that waited on the lock re-checks the cache and reuses another worker's token.
    After ``_MAX_REFRESH_FAILURES`` consecutive failures a key fails fast for the rest of
    the batch.
    """

    def __init__(self, *, oauth_endpoint: str, backend: Backend) -> None:
        self._oauth_endpoint = oauth_endpoint
        self._backend = backend
        self._guard = threading.Lock()
        self._locks: dict[tuple[str, str], threading.Lock] = {}
        self._tokens: dict[tuple[str, str], tuple[str, float | None]] = {}
        self._failures: dict[tuple[str, str], int] = {}
        self._last_error: dict[tuple[str, str], str] = {}

    def _fresh(self, key: tuple[str, str]) -> str | None:
        entry = self._tokens.get(key)
        if entry is None:
            return None
        token, expires_at = entry
        return None if expires_at is not None and time.time() >= expires_at else token

    def current(self, base_url: str, client_id: str) -> str | None:
        """A cached, unexpired token for this client, or None."""
        with self._guard:
            return self._fresh((base_url, client_id))

    def refresh(
        self, base_url: str, client_id: str, secret: str, *, stale_token: str
    ) -> tuple[str | None, str | None]:
        """Return ``(token, None)`` or ``(None, reason)``.

        ``stale_token`` is the token that just expired or got a 401; a cached token that
        differs from it was refreshed by another worker and is returned without a fetch.
        """
        key = (base_url, client_id)
        with self._guard:
            lock = self._locks.setdefault(key, threading.Lock())
        with lock:
            with self._guard:
                cached = self._fresh(key)
                failures = self._failures.get(key, 0)
            if cached is not None and cached != stale_token:
                return cached, None
            if failures >= _MAX_REFRESH_FAILURES:
                reason = self._last_error.get(key, "token fetch failed")
                return None, f"{reason} (gave up after {failures} failed refreshes)"
            if failures:
                time.sleep(_REFRESH_BACKOFF * 2 ** (failures - 1))
            got = fetch_token(base_url, client_id, secret, self._oauth_endpoint, self._backend)
            with self._guard:
                if got.token is not None:
                    self._tokens[key] = (got.token, got.expires_at)
                    self._failures[key] = 0
                    return got.token, None
                self._failures[key] = failures + 1
                self._last_error[key] = got.error or "token fetch failed"
                return None, self._last_error[key]
