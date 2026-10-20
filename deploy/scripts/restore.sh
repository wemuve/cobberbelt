#!/usr/bin/env bash
# Restore a backup into an EMPTY target database and object directory, then verify integrity.
# Usage: BACKUP=/backups/cbi-... PGURL=postgresql://copperbelt:...@host/copperbelt_restore OBJECTS=/srv/cbi/objects-restore ./restore.sh
set -euo pipefail
: "${BACKUP:?}" "${PGURL:?}"
( cd "$BACKUP" && sha256sum -c SHA256SUMS )
EXISTING=$(psql "$PGURL" -Atc "SELECT count(*) FROM information_schema.tables WHERE table_schema='public' AND table_name='documents'")
if [[ "$EXISTING" != "0" ]]; then echo "Refusing: target database is not empty" >&2; exit 2; fi
pg_restore --no-owner --exit-on-error --dbname="$PGURL" "$BACKUP/db.dump"
if [[ -n "${OBJECTS:-}" && -f "$BACKUP/objects.tar.gz" ]]; then
  mkdir -p "$OBJECTS" && tar -C "$OBJECTS" -xzf "$BACKUP/objects.tar.gz"
fi
echo "restored migration: $(psql "$PGURL" -Atc 'SELECT version_num FROM alembic_version') (backup: $(cat "$BACKUP/migration.txt"))"
