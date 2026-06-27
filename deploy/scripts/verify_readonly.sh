#!/usr/bin/env bash
# T015 (US3): read-only role + reproducibility/secret checks (SC-006/SC-007/SC-008). Stack up first.
set -euo pipefail
cd "$(dirname "$0")/.."
set -a; . ./.env; set +a

echo "== app_ro cannot write (SELECT-only role) =="
# use the db container's psql; expect a privilege error (non-zero) on INSERT
if docker compose exec -T -e PGPASSWORD="${APP_RO_PASSWORD}" db \
     psql -U "${APP_RO_USER}" -d "${POSTGRES_DB}" -v ON_ERROR_STOP=1 \
     -c "INSERT INTO races(race_id) VALUES ('999999999999')" >/dev/null 2>&1; then
  echo "FAIL: app_ro was able to INSERT (should be read-only)"; exit 1
fi
echo "  app_ro INSERT correctly rejected"

echo "== app_ro CAN read =="
docker compose exec -T -e PGPASSWORD="${APP_RO_PASSWORD}" db \
  psql -U "${APP_RO_USER}" -d "${POSTGRES_DB}" -tAc "SELECT 1" | grep -q 1

echo "== live OpenAPI == committed front snapshot (015 type sync, SC-007) =="
diff <(curl -fsS localhost:8080/openapi.json | python3 -c 'import json,sys;print(json.dumps(json.load(sys.stdin),sort_keys=True,indent=2))') \
     <(python3 -c 'import json,sys;print(json.dumps(json.load(open("../front/openapi.json")),sort_keys=True,indent=2))') \
  && echo "  OpenAPI in sync"

echo "== built API image contains no .env (secret hygiene, SC-008) =="
if docker run --rm --entrypoint sh horseracing-api:local -c 'find /app -name ".env" -o -name ".env.*" | grep -q .'; then
  echo "FAIL: .env found in image"; exit 1
fi
echo "  no .env in image"

echo "READONLY/REPRO OK"
