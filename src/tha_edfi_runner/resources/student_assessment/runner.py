from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, cast

from tqdm import tqdm

from tha_edfi_runner import endpoints as ep
from tha_edfi_runner.base import ThaEdfiBase
from tha_edfi_runner.resources.student_assessment import api


def _is_401(result: dict[str, Any]) -> bool:
    return result.get("status") == "error" and "401" in str(result.get("message", ""))


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


class ThaStudentAssessment(ThaEdfiBase):
    """Ed-Fi student assessment resource runner."""

    def post_payload(
        self,
        payload: dict[str, Any] | str,
        *,
        key: str,
        endpoint: str = ep.STUDENT_ASSESSMENTS,
        commit: bool = False,
    ) -> dict[str, Any]:
        """POST a single payload to Ed-Fi. Returns {"key", "status", "message"}.

        payload may be a dict or a JSON string.
        # TODO: support additional payload types (form data, raw bytes)
        """
        if not commit:
            return {"key": key, "status": "dry_run", "message": None}
        parsed_payload: dict[str, Any]
        if isinstance(payload, str):
            try:
                parsed_payload = json.loads(payload)
            except (json.JSONDecodeError, TypeError) as exc:
                return {"key": key, "status": "error", "message": f"invalid JSON: {exc}"}
        else:
            parsed_payload = payload
        session = self._session()
        result = api.post_student_assessment(
            self._req, session, self._data_url, endpoint, parsed_payload
        )
        if result["status"] == "error":
            msg = _error_msg(result)
            if result.get("code") != 401 and isinstance(result.get("data"), dict):
                detail = result["data"].get("message") or result["data"].get("error") or ""
                if detail:
                    msg = f"{msg}: {detail}"
            return {"key": key, "status": "error", "message": msg}
        return {"key": key, "status": None, "message": None}

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
        skip_statuses: list[str] | None = None,
        status_col: str = "row status",
        url_col: str = "targetUrl",
        token_col: str = "EdFi Token",
        api_version_col: str | None = None,
        auth_key_col: str | None = None,
        auth_secret_col: str | None = None,
        oauth_endpoint: str | None = None,
        expires_col: str | None = None,
        commit: bool = False,
    ) -> list[dict[str, Any]]:
        """POST each row's payload to Ed-Fi using per-row credentials."""
        effective_skip = skip_statuses if skip_statuses is not None else ["error", "warning"]
        valid_rows = [r for r in rows if r.get(status_col) not in effective_skip]

        if not commit:
            out = [
                {"key": str(row.get(key_col) or "").strip(), "status": "dry_run", "message": None}
                for row in valid_rows
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
            result = instance.post_payload(payload, key=key_val, endpoint=endpoint, commit=True)

            if can_reauth and _is_401(result):
                fresh = _refetch_token(row_base_url, auth_key, auth_secret, oauth_endpoint)
                if fresh:
                    instance = ThaStudentAssessment(
                        base_url=row_base_url, bearer_token=fresh, api_version=effective_api_version
                    )
                    result = instance.post_payload(
                        payload, key=key_val, endpoint=endpoint, commit=True
                    )

            return idx, result

        _label = f"{progress_desc}: posting payloads" if progress_desc else "posting payloads"
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
                idx, out = cast(tuple[int, dict[str, Any]], future.result())  # type: ignore[assignment]
                results[idx] = out  # type: ignore[call-overload]

        self.rows = results
        return results

    def get_by_id(
        self,
        resource_id: str,
        *,
        endpoint: str = ep.STUDENT_ASSESSMENTS,
    ) -> dict[str, Any]:
        """Fetch a single student assessment by Ed-Fi resource ID."""
        session = self._session()
        result = api.get_student_assessment_by_id(
            self._req, session, self._data_url, endpoint, resource_id
        )
        if result["code"] == 404:
            return {"id": resource_id, "status": "error", "message": "not found", "data": None}
        if result["status"] == "error":
            return {
                "id": resource_id,
                "status": "error",
                "message": _error_msg(result),
                "data": None,
            }
        return {"id": resource_id, "status": None, "message": None, "data": result["data"]}

    def batch_get_by_id(
        self,
        rows: list[dict[str, Any]],
        *,
        id_col: str,
        endpoint: str = ep.STUDENT_ASSESSMENTS,
        workers: int = 1,
        show_progress: bool = False,
        progress_desc: str | None = None,
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
        """GET each row's student assessment by ID using per-row credentials."""
        effective_skip = skip_statuses if skip_statuses is not None else ["error", "warning"]
        valid_rows = [r for r in rows if r.get(status_col) not in effective_skip]

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

        _label = f"{progress_desc}: fetching by id" if progress_desc else "fetching by id"
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
                idx, out = cast(tuple[int, dict[str, Any]], future.result())
                results[idx] = out

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
    ) -> dict[str, Any]:
        """Fetch all student assessments for one account, auto-paginating.

        Returns {"key": key, "status": None/"error", "message": ..., "data": [...]}.
        Use batch_get_all to fan out across many accounts with per-row credentials.
        """
        all_items: list[dict[str, Any]] = []
        offset = 0

        _action = "fetching student assessments"
        _label = f"{progress_desc}: {_action}" if progress_desc else _action
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
                return {"key": key, "status": "error", "message": msg, "data": None}
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
        return {"key": key, "status": None, "message": None, "data": all_items}

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
        _label = f"{progress_desc}: {_action}" if progress_desc else _action
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
                flat.append({key_col: key_val, "status": "error", "message": result.get("message")})
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
        """Delete a single student assessment by Ed-Fi resource ID."""
        if not commit:
            return {"id": resource_id, "key": key, "status": "dry_run", "message": None}
        session = self._session()
        result = api.delete_student_assessment(
            self._req, session, self._data_url, endpoint, resource_id
        )
        if result["status"] == "error":
            msg = result["message"] or f"HTTP {result['code']}"
            return {"id": resource_id, "key": key, "status": "error", "message": msg}
        return {"id": resource_id, "key": key, "status": "deleted", "message": None}

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
        skip_statuses: list[str] | None = None,
        status_col: str = "row status",
        url_col: str = "targetUrl",
        token_col: str = "EdFi Token",
        api_version_col: str | None = None,
        auth_key_col: str | None = None,
        auth_secret_col: str | None = None,
        oauth_endpoint: str | None = None,
        expires_col: str | None = None,
        commit: bool = False,
    ) -> list[dict[str, Any]]:
        """DELETE each row's student assessment by ID using per-row credentials."""
        effective_skip = skip_statuses if skip_statuses is not None else ["error", "warning"]
        valid_rows = [r for r in rows if r.get(status_col) not in effective_skip]

        if not commit:
            out = [
                {
                    "id": str(row.get(id_col) or "").strip(),
                    "key": str(row.get(key_col) or "").strip(),
                    "status": "dry_run",
                    "message": None,
                }
                for row in valid_rows
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

        _label = f"{progress_desc}: deleting by id" if progress_desc else "deleting by id"
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
                idx, out = cast(tuple[int, dict[str, Any]], future.result())  # type: ignore[assignment]
                results[idx] = out  # type: ignore[call-overload]

        self.rows = results
        return results
