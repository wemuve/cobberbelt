# Changelog

All notable changes to this project are recorded here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added
- Root README: what the project is, `docker compose up`, the exact backend and frontend test commands, and an index of the docs.
- Frontend unit tests (Vitest) for the API client (`src/lib/api.ts`) and map geometry (`src/lib/geo.ts`); `npm test` runs in CI.
- Backend tests for upload validation and the ClamAV client (against an in-process fake clamd) and for the `copperbelt` CLI, including the worker loop.
- Coverage measured on every backend test run; CI fails below 85% (currently 88%).
- Dependabot for uv, npm, Docker base images and GitHub Actions.
- CI check that `uv.lock` matches `pyproject.toml`.
- `.env.example` documents the variables used by the test harness, scripts, browser journey test and web build.

### Changed
- `maplibre-gl` 5.24 → 6.15 and `postcss` pinned to ≥ 8.5.31, clearing a critical and a high `npm audit` advisory that were failing CI.

## [0.1.0] - 2026-10-21

First working version of the whole stack.

### Added
- Backend foundation: PostGIS schema with row-level security per organisation, authentication, document upload and OCR pipeline (English and French), evidence claims linked to page offsets, search, projects and assessments.
- Security and reliability: tenant isolation tested at both the API and the database, token and membership revocation, rate limits, job retries; unit and provider-contract tests; lint and formatting.
- Operations: Docker images and Compose stack, CI, backup and restore scripts with a restore check, post-deploy smoke test, OpenAPI spec; architecture, ADR, source register, configuration, security and operations docs.
- Web app: evidence viewer, search, map that draws location uncertainty at true scale, projects, assessments and admin; browser journey test; real-document ingestion script.
- Job heartbeat and fencing (fixes double-processing of jobs that outlive the lock timeout); station-table coordinate rule with reviewer-supplied hemispheres; rules-only re-extraction without duplicate claims.
- Evaluation log from real archive documents, with the backlog re-prioritised from it.
- Catalogue harvester for page-numbered JSON APIs (first source: RDC Rocks): licence-gated, metadata only, host-pinned and rate-polite, with probe, harvest, change detection and per-record import; CI check that migrations match the models.

[Unreleased]: https://github.com/wemuve/cobberbelt/compare/c0550fa...HEAD
[0.1.0]: https://github.com/wemuve/cobberbelt/commit/c0550fa
