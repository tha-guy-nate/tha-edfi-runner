# tha-edfi-runner

[![CI](https://github.com/tha-guy-nate/tha-edfi-runner/actions/workflows/ci.yml/badge.svg)](https://github.com/tha-guy-nate/tha-edfi-runner/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/tha-edfi-runner)](https://pypi.org/project/tha-edfi-runner/)
[![Python](https://img.shields.io/pypi/pyversions/tha-edfi-runner)](https://pypi.org/project/tha-edfi-runner/)
[![pre-commit](https://img.shields.io/badge/pre--commit-enabled-brightgreen?logo=pre-commit)](https://github.com/pre-commit/pre-commit)

A Tabular Helper API library that wraps the Ed-Fi ODS REST API with a typed, pipeline-friendly interface.

## Install

```bash
pip install tha-edfi-runner
```

## Quick start

```python
from tha_edfi_runner import ThaEdfiBase, ThaStudentAssessment

# Fetch tokens for each district
base = ThaEdfiBase()
token_rows = base.batch_fetch_tokens(
    district_rows,
    oauth_endpoint="oauth/token",
    account_col="District BK",
)

# POST student assessment payloads
runner = ThaStudentAssessment(api_version="v3")
results = runner.batch_post_payload(
    rows,
    payload_col="payload",
    key_col="Student Assessment BK",
    workers=4,
    commit=True,
)
```

## Auth

Three auth modes are supported — pass exactly one set of credentials:

```python
# OAuth2 client credentials (fetches a token automatically)
ThaEdfiBase(base_url="...", client_id="key", client_secret="secret")

# Pre-fetched bearer token
ThaEdfiBase(base_url="...", bearer_token="eyJ...")

# No auth (valid for ThaEdfiBase construction; _session() will raise)
ThaEdfiBase(base_url="...")
```

By default, `token_url` is derived as `{base_url}/oauth/token`. Override with `token_url=`.

## URL structure

Ed-Fi ODS data endpoints follow the pattern:

```
{base_url}/data/{api_version}/ed-fi/{resource}
```

Pass `api_version` once and the library handles the rest:

```python
runner = ThaStudentAssessment(base_url="https://ods.example.com/api", api_version="v3")
# data calls hit: https://ods.example.com/api/data/v3/ed-fi/studentAssessments
# OAuth token:    https://ods.example.com/api/oauth/token
```

## Multi-district deployments

When each row carries its own URL and token, pass `base_url` and `api_version` at the runner level as defaults, then override per-row via column names:

```python
runner = ThaStudentAssessment(api_version="v3")   # default version
results = runner.batch_get_all(
    rows,
    key_col="District BK",
    url_col="targetUrl",          # per-row ODS base URL
    token_col="EdFi Token",       # per-row bearer token
    api_version_col="apiVersion", # per-row override (takes precedence over "v3")
)
```

## Typical workflow

```python
from tha_edfi_runner import ThaEdfiBase, ThaStudentAssessment
from tha_map_runner import ThaMap

# 1. Fetch tokens for each district
base = ThaEdfiBase()
token_rows = base.batch_fetch_tokens(
    district_rows,
    oauth_endpoint="oauth/token",
    account_col="District BK",
    workers=4,
)

# 2. Enrich district rows with tokens
mapper = ThaMap()
enriched = mapper.enrich_rows(
    district_rows,
    source=token_rows,
    mapping={"EdFi Token": "EdFi Token", "token_expires_at": "token_expires_at"},
    row_key="District BK",
    source_key="District BK",
)

# 3. Fetch all assessments across districts
runner = ThaStudentAssessment(api_version="v3")
flat_assessments = runner.batch_get_all(
    enriched,
    key_col="District BK",
    workers=4,
    show_progress=True,
)

# 4. Join back to original rows
enriched = mapper.expand_rows(
    district_rows,
    source=flat_assessments,
    mapping={"Assessment ID": "id", "Score": "scoreResults.result"},
    row_key="District BK",
    source_key="District BK",
)
```

## API

### `ThaEdfiBase`

```python
ThaEdfiBase(
    *,
    base_url="",
    api_version="",
    client_id=None,
    client_secret=None,
    token_url=None,
    bearer_token=None,
)
```

#### `base.fetch_token()`

Fetch a token using this instance's OAuth2 credentials. Returns:

```python
{"token": "eyJ...", "status": None, "message": None}           # success
{"token": None, "status": "error", "message": "auth error: HTTP 401"}  # failure
```

#### `base.batch_fetch_tokens()`

```python
base.batch_fetch_tokens(
    rows,
    *,
    oauth_endpoint,                    # e.g. "oauth/token"
    account_col,                       # deduplication key
    workers=1,
    show_progress=False,
    progress_desc=None,
    skip_statuses=["error", "warning"],
    status_col="row status",
    url_col="targetUrl",
    key_col="oAuthKey",
    secret_col="oAuthSecret",
    token_col="EdFi Token",
    expires_col="token_expires_at",
) -> list[dict]
```

Returns one record per unique `account_col` value with `token_col` and `expires_col` set. Results stored in `base.rows`.

---

### `ThaStudentAssessment`

Inherits `ThaEdfiBase`. Default endpoint: `ed-fi/studentAssessments`.

#### Single methods

```python
runner.post_payload(payload, *, key, endpoint=..., commit=False) -> dict
# {"key": ..., "status": None | "error" | "dry_run", "message": ...}

runner.get_by_id(resource_id, *, endpoint=...) -> dict
# {"id": ..., "status": None | "error", "message": ..., "data": dict | None}

runner.get_all(*, key, endpoint=..., params=None, limit=500, show_progress=False) -> dict
# {"key": ..., "status": None | "error", "message": ..., "data": [dict, ...]}

runner.delete_by_id(resource_id, *, key, endpoint=..., commit=False) -> dict
# {"id": ..., "key": ..., "status": "deleted" | "error" | "dry_run", "message": ...}
```

All write methods require `commit=True` to execute — otherwise return `status="dry_run"`.

#### Batch methods

All batch methods share these common parameters:

```python
rows,
*,
endpoint=...,
workers=1,
show_progress=False,
progress_desc=None,
skip_statuses=["error", "warning"],
status_col="row status",
url_col="targetUrl",
token_col="EdFi Token",
api_version_col=None,           # column holding per-row ODS version (overrides runner api_version)
auth_key_col=None,              # enable reactive re-auth on 401
auth_secret_col=None,
oauth_endpoint=None,
expires_col=None,               # enable proactive re-auth before token expiry
```

Method-specific required params:

```python
runner.batch_post_payload(rows, *, payload_col, key_col, ..., commit=False) -> list[dict]
runner.batch_get_by_id(rows, *, id_col, ...) -> list[dict]
runner.batch_get_all(rows, *, key_col, ...) -> list[dict]   # flat list; inject key_col into each record
runner.batch_delete_by_id(rows, *, id_col, key_col, ..., commit=False) -> list[dict]
```

Results stored in `runner.rows`.

### Re-auth

Provide `auth_key_col`, `auth_secret_col`, and `oauth_endpoint` to enable automatic token refresh:

- **Proactive**: if `expires_col` is set and the token has expired, a fresh token is fetched before the call
- **Reactive**: if a call returns 401, the token is refreshed once and the call retried

## Alternatives

- [**requests**](https://docs.python-requests.org) — HTTP client underlying this library; use directly for full control
- [**edfi-client**](https://pypi.org/project/edfi-client/) — Ed-Fi-specific client with broader resource coverage

Choose this library when you're already working in the `tha-*` row-dict pipeline and want Ed-Fi batch operations to follow the same conventions (skip_statuses, commit flag, self.rows, per-row credentials).

## License

MIT
