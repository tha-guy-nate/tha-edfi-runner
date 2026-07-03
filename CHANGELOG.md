# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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
