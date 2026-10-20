#!/usr/bin/env bash
# First-boot initialisation for the postgis container: create roles and the database.
set -euo pipefail
psql -v ON_ERROR_STOP=1 --username postgres \
  -v owner_password="$CBI_DB_OWNER_PASSWORD" -v app_password="$CBI_DB_APP_PASSWORD" -f /bootstrap.sql
psql -v ON_ERROR_STOP=1 --username postgres -d copperbelt -c "CREATE EXTENSION IF NOT EXISTS postgis; CREATE EXTENSION IF NOT EXISTS vector; CREATE EXTENSION IF NOT EXISTS pg_trgm; CREATE EXTENSION IF NOT EXISTS unaccent;"
