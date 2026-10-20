# Operations runbook

## Processes
| Process | Command | Scales | Health |
|---|---|---|---|
| API | `uvicorn copperbelt.main:app` | Horizontally (move rate limits to gateway/Redis first) | `GET /api/v1/health` = liveness; `GET /api/v1/ready` = DB, migration, storage, job counts |
| Worker | `copperbelt worker` | Horizontally (SKIP LOCKED) | `ready.checks.jobs` (queued / running / failed_retrying / dead) |
| Migrations | `alembic upgrade head` (owner role) | Run once per deploy, before API/worker start | `ready.checks.database.migration` |

"Deployed", "healthy", "data ingested" and "integration live" are separate states. Check each one:
1. Deployment succeeded: the container is running.
2. Health: `/health` returns 200.
3. Readiness: `/ready` returns 200 with the expected migration.
4. Ingestion: `python backend/scripts/smoke.py` prints `SMOKE OK`.
5. Integrations: `copperbelt check-providers`, where each provider must show `live_verified`.

## Alerts to configure
- `/ready` not 200 for 2 minutes.
- `jobs.dead > 0` (each dead job needs a human).
- `jobs.queued` rising for 15 minutes with no `running` (workers down).
- API 5xx rate above 1% over 5 minutes, from the structured `http.request` logs.
- Any provider check moving from `live_verified` to `error` or `unreachable`.
- Backup job missing, or the weekly restore verification failing.

## Logs
JSON lines on stdout carry `request_id`, `method`, `path`, `status` and `duration_ms`. Error responses include the same `request_id`, so a user-reported id can be found with `grep`.

## Dead jobs
`GET /api/v1/jobs?status=dead` shows the jobs, with `last_error`. Fix the cause (for example a restored object or a scanner back up), then `POST /api/v1/jobs/{id}/retry`.

## Backup and restore
- **Backup (daily):** `PGURL=<owner url> OBJECTS=<local objects dir> OUT=<backup dir> deploy/scripts/backup.sh` writes `db.dump`, `objects.tar.gz`, `migration.txt` and `SHA256SUMS`. For S3 storage, enable bucket versioning plus cross-region replication instead of `OBJECTS`.
- **Restore:** create an empty database with the four extensions, then run `BACKUP=… PGURL=… OBJECTS=… deploy/scripts/restore.sh`. It verifies checksums first and refuses a non-empty target.
- **Verify (weekly, automated):** `SRC_PGURL=… DST_PGURL=… DST_OBJECTS=… deploy/scripts/verify_restore.sh` compares row counts and the SHA-256 of every stored original.
- **Last verified:** 2026-10-20 on the development database, ending in `RESTORE VERIFIED` with RLS policies intact (21).

## Upgrades
- Pin versions through `uv.lock` / `package-lock.json`.
- Upgrade in a branch, run CI, deploy to staging, run the smoke test, then deploy to production.
- Migrations must be backward-compatible with the previous API version for one release: add columns before removing them.

## Secrets
Generate them with `openssl rand -hex 32` and keep them in the platform's secret store. Rotating the JWT secret logs everyone out. Rotating database passwords takes `ALTER ROLE … PASSWORD` followed by a rolling restart.
