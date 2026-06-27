#!/usr/bin/env bash
# T012 (US2): single-origin routing boundary (SC-003). Assumes the stack is up (smoke.sh first).
set -euo pipefail
B=${BASE:-http://localhost:8080}

echo "== / returns the SPA shell =="
curl -fsS "$B/" | grep -q '<div id="root"'

echo "== SPA deep link → index.html (history fallback) =="
curl -fsS "$B/races/200805030401" | grep -q '<div id="root"'

echo "== /api/v1/health → 200 =="
test "$(curl -s -o /dev/null -w '%{http_code}' "$B/api/v1/health")" = "200"

echo "== /api/v1/races → JSON (path preserved through proxy) =="
curl -fsS "$B/api/v1/races?page_size=1" | grep -q '"items"'

echo "== unknown /api/v1/* → API 404 (NOT the SPA index.html) =="
code=$(curl -s -o /tmp/unk.txt -w '%{http_code}' "$B/api/v1/does-not-exist")
test "$code" = "404"
! grep -q '<div id="root"' /tmp/unk.txt   # must not be the SPA shell

echo "ROUTING OK"
