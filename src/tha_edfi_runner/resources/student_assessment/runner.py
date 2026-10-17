from __future__ import annotations

import json
import time
from collections.abc import Collection, Mapping
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, cast

from tqdm import tqdm

from tha_edfi_runner import endpoints as ep
from tha_edfi_runner.base import ThaEdfiBase, _compose_label
from tha_edfi_runner.errors import EdfiError
from tha_edfi_runner.resources.student_assessment import api

_CORRELATION_HEADERS = ("correlation-id", "x-correlation-id")


def _is_401(result: dict[str, Any]) -> bool:
    # http_status also catches a 401 mapped through allow_statuses.
    if result.get("http_status") == 401:
        return True
    return result.get("status") == "error" and "401" in str(result.get("message", ""))


def _correlation_id(result: dict[str, Any]) -> str | None:
    """Ed-Fi ``correlationId`` from the error body, else a correlation-id header."""
    data = result.get("data")
    if isinstance(data, dict):
        cid = data.get("correlationId")
        if cid:
            return str(cid)
    headers = getattr(result.get("raw_response"), "headers", None)
    if headers is not None:
        for name in _CORRELATION_HEADERS:
            value = headers.get(name)
            if isinstance(value, str) and value:
                return value
    return None


def _validate_status_handling(max_retries: int, retry_backoff: float) -> None:
    if max_retries < 0:
        raise EdfiError(f"max_retries must be >= 0, got {max_retries}")
    if retry_backoff < 0:
        raise EdfiError(f"retry_backoff must be >= 0, got {retry_backoff}")


def _refetch_token(base_url: str, key: str, secret: str, oauth_endpoint: str | None) -> str | None:
    if not oauth_endpoint:
        return None
    token_url = f"{base_url}/{oauth_endpoint.lstrip('/')}"
    instance = ThaEdfiBase(
        base_url=base_url, client_id=key, client_secret=secret, token_url=token_url
    )
    result = instance.fetch_token()
    return result["token"] if result["status"] is None else None


def _error_msg(result: dict[str, Any]) -> str:
    if result.get("code") == 401:
        return "auth error: HTTP 401"
    return result.get("message") or f"HTTP {result.get('code')}"


def _post_error_msg(result: dict[str, Any]) -> str:
    msg = _error_msg(result)
    data = result.get("data")
    if result.get("code") != 401 and isinstance(data, dict):
        detail = data.get("message") or data.get("error") or data.get("detail") or ""
        detail = detail.strip() if isinstance(detail, str) else detail
        if detail:
            msg = f"{msg}: {detail}"
    return msg


def _should_retry(
    result: dict[str, Any],
    retry_statuses: Collection[int] | None,
    retry_messages: Collection[str] | None,
) -> bool:
    if result["status"] != "error" or not retry_statuses:
        return False
    if result.get("code") not in retry_statuses:
        return False
    if not retry_messages:
        return True
    msg = _post_error_msg(result).lower()
    return any(m.lower() in msg for m in retry_messages)


def _kept_rows_with_indices(
    rows: list[dict[str, Any]], status_col: str, skip: list[str]
) -> tuple[list[dict[str, Any]], list[int]]:
    """Drop rows whose ``status_col`` is in ``skip``, keeping each survivor paired
    with its original position in ``rows``.

    The positions become each result's ``row_index`` so it stays a stable
    correlation key even when rows are skipped.
    """
    valid_rows: list[dict[str, Any]] = []
    orig_indices: list[int] = []
    for orig_idx, row in enumerate(rows):
        if row.get(status_col) not in skip:
            valid_rows.append(row)
            orig_indices.append(orig_idx)
    return valid_rows, orig_indices


def _row_ids(rows: list[dict[str, Any]], col: str | None) -> list[str] | None:
    """Per-row values of the caller's identity column ``col``, for the ``row_id``
    result key. ``None`` when no column is configured. A missing value becomes
    ``""``; other values are stringified and stripped so an integer id survives.
    """
    if not col:
        return None
    return ["" if (v := row.get(col)) is None else str(v).strip() for row in rows]


class ThaStudentAssessment(ThaEdfiBase):
    """Ed-Fi student assessment resource runner."""

    def post_payload(
        self,
        payload: dict[str, Any] | str,
        *,
        key: str,
        endpoint: str = ep.STUDENT_ASSESSMENTS,
        commit: bool = False,
        retry_statuses: Collection[int] | None = None,
        retry_messages: Collection[str] | None = None,
        max_retries: int = 2,
        retry_backoff: float = 1.0,
        allow_statuses: Mapping[int, str | None] | None = None,
    ) -> dict[str, Any]:
        """POST a single payload to Ed-Fi.

        Returns {"key", "status", "message", "http_status", "correlation_id"}.
        ``http_status`` is ``None`` for client-side failures; ``correlation_id`` is set
        only on an Ed-Fi error.

        Opt-in: ``retry_statuses`` re-POSTs on those codes (up to ``max_retries``,
        exponential ``retry_backoff``), narrowed by ``retry_messages`` (case-insensitive
        substrings the Ed-Fi message must contain). A failed retry is ``"error"``.
        ``allow_statuses`` maps a code that wasn't retried to the row status used
        instead of ``"error"``, e.g. ``{400: "warning"}``.

        payload may be a dict or a JSON string.
        # TODO: support additional payload types (form data, raw bytes)
        """
        _validate_status_handling(max_retries, retry_backoff)
        if not commit:
            return {
                "key": key,
                "status": "dry_run",
                "message": None,
                "http_status": None,
                "correlation_id": None,
            }
        parsed_payload: dict[str, Any]
        if isinstance(payload, str):
            try:
                parsed_payload = json.loads(payload)
            except (json.JSONDecodeError, TypeError) as exc:
                return {
                    "key": key,
                    "status": "error",
                    "message": f"invalid JSON: {exc}",
                    "http_status": None,
                    "correlation_id": None,
                }
        else:
            parsed_payload = payload
        session = self._session()
        retries = 0
        while True:
            result = api.post_student_assessment(
                self._req, session, self._data_url, endpoint, parsed_payload
            )
            if retries >= max_retries or not _should_retry(result, retry_statuses, retry_messages):
                break
            time.sleep(retry_backoff * 2**retries)
            retries += 1
        http_status = result.get("code")
        if result["status"] == "error":
            msg = _post_error_msg(result)
            correlation_id = _correlation_id(result)
            if retry_statuses:
                notes = [f"retries attempted: {retries}"]
                if correlation_id:
                    notes.insert(0, f"correlation id: {correlation_id}")
                msg = f"{msg} ({'; '.join(notes)})"
            status: str | None = "error"
            # A retry-eligible failure stays "error"; allow_statuses covers the rest.
            if (
                not _should_retry(result, retry_statuses, retry_messages)
                and allow_statuses is not None
                and isinstance(http_status, int)
                and http_status in allow_statuses
            ):
                status = allow_statuses[http_status]
            return {
                "key": key,
                "status": status,
                "message": msg,
                "http_status": http_status,
                "correlation_id": correlation_id,
            }
        return {
            "key": key,
            "status": None,
            "message": None,
            "http_status": http_status,
            "correlation_id": None,
        }

    def batch_post_payload(
        self,
        rows: list[dict[str, Any]],
        *,
        payload_col: str,
        key_col: str,
        endpoint: str = ep.STUDENT_ASSESSMENTS,
        workers: int = 1,
        show_progress: bool = False,
        progress_desc: str | None = None,
        label: str | None = None,
        skip_statuses: list[str] | None = None,
        status_col: str = "row status",
        url_col: str = "targetUrl",
        token_col: str = "EdFi Token",
        api_version_col: str | None = None,
        auth_key_col: str | None = None,
        auth_secret_col: str | None = None,
        oauth_endpoint: str | None = None,
        expires_col: str | None = None,
        row_id_col: str | None = None,
        commit: bool = False,
        retry_statuses: Collection[int] | None = None,
        retry_messages: Collection[str] | None = None,
        max_retries: int = 2,
        retry_backoff: float = 1.0,
        allow_statuses: Mapping[int, str | None] | None = None,
    ) -> list[dict[str, Any]]:
        """POST each row's payload to Ed-Fi using per-row credentials.

        Each result carries ``row_index`` — the row's position in the caller's
        ``rows`` list, unchanged by any rows skipped via ``skip_statuses`` — as a
        collision-free correlation key (the business key in ``key`` can repeat when
        a caller posts several payloads for one account in a batch), plus
        ``http_status`` (the Ed-Fi response code, or ``None`` for client-side
        failures). Feed the results to ``ThaMap.enrich_rows`` keyed on ``row_index``.

        Pass ``row_id_col`` to also echo that column's value onto each result as
        ``row_id`` — a caller-owned identity (e.g. a key assigned upstream at CSV
        read) that survives the whole pipeline, independent of ``row_index`` and
        the business key. ``row_id`` is ``None`` on every result when ``row_id_col``
        is not set.

        Retry/allow options and ``correlation_id``: see ``post_payload``.
        """
        _validate_status_handling(max_retries, retry_backoff)
        effective_skip = skip_statuses if skip_statuses is not None else ["error", "warning"]
        valid_rows, orig_indices = _kept_rows_with_indices(rows, status_col, effective_skip)
        row_ids = _row_ids(valid_rows, row_id_col)

        if not commit:
            out = [
                {
                    "key": str(row.get(key_col) or "").strip(),
                    "status": "dry_run",
                    "message": None,
                    "http_status": None,
                    "correlation_id": None,
                    "row_index": orig_indices[pos],
                    "row_id": row_ids[pos] if row_ids is not None else None,
                }
                for pos, row in enumerate(valid_rows)
            ]
            self.rows = out
            return out

        results: list[dict[str, Any]] = cast(list[dict[str, Any]], [{}] * len(valid_rows))

        def _post(idx: int, row: dict[str, Any]) -> tuple[int, dict[str, Any]]:
            key_val = str(row.get(key_col) or "").strip()
            row_base_url = (row.get(url_col) or "").rstrip("/")
            row_token = (row.get(token_col) or "").strip()
            raw = row.get(payload_col)

            if not row_base_url:
                return idx, {"key": key_val, "status": "error", "message": f"missing {url_col!r}"}
            if not row_token:
                return idx, {"key": key_val, "status": "error", "message": f"missing {token_col!r}"}
            if raw is None:
                msg = f"missing column: {payload_col!r}"
                return idx, {"key": key_val, "status": "error", "message": msg}

            # TODO: support additional payload types (form data, raw bytes)
            if isinstance(raw, str):
                try:
                    payload: dict[str, Any] = json.loads(raw)
                except (json.JSONDecodeError, TypeError) as exc:
                    msg = f"invalid JSON: {exc}"
                    return idx, {"key": key_val, "status": "error", "message": msg}
            else:
                payload = raw

            row_api_version = (row.get(api_version_col) or "").strip() if api_version_col else ""
            effective_api_version = row_api_version or self.api_version
            auth_key = (row.get(auth_key_col) or "").strip() if auth_key_col else ""
            auth_secret = (row.get(auth_secret_col) or "").strip() if auth_secret_col else ""
            can_reauth = bool(
                auth_key_col and auth_secret_col and oauth_endpoint and auth_key and auth_secret
            )

            if can_reauth and expires_col:
                exp = row.get(expires_col)
                if isinstance(exp, (int, float)) and time.time() >= exp:
                    fresh = _refetch_token(row_base_url, auth_key, auth_secret, oauth_endpoint)
                    if fresh:
                        row_token = fresh

            instance = ThaStudentAssessment(
                base_url=row_base_url, bearer_token=row_token, api_version=effective_api_version
            )
            status_handling: dict[str, Any] = {
                "retry_statuses": retry_statuses,
                "retry_messages": retry_messages,
                "max_retries": max_retries,
                "retry_backoff": retry_backoff,
                "allow_statuses": allow_statuses,
            }
            result = instance.post_payload(
                payload, key=key_val, endpoint=endpoint, commit=True, **status_handling
            )

            if can_reauth and _is_401(result):
                fresh = _refetch_token(row_base_url, auth_key, auth_secret, oauth_endpoint)
                if fresh:
                    instance = ThaStudentAssessment(
                        base_url=row_base_url, bearer_token=fresh, api_version=effective_api_version
                    )
                    result = instance.post_payload(
                        payload, key=key_val, endpoint=endpoint, commit=True, **status_handling
                    )

            return idx, result

        _label = _compose_label(progress_desc, label, "posting payloads")
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(_post, idx, row): idx for idx, row in enumerate(valid_rows)}
            futures_iter = (
                tqdm(
                    as_completed(futures),
                    total=len(futures),
                    desc=_label,
                )
                if show_progress
                else as_completed(futures)
            )
            for future in futures_iter:
                idx, res = cast(tuple[int, dict[str, Any]], future.result())
                res["row_index"] = orig_indices[idx]
                res["row_id"] = row_ids[idx] if row_ids is not None else None
                res.setdefault("http_status", None)
                res.setdefault("correlation_id", None)
                results[idx] = res

        self.rows = results
        return results

    def get_by_id(
        self,
        resource_id: str,
        *,
        endpoint: str = ep.STUDENT_ASSESSMENTS,
    ) -> dict[str, Any]:
        """Fetch a single student assessment by Ed-Fi resource ID.

        ``http_status`` carries the HTTP response code (e.g. 200, 404, 401).
        """
        session = self._session()
        result = api.get_student_assessment_by_id(
            self._req, session, self._data_url, endpoint, resource_id
        )
        http_status = result.get("code")
        if result["code"] == 404:
            return {
                "id": resource_id,
                "status": "error",
                "message": "not found",
                "data": None,
                "http_status": http_status,
                "correlation_id": _correlation_id(result),
            }
        if result["status"] == "error":
            return {
                "id": resource_id,
                "status": "error",
                "message": _error_msg(result),
                "data": None,
                "http_status": http_status,
                "correlation_id": _correlation_id(result),
            }
        return {
            "id": resource_id,
            "status": None,
            "message": None,
            "data": result["data"],
            "http_status": http_status,
            "correlation_id": None,
        }

    def batch_get_by_id(
        self,
        rows: list[dict[str, Any]],
        *,
        id_col: str,
        endpoint: str = ep.STUDENT_ASSESSMENTS,
        workers: int = 1,
        show_progress: bool = False,
        progress_desc: str | None = None,
        label: str | None = None,
        skip_statuses: list[str] | None = None,
        status_col: str = "row status",
        url_col: str = "targetUrl",
        token_col: str = "EdFi Token",
        api_version_col: str | None = None,
        auth_key_col: str | None = None,
        auth_secret_col: str | None = None,
        oauth_endpoint: str | None = None,
        expires_col: str | None = None,
        row_id_col: str | None = None,
    ) -> list[dict[str, Any]]:
        """GET each row's student assessment by ID using per-row credentials.

        Each result carries ``row_index`` — the row's position in the caller's
        ``rows`` list, unchanged by any rows skipped via ``skip_statuses`` — as a
        collision-free correlation key, plus ``http_status`` (the Ed-Fi response
        code, or ``None`` for client-side failures).

        Pass ``row_id_col`` to also echo that column's value onto each result as
        ``row_id`` — a caller-owned identity that survives the whole pipeline,
        independent of ``row_index``. ``row_id`` is ``None`` on every result when
        ``row_id_col`` is not set.
        """
        effective_skip = skip_statuses if skip_statuses is not None else ["error", "warning"]
        valid_rows, orig_indices = _kept_rows_with_indices(rows, status_col, effective_skip)
        row_ids = _row_ids(valid_rows, row_id_col)

        results: list[dict[str, Any]] = cast(list[dict[str, Any]], [{}] * len(valid_rows))

        def _get(idx: int, row: dict[str, Any]) -> tuple[int, dict[str, Any]]:
            resource_id = str(row.get(id_col) or "").strip()
            if not resource_id:
                msg = f"missing column: {id_col!r}"
                return idx, {"id": None, "status": "error", "message": msg, "data": None}
            row_base_url = (row.get(url_col) or "").rstrip("/")
            row_token = (row.get(token_col) or "").strip()
            if not row_base_url:
                msg = f"missing {url_col!r}"
                return idx, {"id": resource_id, "status": "error", "message": msg, "data": None}
            if not row_token:
                msg = f"missing {token_col!r}"
                return idx, {"id": resource_id, "status": "error", "message": msg, "data": None}

            row_api_version = (row.get(api_version_col) or "").strip() if api_version_col else ""
            effective_api_version = row_api_version or self.api_version
            auth_key = (row.get(auth_key_col) or "").strip() if auth_key_col else ""
            auth_secret = (row.get(auth_secret_col) or "").strip() if auth_secret_col else ""
            can_reauth = bool(
                auth_key_col and auth_secret_col and oauth_endpoint and auth_key and auth_secret
            )

            if can_reauth and expires_col:
                exp = row.get(expires_col)
                if isinstance(exp, (int, float)) and time.time() >= exp:
                    fresh = _refetch_token(row_base_url, auth_key, auth_secret, oauth_endpoint)
                    if fresh:
                        row_token = fresh

            instance = ThaStudentAssessment(
                base_url=row_base_url, bearer_token=row_token, api_version=effective_api_version
            )
            result = instance.get_by_id(resource_id, endpoint=endpoint)

            if can_reauth and _is_401(result):
                fresh = _refetch_token(row_base_url, auth_key, auth_secret, oauth_endpoint)
                if fresh:
                    instance = ThaStudentAssessment(
                        base_url=row_base_url, bearer_token=fresh, api_version=effective_api_version
                    )
                    result = instance.get_by_id(resource_id, endpoint=endpoint)

            return idx, result

        _label = _compose_label(progress_desc, label, "fetching by id")
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(_get, idx, row): idx for idx, row in enumerate(valid_rows)}
            futures_iter = (
                tqdm(
                    as_completed(futures),
                    total=len(futures),
                    desc=_label,
                )
                if show_progress
                else as_completed(futures)
            )
            for future in futures_iter:
                idx, res = cast(tuple[int, dict[str, Any]], future.result())
                res["row_index"] = orig_indices[idx]
                res["row_id"] = row_ids[idx] if row_ids is not None else None
                res.setdefault("http_status", None)
                res.setdefault("correlation_id", None)
                results[idx] = res

        self.rows = results
        return results

    def get_all(
        self,
        *,
        key: str,
        endpoint: str = ep.STUDENT_ASSESSMENTS,
        params: dict[str, Any] | None = None,
        limit: int = 500,
        show_progress: bool = False,
        progress_desc: str | None = None,
        label: str | None = None,
    ) -> dict[str, Any]:
        """Fetch all student assessments for one account, auto-paginating.

        Returns {"key", "status", "message", "data", "correlation_id"}.
        Use batch_get_all to fan out across many accounts with per-row credentials.
        """
        all_items: list[dict[str, Any]] = []
        offset = 0

        _action = "fetching student assessments"
        _label = _compose_label(progress_desc, label, _action)
        progress = tqdm(desc=_label, unit=" items") if show_progress else None

        while True:
            session = self._session()
            result = api.get_student_assessments(
                self._req,
                session,
                self._data_url,
                endpoint,
                offset=offset,
                limit=limit,
                params=params,
            )
            if result["status"] == "error":
                if progress is not None:
                    progress.close()
                if result.get("code") == 401:
                    msg = "auth error: HTTP 401"
                else:
                    msg = f"GET {endpoint} failed: {result['message']} (code {result['code']})"
                return {
                    "key": key,
                    "status": "error",
                    "message": msg,
                    "data": None,
                    "correlation_id": _correlation_id(result),
                }
            page: list[dict[str, Any]] = result["data"] or []
            all_items.extend(page)
            if progress is not None:
                progress.update(len(page))
            if len(page) < limit:
                break
            offset += limit

        if progress is not None:
            progress.close()

        self.rows = all_items
        return {
            "key": key,
            "status": None,
            "message": None,
            "data": all_items,
            "correlation_id": None,
        }

    def batch_get_all(
        self,
        rows: list[dict[str, Any]],
        *,
        key_col: str,
        endpoint: str = ep.STUDENT_ASSESSMENTS,
        params: dict[str, Any] | None = None,
        limit: int = 500,
        workers: int = 1,
        show_progress: bool = False,
        progress_desc: str | None = None,
        label: str | None = None,
        skip_statuses: list[str] | None = None,
        status_col: str = "row status",
        url_col: str = "targetUrl",
        token_col: str = "EdFi Token",
        api_version_col: str | None = None,
        auth_key_col: str | None = None,
        auth_secret_col: str | None = None,
        oauth_endpoint: str | None = None,
        expires_col: str | None = None,
    ) -> list[dict[str, Any]]:
        """Fetch all student assessments across multiple accounts, returning a flat list.

        Injects key_col into each record. Deduplicates by key_col. Pass results as source
        to expand_rows to join back to rows.
        Error accounts contribute a single {key_col, status, message} placeholder to the flat list.
        """
        effective_skip = skip_statuses if skip_statuses is not None else ["error", "warning"]
        valid_rows = [r for r in rows if r.get(status_col) not in effective_skip]

        seen: set[str] = set()
        deduped: list[dict[str, Any]] = []
        for row in valid_rows:
            key_val = str(row.get(key_col) or "").strip()
            if key_val and key_val not in seen:
                seen.add(key_val)
                deduped.append(row)

        account_results: list[dict[str, Any]] = cast(list[dict[str, Any]], [{}] * len(deduped))

        def _fetch(idx: int, row: dict[str, Any]) -> tuple[int, dict[str, Any]]:
            key_val = str(row.get(key_col) or "").strip()
            row_base_url = (row.get(url_col) or "").rstrip("/")
            row_token = (row.get(token_col) or "").strip()
            if not row_base_url:
                msg = f"missing {url_col!r}"
                return idx, {"key": key_val, "status": "error", "message": msg, "data": None}
            if not row_token:
                msg = f"missing {token_col!r}"
                return idx, {"key": key_val, "status": "error", "message": msg, "data": None}

            row_api_version = (row.get(api_version_col) or "").strip() if api_version_col else ""
            effective_api_version = row_api_version or self.api_version
            auth_key = (row.get(auth_key_col) or "").strip() if auth_key_col else ""
            auth_secret = (row.get(auth_secret_col) or "").strip() if auth_secret_col else ""
            can_reauth = bool(
                auth_key_col and auth_secret_col and oauth_endpoint and auth_key and auth_secret
            )

            if can_reauth and expires_col:
                exp = row.get(expires_col)
                if isinstance(exp, (int, float)) and time.time() >= exp:
                    fresh = _refetch_token(row_base_url, auth_key, auth_secret, oauth_endpoint)
                    if fresh:
                        row_token = fresh

            instance = ThaStudentAssessment(
                base_url=row_base_url, bearer_token=row_token, api_version=effective_api_version
            )
            result = instance.get_all(key=key_val, endpoint=endpoint, params=params, limit=limit)

            if can_reauth and _is_401(result):
                fresh = _refetch_token(row_base_url, auth_key, auth_secret, oauth_endpoint)
                if fresh:
                    instance = ThaStudentAssessment(
                        base_url=row_base_url, bearer_token=fresh, api_version=effective_api_version
                    )
                    result = instance.get_all(
                        key=key_val, endpoint=endpoint, params=params, limit=limit
                    )

            return idx, result

        _action = "fetching all student assessments"
        _label = _compose_label(progress_desc, label, _action)
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(_fetch, idx, row): idx for idx, row in enumerate(deduped)}
            futures_iter = (
                tqdm(
                    as_completed(futures),
                    total=len(futures),
                    desc=_label,
                )
                if show_progress
                else as_completed(futures)
            )
            for future in futures_iter:
                idx, out = future.result()
                account_results[idx] = out

        flat: list[dict[str, Any]] = []
        for result in account_results:
            key_val = result.get("key", "")
            if result.get("status") == "error":
                flat.append(
                    {
                        key_col: key_val,
                        "status": "error",
                        "message": result.get("message"),
                        "correlation_id": result.get("correlation_id"),
                    }
                )
            else:
                for record in result.get("data") or []:
                    record[key_col] = key_val
                    flat.append(record)

        self.rows = flat
        return flat

    def delete_by_id(
        self,
        resource_id: str,
        *,
        key: str,
        endpoint: str = ep.STUDENT_ASSESSMENTS,
        commit: bool = False,
    ) -> dict[str, Any]:
        """Delete a single student assessment by Ed-Fi resource ID.

        ``http_status`` carries the HTTP response code (e.g. 204, 404, 401), or
        ``None`` on a dry run.
        """
        if not commit:
            return {
                "id": resource_id,
                "key": key,
                "status": "dry_run",
                "message": None,
                "http_status": None,
                "correlation_id": None,
            }
        session = self._session()
        result = api.delete_student_assessment(
            self._req, session, self._data_url, endpoint, resource_id
        )
        http_status = result.get("code")
        if result["status"] == "error":
            msg = result["message"] or f"HTTP {result['code']}"
            return {
                "id": resource_id,
                "key": key,
                "status": "error",
                "message": msg,
                "http_status": http_status,
                "correlation_id": _correlation_id(result),
            }
        return {
            "id": resource_id,
            "key": key,
            "status": "deleted",
            "message": None,
            "http_status": http_status,
            "correlation_id": None,
        }

    def batch_delete_by_id(
        self,
        rows: list[dict[str, Any]],
        *,
        id_col: str,
        key_col: str,
        endpoint: str = ep.STUDENT_ASSESSMENTS,
        workers: int = 1,
        show_progress: bool = False,
        progress_desc: str | None = None,
        label: str | None = None,
        skip_statuses: list[str] | None = None,
        status_col: str = "row status",
        url_col: str = "targetUrl",
        token_col: str = "EdFi Token",
        api_version_col: str | None = None,
        auth_key_col: str | None = None,
        auth_secret_col: str | None = None,
        oauth_endpoint: str | None = None,
        expires_col: str | None = None,
        row_id_col: str | None = None,
        commit: bool = False,
    ) -> list[dict[str, Any]]:
        """DELETE each row's student assessment by ID using per-row credentials.

        Each result carries ``row_index`` — the row's position in the caller's
        ``rows`` list, unchanged by any rows skipped via ``skip_statuses`` — as a
        collision-free correlation key, plus ``http_status`` (the Ed-Fi response
        code, or ``None`` for client-side failures / dry runs).

        Pass ``row_id_col`` to also echo that column's value onto each result as
        ``row_id`` — a caller-owned identity that survives the whole pipeline,
        independent of ``row_index``. ``row_id`` is ``None`` on every result when
        ``row_id_col`` is not set.
        """
        effective_skip = skip_statuses if skip_statuses is not None else ["error", "warning"]
        valid_rows, orig_indices = _kept_rows_with_indices(rows, status_col, effective_skip)
        row_ids = _row_ids(valid_rows, row_id_col)

        if not commit:
            out = [
                {
                    "id": str(row.get(id_col) or "").strip(),
                    "key": str(row.get(key_col) or "").strip(),
                    "status": "dry_run",
                    "message": None,
                    "http_status": None,
                    "correlation_id": None,
                    "row_index": orig_indices[pos],
                    "row_id": row_ids[pos] if row_ids is not None else None,
                }
                for pos, row in enumerate(valid_rows)
            ]
            self.rows = out
            return out

        results: list[dict[str, Any]] = cast(list[dict[str, Any]], [{}] * len(valid_rows))

        def _delete(idx: int, row: dict[str, Any]) -> tuple[int, dict[str, Any]]:
            resource_id = str(row.get(id_col) or "").strip()
            key_val = str(row.get(key_col) or "").strip()
            if not resource_id:
                msg = f"missing column: {id_col!r}"
                return idx, {"id": None, "key": key_val, "status": "error", "message": msg}
            row_base_url = (row.get(url_col) or "").rstrip("/")
            row_token = (row.get(token_col) or "").strip()
            if not row_base_url:
                msg = f"missing {url_col!r}"
                return idx, {"id": resource_id, "key": key_val, "status": "error", "message": msg}
            if not row_token:
                msg = f"missing {token_col!r}"
                return idx, {"id": resource_id, "key": key_val, "status": "error", "message": msg}

            row_api_version = (row.get(api_version_col) or "").strip() if api_version_col else ""
            effective_api_version = row_api_version or self.api_version
            auth_key = (row.get(auth_key_col) or "").strip() if auth_key_col else ""
            auth_secret = (row.get(auth_secret_col) or "").strip() if auth_secret_col else ""
            can_reauth = bool(
                auth_key_col and auth_secret_col and oauth_endpoint and auth_key and auth_secret
            )

            if can_reauth and expires_col:
                exp = row.get(expires_col)
                if isinstance(exp, (int, float)) and time.time() >= exp:
                    fresh = _refetch_token(row_base_url, auth_key, auth_secret, oauth_endpoint)
                    if fresh:
                        row_token = fresh

            instance = ThaStudentAssessment(
                base_url=row_base_url, bearer_token=row_token, api_version=effective_api_version
            )
            result = instance.delete_by_id(resource_id, key=key_val, endpoint=endpoint, commit=True)

            if can_reauth and _is_401(result):
                fresh = _refetch_token(row_base_url, auth_key, auth_secret, oauth_endpoint)
                if fresh:
                    instance = ThaStudentAssessment(
                        base_url=row_base_url, bearer_token=fresh, api_version=effective_api_version
                    )
                    result = instance.delete_by_id(
                        resource_id, key=key_val, endpoint=endpoint, commit=True
                    )

            return idx, result

        _label = _compose_label(progress_desc, label, "deleting by id")
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(_delete, idx, row): idx for idx, row in enumerate(valid_rows)}
            futures_iter = (
                tqdm(
                    as_completed(futures),
                    total=len(futures),
                    desc=_label,
                )
                if show_progress
                else as_completed(futures)
            )
            for future in futures_iter:
                idx, res = cast(tuple[int, dict[str, Any]], future.result())
                res["row_index"] = orig_indices[idx]
                res["row_id"] = row_ids[idx] if row_ids is not None else None
                res.setdefault("http_status", None)
                res.setdefault("correlation_id", None)
                results[idx] = res

        self.rows = results
        return results
