# Copperbelt Intelligence API

FastAPI service and background worker. See the [root README](../README.md) for setup and tests.

```bash
uv sync --frozen
uv run alembic upgrade head          # owner role: CBI_DATABASE_OWNER_URL
uv run uvicorn copperbelt.main:app   # API
uv run copperbelt worker             # background jobs (OCR, extraction, catalogue harvests)
uv run copperbelt drain              # process queued jobs once, then exit
uv run copperbelt check-providers    # live check of every configured external provider
uv run pytest                        # tests (needs the test database, see root README)
```
