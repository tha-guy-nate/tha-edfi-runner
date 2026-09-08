# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.13] - 2026-09-08
### Changed
- `row_index` on `batch_post_payload` / `batch_get_by_id` / `batch_delete_by_id` results is now the row's position in the caller's `rows` list, **not** its position in the post-`skip_statuses` filtered subset. When rows are skipped, the surviving results keep the indices those rows had on input (e.g. rows `0,2,4` after `1` and `3` are skipped), so `row_index` lines up with the original `rows` for `ThaMap.enrich_rows`. Batches with nothing skipped are unaffected. Introduced one release earlier in 0.1.12.

## [0.1.12] - 2026-09-08
### Added
- Every per-row result from `ThaStudentAssessment.batch_post_payload`, `batch_get_by_id`, and `batch_delete_by_id` now carries a `row_index` key — the position of the row in the returned list (which mirrors the filtered `rows`). It is a collision-free correlation key for merging results back onto the source rows (e.g. via `ThaMap.enrich_rows`) when the business key in `key` repeats across a batch — multiple school years, job types, or canary records for one account.
- `post_payload`, `get_by_id`, `delete_by_id` and their `batch_*` counterparts now return an `http_status` field holding the integer HTTP status code of the Ed-Fi response (e.g. `201`, `409`, `401`), or `None` for client-side failures (dry run, invalid JSON, missing column/URL/token). Callers can branch on the code — e.g. treat a `409` as an expected conflict versus a `401`/`403` auth failure — without substring-matching it out of the `message` prose.
- Both fields are purely additive; existing `key`/`id`/`status`/`message`/`data` fields are unchanged. `batch_get_all` (flat, account-deduplicated record list) is intentionally left as-is — it does not return one dict per row.

## [0.1.11] - 2026-09-07
### Fixed
- `api_version` no longer forwards an Ed-Fi ODS/API **product** release (e.g. `"7.1"`, as stored in a connection record's `edfiApiVersion`) straight into the resource URL. `ThaEdfiBase` now normalises `api_version` (and per-row `api_version_col` values) through `resolve_api_spec_segment`: product releases 3.x/5.x/6.x/7.x map to the `v3` Data Standard URL segment, an already-correct `v<N>` segment passes through, and an empty value is left untouched. Previously `.../data/7.1/ed-fi/studentAssessments` produced an HTTP 302 instead of hitting `.../data/v3/...`.

## [0.1.10] - 2026-08-21
### Fixed
- Re-locked transitive `pip` (pulled in via `deptry` -> `pip-api`) from `26.1.2` to `26.2.1`, resolving a known CVE (PYSEC-2026-3721) flagged by `pip-audit`.

## [0.1.9] - 2026-07-25
### Fixed
- Bumped `tha-req-runner` dependency floor from `>=0.2.5` to `>=0.2.7` — versions 0.2.5 and earlier are yanked on PyPI.
- Corrected `__init__.py.__version__` drift (was stuck at 0.1.7 while `pyproject.toml` had already moved to 0.1.8).

## [0.1.8] - 2026-07-04
### Fixed
- Aligned `pyproject.toml` `keywords` with the GitHub topic naming (`api` -> `rest-api`) to match how the repo is tagged elsewhere.

## [0.1.7] - 2026-07-04
### Fixed
- `get_all(show_progress=True)` crashed with `TypeError: bool() undefined when iterable == total == None` — the bare `tqdm(desc=..., unit=...)` progress bar (no `total`/`iterable`) doesn't support truthiness checks. Changed all `if progress:` guards to `if progress is not None:`. This is why the `show_progress=True` path had 0% test coverage — it was untested and broken.
- Closed remaining test coverage gaps (94% → 100%): `OAuth2Auth.expires_at`, `_refetch_token` (previously untested — always mocked out in call-site tests), non-string payload values in `batch_post_payload`, and proactive token-refresh via `expires_col` in `batch_post_payload`/`batch_get_all`/`batch_delete_by_id` (only the reactive on-401 refresh path had tests).

## [0.1.6] - 2026-07-03
### Fixed
- Bumped `tha-req-runner` dependency floor from `>=0.2.2` to `>=0.2.5` — versions 0.2.2 and earlier are yanked on PyPI, and the committed lock file was still resolving to the yanked 0.2.2.
- Corrected `__init__.py.__version__` drift (was stuck at 0.1.3 while `pyproject.toml` had already moved to 0.1.5).

## [0.1.5] - 2026-06-27
### Added
- MIT license file with attribution requirement.
- Auto-tag reusable workflow in CI.
### Changed
- Enabled mypy strict mode for comprehensive type checking.

## [0.1.3] - 2026-06-16
### Added
- Python 3.13 and 3.14 classifier and CI support.
- Dependabot for automated updates.
### Changed
- Standardized CI and publish workflows.
- Bumped minimum dev dependency floors (pytest ≥ 9.1.0, ruff ≥ 0.15.17, mypy ≥ 2.1.0).
- Improved tqdm label formatting.

## [0.1.2] - 2026-06-05
### Fixed
- mypy type errors in `runner.py`.
- ruff lint violations (E501, I001).

## [0.1.0] - 2026-06-05
### Added
- Initial release with `ThaEdFi` for typed Ed-Fi ODS/API access.
