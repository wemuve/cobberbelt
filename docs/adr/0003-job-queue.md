# ADR 0003 — PostgreSQL-backed job queue as the transactional outbox

**Status:** accepted · 2026-10-20

**Decision.** Jobs are rows in `jobs`, inserted in the same transaction as the change that requires them. Workers claim with `SELECT … FOR UPDATE SKIP LOCKED`. Retries use exponential backoff (10 s doubling, capped at 1 h). After `max_attempts` a job is `dead` and needs an operator retry (`POST /api/v1/jobs/{id}/retry`). A job whose worker died is reclaimed once its lock is older than `CBI_JOB_LOCK_TIMEOUT_SECONDS`. Handlers raise `PermanentJobError` for failures retrying cannot fix, such as a checksum mismatch.

**Why.** It needs no extra infrastructure, never loses or phantoms a job relative to its data, and is easy to inspect. Throughput of hundreds of jobs per second is far beyond first-release needs.

**Revisit when** queue depth or the need for priorities or fan-out exceeds what one table serves well. Then keep the outbox and relay it to a broker.
