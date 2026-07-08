#!/usr/bin/env bash
# Drift-check (Feature 051, mirrors front/scripts/check-openapi.sh): regenerate TS types from the
# committed admin/openapi.json and fail if src/api/schema.d.ts is out of date.
set -euo pipefail
cd "$(dirname "$0")/.."
tmp="$(mktemp)"
pnpm exec openapi-typescript openapi.json -o "$tmp" >/dev/null
diff -q "$tmp" src/api/schema.d.ts || { echo "schema.d.ts is stale — run pnpm gen:types"; exit 1; }
# 053 ops write API (proxy) — this snapshot/type pair drifted silently before this check existed.
pnpm exec openapi-typescript ops-openapi.json -o "$tmp" >/dev/null
diff -q "$tmp" src/api/ops-schema.d.ts || { echo "ops-schema.d.ts is stale — regenerate from ops-openapi.json"; exit 1; }
rm -f "$tmp"
echo "admin openapi types are in sync"
