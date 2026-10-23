# Copperbelt Intelligence

Evidence-traceable geological and mining project intelligence. Teams upload authorised archive documents (maps, reports, station tables). The platform OCRs them in English and French, extracts claims with a link back to the page they came from, places features on a map with their true location uncertainty, and builds project workspaces and auditable assessments on top. Every number and position can be traced to its source document, and nothing is invented to fill a gap.

| Part | Stack | Path |
|---|---|---|
| API + worker | Python 3.13, FastAPI, SQLAlchemy 2, Alembic, structlog | [`backend/`](backend/) |
| Database | PostgreSQL 16 + PostGIS + pgvector + full-text search, row-level security per tenant | [`deploy/db/`](deploy/db/) |
| Web app | Next.js 15, React 19, MapLibre | [`frontend/`](frontend/) |
| OCR / malware scan | Tesseract (eng+fra), ClamAV | via Docker |

Deployment is container-based (Docker Compose and three Dockerfiles). The repository deliberately contains no Terraform, Kubernetes or other infrastructure-as-code; see [`docs/operations.md`](docs/operations.md) for how the containers are run.

## Quick start (Docker)

Requires Docker with Compose v2.

```bash
cp .env.example .env          # then replace every change-me value
docker compose up --build
```

- API docs: http://localhost:8000/api/docs
- Web app: http://localhost:3000
- Readiness: `curl http://localhost:8000/api/v1/ready`
- Smoke test against the running stack: `cd backend && CBI_SMOKE_URL=http://localhost:8000 uv run python scripts/smoke.py` (prints `SMOKE OK`)

## Running the tests

### Backend

The tests run against a real PostgreSQL database, as the non-superuser application role, so row-level security is exercised exactly as in production. External providers are never called.

Prerequisites: [uv](https://docs.astral.sh/uv/), PostgreSQL 16 with PostGIS and pgvector, Tesseract with the `eng` and `fra` languages. On Ubuntu 24.04:

```bash
sudo apt-get install postgresql-16-postgis-3 postgresql-16-pgvector tesseract-ocr tesseract-ocr-fra fonts-dejavu-core
```

One-time database setup (as a PostgreSQL superuser):

```bash
psql -U postgres -v owner_password=devpassword -v app_password=devpassword -f deploy/db/bootstrap.sql
psql -U postgres -c "ALTER ROLE copperbelt SUPERUSER"   # the test harness drops and recreates the schema
psql -U postgres -c "CREATE DATABASE copperbelt_test OWNER copperbelt"
```

Then:

```bash
cd backend
uv sync --frozen
uv run pytest                                # unit, workflow, security, job and provider-contract tests, with coverage
uv run ruff check src tests && uv run ruff format --check src tests
```

The test database name, host and passwords can be changed with `CBI_TEST_DB`, `CBI_TEST_DB_HOST`, `CBI_TEST_APP_PASSWORD` and `CBI_TEST_OWNER_PASSWORD` (see [`.env.example`](.env.example)). Tests that call real external providers are skipped unless `CBI_RUN_LIVE=1` is set together with real credentials.

### Frontend

```bash
cd frontend
npm ci
npm test            # unit tests (Vitest)
npm run lint
npm run typecheck
npm run build
```

The browser journey test (`frontend/e2e/journey.mjs`) drives a running stack with a synthetic PDF; its usage is documented at the top of the file.

### CI

[`.github/workflows/ci.yml`](.github/workflows/ci.yml) runs all of the above on every push and pull request, plus `alembic check` (migrations match the models), `pip-audit`, `npm audit`, an OpenAPI drift check against [`docs/api/openapi.json`](docs/api/openapi.json), and a build of all three images.

## Documentation

| Topic | File |
|---|---|
| Architecture, request path, document pipeline, evidence model | [`docs/architecture.md`](docs/architecture.md) |
| Architecture decision records | [`docs/adr/`](docs/adr/) |
| Configuration (every `CBI_*` setting, per environment) | [`docs/configuration-matrix.md`](docs/configuration-matrix.md) |
| Security model and threat assessment | [`docs/security.md`](docs/security.md) |
| Operations runbook, backups and restore | [`docs/operations.md`](docs/operations.md) |
| Data sources and licences | [`docs/source-register.md`](docs/source-register.md) |
| Evaluation against real archive documents | [`docs/evaluation.md`](docs/evaluation.md) |
| Backlog and validation plan | [`docs/backlog.md`](docs/backlog.md) |
| Release history | [`CHANGELOG.md`](CHANGELOG.md) |
