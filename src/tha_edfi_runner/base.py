from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from tha_req_runner.runner import ThaReq
from tqdm import tqdm

from tha_edfi_runner.auth import BearerAuth, OAuth2Auth
from tha_edfi_runner.errors import EdfiError


class ThaEdfiBase:
    """Base class for Ed-Fi resource runners. Holds base_url, auth, and a shared ThaReq instance."""

    def __init__(
        self,
        *,
        base_url: str = "",
        api_version: str = "",
        # OAuth2 client credentials
        client_id: str | None = None,
        client_secret: str | None = None,
        token_url: str | None = None,
        # Pre-provided bearer token
        bearer_token: str | None = None,
        # Basic auth (not yet implemented — placeholder)
        username: str | None = None,
        password: str | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_version = api_version.strip("/")
        self._data_url = (
            f"{self.base_url}/data/{self.api_version}" if self.api_version else self.base_url
        )
        self._req = ThaReq()
        self._auth: OAuth2Auth | BearerAuth | None = None

        if bearer_token is not None:
            self._auth = BearerAuth(token=bearer_token)
        elif client_id is not None and client_secret is not None:
            resolved_token_url = token_url or f"{self.base_url}/oauth/token"
            self._auth = OAuth2Auth(
                client_id=client_id,
                client_secret=client_secret,
                token_url=resolved_token_url,
            )
        elif username is not None or password is not None:
            raise EdfiError(
                "BasicAuth is not yet implemented. Use client_id/client_secret or bearer_token."
            )

    def _session(self) -> Any:
        """Return a thread-local session with the current auth token stamped on headers."""
        if self._auth is None:
            raise EdfiError(
                "No auth configured. Provide client_id + client_secret or bearer_token."
            )
        token = self._auth.get_token()
        session = self._req.get_session()
        session.headers["Authorization"] = f"Bearer {token}"
        return session

    def fetch_token(self) -> dict[str, Any]:
        """Fetch an OAuth token using this instance's credentials.

        Requires the instance to be configured with client_id + client_secret.
        Returns {"token": ..., "status": None/"error", "message": None/str}.
        """
        if self._auth is None:
            return {"token": None, "status": "error", "message": "No auth configured."}
        try:
            token = self._auth.get_token()
            return {"token": token, "status": None, "message": None}
        except EdfiError as exc:
            msg = "auth error: HTTP 401" if "401" in str(exc) else str(exc)
            return {"token": None, "status": "error", "message": msg}
        except Exception as exc:
            return {"token": None, "status": "error", "message": f"auth failed: {exc}"}

    def batch_fetch_tokens(
        self,
        rows: list[dict[str, Any]],
        *,
        oauth_endpoint: str,
        account_col: str,
        workers: int = 1,
        show_progress: bool = False,
        progress_desc: str | None = None,
        skip_statuses: list[str] | None = None,
        status_col: str = "row status",
        url_col: str = "targetUrl",
        key_col: str = "oAuthKey",
        secret_col: str = "oAuthSecret",
        token_col: str = "EdFi Token",
        expires_col: str = "token_expires_at",
    ) -> list[dict[str, Any]]:
        """Fetch OAuth tokens for each unique account via client credentials grant.

        Returns one record per unique account_col value carrying account_col,
        key_col, secret_col, and token_col. Use enrich_rows to stamp the token
        back onto rows.
        """
        effective_skip = skip_statuses if skip_statuses is not None else ["error", "warning"]
        valid_rows = [r for r in rows if r.get(status_col) not in effective_skip]

        seen_accounts: set[str] = set()
        deduped: list[dict[str, Any]] = []
        for row in valid_rows:
            account_val = (row.get(account_col) or "").strip()
            if account_val and account_val not in seen_accounts:
                seen_accounts.add(account_val)
                deduped.append(row)

        results: list[dict[str, Any]] = [{}] * len(deduped)

        def _fetch_one(idx: int, row: dict[str, Any]) -> tuple[int, dict[str, Any]]:
            account_val = (row.get(account_col) or "").strip()
            base_url = (row.get(url_col) or "").rstrip("/")
            key = (row.get(key_col) or "").strip()
            secret = (row.get(secret_col) or "").strip()
            base: dict[str, Any] = {account_col: account_val, key_col: key, secret_col: secret}

            if not base_url:
                return idx, {
                    **base,
                    token_col: None,
                    expires_col: None,
                    status_col: "error",
                    "message": f"missing {url_col!r}",
                }
            if not key:
                return idx, {
                    **base,
                    token_col: None,
                    expires_col: None,
                    status_col: "error",
                    "message": f"missing {key_col!r}",
                }
            if not secret:
                return idx, {
                    **base,
                    token_col: None,
                    expires_col: None,
                    status_col: "error",
                    "message": f"missing {secret_col!r}",
                }

            token_url = f"{base_url}/{oauth_endpoint.lstrip('/')}"
            instance = ThaEdfiBase(
                base_url=base_url,
                client_id=key,
                client_secret=secret,
                token_url=token_url,
            )
            result = instance.fetch_token()
            if result["status"] == "error":
                return idx, {
                    **base,
                    token_col: None,
                    expires_col: None,
                    status_col: "error",
                    "message": result["message"],
                }
            _exp = getattr(instance._auth, "expires_at", None)
            exp_val: float | None = _exp if isinstance(_exp, float) else None
            return idx, {
                **base,
                token_col: result["token"],
                expires_col: exp_val,
                status_col: None,
                "message": None,
            }

        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(_fetch_one, idx, row): idx for idx, row in enumerate(deduped)}
            futures_iter = (
                tqdm(
                    as_completed(futures),
                    total=len(futures),
                    desc=progress_desc or "fetching tokens",
                )
                if show_progress
                else as_completed(futures)
            )
            for future in futures_iter:
                idx, out = future.result()
                results[idx] = out

        self.rows = results
        return results
