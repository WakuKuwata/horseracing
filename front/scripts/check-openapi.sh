#!/usr/bin/env bash
# Drift check: regenerate types from the COMMITTED openapi.json and diff against the committed
# src/api/schema.d.ts. Deterministic (pinned openapi-typescript). Fails on any drift.
set -euo pipefail
cd "$(dirname "$0")/.."

check() {
  local snapshot="$1" generated="$2" name="$3"
  local tmp
  tmp="$(mktemp)"
  pnpm exec openapi-typescript "$snapshot" -o "$tmp"
  if ! diff -u "$generated" "$tmp"; then
    echo "openapi drift: $generated is out of date — regenerate from $snapshot and commit." >&2
    rm -f "$tmp"
    exit 1
  fi
  rm -f "$tmp"
  echo "$name types in sync with committed $snapshot"
}

# 014 read-only API
check openapi.json src/api/schema.d.ts "014 api"
# 024 ops write API
check ops-openapi.json src/api/ops-schema.d.ts "024 ops"
