from __future__ import annotations

from typing import Any

from tha_req_runner.runner import ThaReq


def post_student_assessment(
    req: ThaReq,
    session: Any,
    data_url: str,
    endpoint: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    return req.safe_call(session.post, f"{data_url}{endpoint}", json=payload)


def get_student_assessment_by_id(
    req: ThaReq,
    session: Any,
    data_url: str,
    endpoint: str,
    resource_id: str,
) -> dict[str, Any]:
    return req.safe_call(session.get, f"{data_url}{endpoint}/{resource_id}")


def get_student_assessments(
    req: ThaReq,
    session: Any,
    data_url: str,
    endpoint: str,
    *,
    offset: int = 0,
    limit: int = 500,
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    query: dict[str, Any] = {"offset": offset, "limit": limit}
    if params:
        query.update(params)
    return req.safe_call(session.get, f"{data_url}{endpoint}", params=query)


def delete_student_assessment(
    req: ThaReq,
    session: Any,
    data_url: str,
    endpoint: str,
    resource_id: str,
) -> dict[str, Any]:
    return req.safe_call(session.delete, f"{data_url}{endpoint}/{resource_id}")
