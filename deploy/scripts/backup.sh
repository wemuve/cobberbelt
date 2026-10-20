#!/usr/bin/env bash
# Back up the database (custom-format pg_dump) and local object storage, with checksums.
# Usage: PGURL=postgresql://copperbelt:...@host/copperbelt OBJECTS=/srv/cbi/objects OUT=/backups ./backup.sh
# For S3 storage, rely on bucket versioning + replication instead of OBJECTS (see docs/operations.md).
set -euo pipefail
: "${PGURL:?set PGURL (owner role)}" "${OUT:?set OUT}"
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
DIR="$OUT/cbi-$STAMP"
mkdir -p "$DIR"
pg_dump --format=custom --no-owner --file="$DIR/db.dump" "$PGURL"
if [[ -n "${OBJECTS:-}" ]]; then
  tar -C "$OBJECTS" -czf "$DIR/objects.tar.gz" .
fi
psql "$PGURL" -Atc "SELECT version_num FROM alembic_version" > "$DIR/migration.txt"
( cd "$DIR" && sha256sum * > SHA256SUMS )
echo "$DIR"
