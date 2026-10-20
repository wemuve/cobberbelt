# ADR 0004 — Hybrid search: PostgreSQL FTS + trigram + spatial now, vectors when evaluated

**Status:** accepted · 2026-10-20

**Decision.** Each page stores a generated `tsvector` that combines English stemming over accent-stripped text (weight A), French stemming over the original (weight B) and unstemmed tokens. Queries expand commodity terms through an en/fr vocabulary (for example *cuivre* → copper, chalcopyrite, malachite…). Feature names use `pg_trgm`. Spatial filters use PostGIS. Pages are chunked (1,200 chars, 200 overlap) with a nullable `vector(1536)` column and an HNSW index ready for embeddings.

**Why not vectors first.** Geological retrieval depends on exact names (formations, prospects, borehole IDs, mineral species) that embeddings handle poorly, and an embedding provider is a data-handling decision not yet made. Every search response lists the retrieval modes actually used, so semantic search can never be silently assumed.

**Next.** Build a relevance evaluation set from real documents (queries with judged relevant pages), measure FTS-only, then add embeddings and fuse ranks (for example RRF) only if they measurably help.
