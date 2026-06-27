#!/usr/bin/env bash
# T010 (US1): compose up smoke (SC-001/SC-002). Run from deploy/. Requires deploy/.env.
set -euo pipefail
cd "$(dirname "$0")/.."

echo "== docker compose config =="
docker compose config >/dev/null

echo "== build =="
docker compose build

echo "== up --wait (postgres → migrate → api → nginx) =="
docker compose up --wait -d

echo "== migrate exited 0 =="
code=$(docker compose ps -a --format '{{.Service}} {{.ExitCode}}' | awk '$1=="migrate"{print $2}')
test "${code:-1}" = "0" || { echo "migrate exit=$code"; exit 1; }

echo "== nginx → /api/v1/health == 200 (schema_in_sync) =="
curl -fsS localhost:8080/api/v1/health | grep -q '"schema_in_sync": *true'

echo "== nginx → /api/v1/races returns JSON =="
curl -fsS 'localhost:8080/api/v1/races?page_size=2' | grep -q '"items"'

echo "SMOKE OK"
