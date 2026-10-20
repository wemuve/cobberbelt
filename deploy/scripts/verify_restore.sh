#!/usr/bin/env bash
# Verify a restored copy against the source: row counts per table and SHA-256 of every stored original.
# Usage: SRC_PGURL=... DST_PGURL=... DST_OBJECTS=/path ./verify_restore.sh
set -euo pipefail
fail=0
for t in organisations users documents document_pages claims geo_features feature_evidence projects risks assessments audit_events; do
  a=$(psql "$SRC_PGURL" -Atc "SELECT count(*) FROM $t"); b=$(psql "$DST_PGURL" -Atc "SELECT count(*) FROM $t")
  printf '%-18s source=%-6s restored=%-6s %s\n' "$t" "$a" "$b" "$([[ $a == "$b" ]] && echo OK || echo MISMATCH)"
  [[ $a == "$b" ]] || fail=1
done
if [[ -n "${DST_OBJECTS:-}" ]]; then
  while IFS='|' read -r key sha; do
    actual=$(sha256sum "$DST_OBJECTS/$key" | cut -d' ' -f1)
    [[ "$actual" == "$sha" ]] || { echo "OBJECT MISMATCH $key"; fail=1; }
  done < <(psql "$DST_PGURL" -Atc "SELECT storage_key, sha256 FROM documents")
  echo "objects verified: $(psql "$DST_PGURL" -Atc 'SELECT count(*) FROM documents')"
fi
[[ $fail == 0 ]] && echo "RESTORE VERIFIED" || { echo "RESTORE VERIFICATION FAILED"; exit 1; }
