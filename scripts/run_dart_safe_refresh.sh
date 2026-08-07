#!/bin/bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"

PYTHON_BIN="${PYTHON_BIN:-python3}"
BATCH_SIZE="${BATCH_SIZE:-5}"
TOTAL_COMPANIES="${TOTAL_COMPANIES:-100}"
BEGIN_DATE="${BEGIN_DATE:-20260101}"
END_DATE="${END_DATE:-20260806}"
REQUEST_DELAY="${REQUEST_DELAY:-6}"
REQUEST_JITTER="${REQUEST_JITTER:-1.5}"
COMPANY_DELAY="${COMPANY_DELAY:-10}"
BATCH_PAUSE="${BATCH_PAUSE:-90}"
MAX_ATTEMPTS="${MAX_ATTEMPTS:-4}"

base_args=(
  "$PYTHON_BIN"
  scripts/build_snapshot_from_dart_web.py
  --request-delay "$REQUEST_DELAY"
  --request-jitter "$REQUEST_JITTER"
  --company-delay "$COMPANY_DELAY"
  --max-attempts "$MAX_ATTEMPTS"
)

offset=0
while [ "$offset" -lt "$TOTAL_COMPANIES" ]; do
  echo "== sync-company-map offset=$offset limit=$BATCH_SIZE =="
  "${base_args[@]}" sync-company-map --offset "$offset" --limit "$BATCH_SIZE" --retry-pending || true

  echo "== pause ${BATCH_PAUSE}s =="
  sleep "$BATCH_PAUSE"

  echo "== scan-latest offset=$offset limit=$BATCH_SIZE =="
  "${base_args[@]}" scan-latest --begin "$BEGIN_DATE" --end "$END_DATE" --offset "$offset" --limit "$BATCH_SIZE" --retry-pending || true

  offset=$((offset + BATCH_SIZE))
  if [ "$offset" -lt "$TOTAL_COMPANIES" ]; then
    echo "== batch cooldown ${BATCH_PAUSE}s =="
    sleep "$BATCH_PAUSE"
  fi
done

echo "== enrich-targets =="
"${base_args[@]}" enrich-targets

echo "Safe refresh finished."
