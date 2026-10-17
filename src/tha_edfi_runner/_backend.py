from __future__ import annotations

import threading
from typing import Any, Literal, TypeVar

from tha_req_runner.runner import ThaReq

from tha_edfi_runner.errors import EdfiError

Backend = Literal["requests", "httpx2"]
_BACKENDS = ("requests", "httpx2")
_R = TypeVar("_R", bound=ThaReq)


class BatchReq(ThaReq):
    """A ThaReq shared by every row of one batch call.

    Sessions stay per thread (one per worker), so rows reuse connections instead of
    opening one per row. ``close_all`` closes every worker's session at the end; the
    plain ``close_session`` only reaches the calling thread's.
    """

    def __init__(self, *, backend: Backend = "requests") -> None:
        super().__init__(backend=backend)
        self._opened: list[Any] = []
        self._opened_lock = threading.Lock()

    def get_session(self, **kwargs: Any) -> Any:
        session = super().get_session(**kwargs)
        with self._opened_lock:
            if all(s is not session for s in self._opened):
                self._opened.append(session)
        return session

    def close_all(self) -> None:
        with self._opened_lock:
            sessions, self._opened = self._opened, []
        for session in sessions:
            try:
                session.close()
            except Exception:
                pass


def make_req(backend: Backend) -> ThaReq:
    """Build a ThaReq for ``backend``, with an install hint if httpx2 is missing."""
    return _build(ThaReq, backend)


def make_batch_req(backend: Backend) -> BatchReq:
    return _build(BatchReq, backend)


def _build(cls: type[_R], backend: Backend) -> _R:
    if backend not in _BACKENDS:
        raise EdfiError(f"backend must be one of {_BACKENDS}, got {backend!r}")
    try:
        return cls(backend=backend)
    except ImportError as exc:
        raise ImportError(
            "The httpx2 backend needs the extra: pip install tha-edfi-runner[httpx2]"
        ) from exc
