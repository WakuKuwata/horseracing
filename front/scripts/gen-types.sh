#!/usr/bin/env bash
# Refresh the committed OpenAPI snapshot from a RUNNING 014 API, then regenerate TS types.
# Requires the API up (see quickstart). Manual step — types/snapshot are committed for offline use.
set -euo pipefail
cd "$(dirname "$0")/.."

API_BASE="${VITE_API_BASE:-http://localhost:8000}"

# fetch + deterministically format (sorted keys) so the committed snapshot diff is stable
curl -fsS "${API_BASE}/openapi.json" \
  | node -e 'let s="";process.stdin.on("data",d=>s+=d).on("end",()=>{const o=JSON.parse(s);process.stdout.write(JSON.stringify(o,Object.keys(o).sort?undefined:undefined,2));});' \
  > /tmp/openapi.raw.json
# stable sort via python-free node: re-stringify with sorted keys
node -e '
const fs=require("fs");
const sortKeys=(v)=>Array.isArray(v)?v.map(sortKeys):(v&&typeof v==="object"?Object.fromEntries(Object.keys(v).sort().map(k=>[k,sortKeys(v[k])])):v);
const o=JSON.parse(fs.readFileSync("/tmp/openapi.raw.json","utf8"));
fs.writeFileSync("openapi.json",JSON.stringify(sortKeys(o),null,2)+"\n");
'
pnpm exec openapi-typescript openapi.json -o src/api/schema.d.ts
echo "regenerated openapi.json + src/api/schema.d.ts"
