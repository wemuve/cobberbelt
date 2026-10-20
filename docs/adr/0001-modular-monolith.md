# ADR 0001 — Modular monolith on FastAPI + PostgreSQL/PostGIS

**Status:** accepted · 2026-10-20

**Context.** Six capability areas share identity, permissions, provenance, geometry and audit. The team is small and the first release covers one workflow.

**Decision.** One Python package (`copperbelt`) with domain modules, one API process and one worker process built from the same image. PostgreSQL 16 with PostGIS is the system of record for relational, spatial, full-text and (via pgvector) vector data.

**Consequences.** One transaction can cover a domain change, its audit event and its follow-up job. Spatial joins between evidence, projects and areas are plain SQL. Extraction to a service happens only for a real scaling or isolation need; satellite raster processing is the likely first.

**Rejected.** Microservices per domain (operational cost, distributed transactions, no current need). A separate search engine such as Elasticsearch (another stateful system to secure and keep consistent; revisit if FTS relevance or volume demands it).
