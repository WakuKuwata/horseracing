#!/usr/bin/env bash
# analyze F1 (SC-004): migration failure must block the API (fail-closed). Run from deploy/.
# Breaks the owner connection so migrate exits non-zero, then asserts api never becomes running.
set -euo pipefail
cd "$(dirname "$0")/.."

echo "== teardown =="
docker compose down -v >/dev/null 2>&1 || true

echo "== up with a broken owner URL (migrate must fail) =="
# wrong password → migrate alembic/connect fails → non-zero exit
set +e
DATABASE_URL_OWNER="postgresql+psycopg://hr_owner:WRONG@db:5432/${POSTGRES_DB:-horseracing}" \
  docker compose up --wait -d
up_rc=$?
set -e

echo "== migrate exited NON-zero =="
code=$(docker compose ps -a --format '{{.Service}} {{.ExitCode}}' | awk '$1=="migrate"{print $2}')
test "${code:-0}" != "0" || { echo "FAIL: migrate exited 0 with broken DB"; exit 1; }

echo "== api is NOT running (fail-closed) =="
state=$(docker compose ps --format '{{.Service}} {{.State}}' | awk '$1=="api"{print $2}')
test "${state:-}" != "running" || { echo "FAIL: api started despite migrate failure"; exit 1; }

echo "FAIL-CLOSED OK (up_rc=$up_rc, migrate exit=$code, api state=${state:-absent})"
docker compose down -v >/dev/null 2>&1 || true
