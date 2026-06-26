#!/usr/bin/env bash
# Drift check: regenerate types from the COMMITTED openapi.json and diff against the committed
# src/api/schema.d.ts. Deterministic (pinned openapi-typescript). Fails on any drift.
set -euo pipefail
cd "$(dirname "$0")/.."

tmp="$(mktemp)"
pnpm exec openapi-typescript openapi.json -o "$tmp"
if ! diff -u src/api/schema.d.ts "$tmp"; then
  echo "openapi drift: src/api/schema.d.ts is out of date — run pnpm gen:types and commit." >&2
  rm -f "$tmp"
  exit 1
fi
rm -f "$tmp"
echo "openapi types in sync with committed openapi.json"
