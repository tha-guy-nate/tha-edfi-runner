from __future__ import annotations

from typing import Literal

from tha_req_runner.runner import ThaReq

from tha_edfi_runner.errors import EdfiError

Backend = Literal["requests", "httpx2"]
_BACKENDS = ("requests", "httpx2")


def make_req(backend: Backend) -> ThaReq:
    """Build a ThaReq for ``backend``, with an install hint if httpx2 is missing."""
    if backend not in _BACKENDS:
        raise EdfiError(f"backend must be one of {_BACKENDS}, got {backend!r}")
    try:
        return ThaReq(backend=backend)
    except ImportError as exc:
        raise ImportError(
            "The httpx2 backend needs the extra: pip install tha-edfi-runner[httpx2]"
        ) from exc
