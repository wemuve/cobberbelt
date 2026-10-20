# ADR 0002 — Tenant isolation: application filters plus PostgreSQL row-level security

**Status:** accepted · 2026-10-20

**Decision.** Every tenant table has `org_id`. The API filters by organisation in every query, and PostgreSQL RLS policies (`FORCE ROW LEVEL SECURITY`) compare `org_id` with the transaction-local `app.org_id`, set at the start of every transaction from the session. The runtime connects as `copperbelt_app`, which is neither owner nor superuser and lacks `BYPASSRLS`. Migrations and backups run as the owner role (`BYPASSRLS`). Workers use a per-job tenant session; only queue bookkeeping uses the system context.

**Why both layers.** RLS alone gives poor error messages and makes per-user rules (classification, project membership) awkward. Application filters alone fail silently when someone forgets a `WHERE`. The RLS test connects directly as the app role to prove the database layer independently.

**Not covered by RLS.** `users`, `memberships` and `organisations` are needed for authentication and are guarded in the application. Document classification and project visibility are per-user rules enforced in SQL by `services/access.py`.
