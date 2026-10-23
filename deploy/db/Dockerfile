# PostgreSQL 16 + pgvector + PostGIS 3. Migration 0001 needs postgis, pg_trgm, unaccent and vector.
# Built on the pgvector image (Debian bookworm) rather than postgis/postgis:16-3.5, which is still on
# Debian bullseye; apt.postgresql.org no longer serves bullseye, so pgvector could not be installed there.
FROM pgvector/pgvector:0.8.7-pg16-bookworm
RUN apt-get update \
    && apt-get install -y --no-install-recommends postgresql-16-postgis-3 postgresql-16-postgis-3-scripts \
    && rm -rf /var/lib/apt/lists/*
