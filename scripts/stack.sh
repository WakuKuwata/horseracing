#!/usr/bin/env bash
# ローカル製品スタックの独立起動管理(Browser pane / preview 非依存)。
# 使い方: scripts/stack.sh {start|stop|restart|status} [component]
#   component 省略時は全体。指定可: postgres api ops-api worker front admin
#
# 各サーバーは nohup でシェル/エディタのセッションから切り離して起動し、
# PID とログを logs/ (gitignore 済み) に置く。停止は PID からプロセスツリーを
# 再帰的に辿って子孫ごと止める(macOS に setsid が無いため。uv run→python、
# pnpm→node のように親だけ殺すと本体が孤児で生き残るのを防ぐ)。
set -u

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LOGS="$ROOT/logs"
mkdir -p "$LOGS"

DB_URL="postgresql+psycopg://aiuma:aiuma@localhost:15432/horseracing"
COMPONENTS=(postgres api ops-api worker front admin)

# --- 各 component の起動コマンド -------------------------------------------
# api は荒れ度ローダー(084/086)が fail-closed のため 4 env が必須
# (.claude/launch.json の "api" 構成と同一。欠けると全レース artifact_unavailable)
start_api() {
  cd "$ROOT/api" || return 1
  DATABASE_URL="$DB_URL" \
  DISPERSION_BOUNDARY_PATH="$ROOT/artifacts/dispersion_bands/dispbands-v1.json" \
  DISPERSION_PCAL_PATH="$ROOT/artifacts/dispersion_bands/pcal-v1.json" \
  CHAOS_BANDS_ARTIFACT_PATH="$ROOT/artifacts/chaos_bands/20d1e000de200a2a1ad0687ba9456cf12121f1b575dc5d87a7d482e9f9f83680.json" \
  CHAOS_BANDS_APPROVED_MANIFEST="$ROOT/config/chaos_bands_approved.json" \
  nohup uv run uvicorn horseracing_api.app:app --host 127.0.0.1 --port 8000 \
    > "$LOGS/api.log" 2>&1 &
  echo $! > "$LOGS/api.pid"
}

start_ops_api() {
  cd "$ROOT/ops" || return 1
  DATABASE_URL="$DB_URL" \
  nohup uv run uvicorn horseracing_ops.app:app --host 127.0.0.1 --port 8001 \
    > "$LOGS/ops-api.log" 2>&1 &
  echo $! > "$LOGS/ops-api.pid"
}

start_worker() {
  cd "$ROOT/ops" || return 1
  DATABASE_URL="$DB_URL" \
  nohup uv run python -m horseracing_ops.worker \
    > "$LOGS/worker.log" 2>&1 &
  echo $! > "$LOGS/worker.pid"
}

start_front() {
  cd "$ROOT" || return 1
  nohup pnpm -C front dev --port 5174 \
    > "$LOGS/front.log" 2>&1 &
  echo $! > "$LOGS/front.pid"
}

start_admin() {
  cd "$ROOT" || return 1
  nohup pnpm -C admin dev --host 127.0.0.1 --port 5175 \
    > "$LOGS/admin.log" 2>&1 &
  echo $! > "$LOGS/admin.pid"
}

# --- 汎用処理 ---------------------------------------------------------------
port_of() {
  case "$1" in
    api) echo 8000 ;;
    ops-api) echo 8001 ;;
    front) echo 5174 ;;
    admin) echo 5175 ;;
    *) echo "" ;;
  esac
}

is_up() {
  local c="$1"
  case "$c" in
    postgres)
      docker ps --filter name=docker-postgres-1 --format '{{.Status}}' | grep -q Up ;;
    worker)
      pgrep -f "horseracing_ops\.worker" >/dev/null 2>&1 ;;
    *)
      local p; p="$(port_of "$c")"
      lsof -nP -iTCP:"$p" -sTCP:LISTEN -t >/dev/null 2>&1 ;;
  esac
}

# 子孫を先に止めてから親を止める(uv run→python、pnpm→node の孤児化防止)
kill_tree() {
  local pid="$1"
  local child
  for child in $(pgrep -P "$pid" 2>/dev/null); do
    kill_tree "$child"
  done
  kill "$pid" 2>/dev/null
}

start_one() {
  local c="$1"
  if is_up "$c"; then echo "[skip] $c は既に稼働中"; return 0; fi
  case "$c" in
    postgres) docker start docker-postgres-1 >/dev/null && echo "[start] postgres" ;;
    api) start_api && echo "[start] api :8000" ;;
    ops-api) start_ops_api && echo "[start] ops-api :8001" ;;
    worker) start_worker && echo "[start] worker" ;;
    front) start_front && echo "[start] front :5174" ;;
    admin) start_admin && echo "[start] admin :5175" ;;
    *) echo "unknown component: $c" >&2; return 1 ;;
  esac
}

# 停止要求のあと、実際にプロセスが消えるまで待つ。worker は SIGTERM を受けてから
# 走行中ジョブを畳んでキューに戻して終了するので、消えるまで最大 10 数秒かかる。
# 待たずに start へ進むと is_up がまだ true を返し「[skip] 既に稼働中」で起動を飛ばす
# ——古いコードが動き続け、restart したつもりが無反映になる(このリポジトリで過去に
# 実際に起きた事故と同じ形)。
wait_gone() {
  local c="$1" i=0
  while is_up "$c" && [ "$i" -lt 40 ]; do sleep 0.5; i=$((i+1)); done
  if is_up "$c"; then
    echo "[warn] $c が 20 秒経っても停止しない。強制終了する" >&2
    case "$c" in
      worker) for pid in $(pgrep -f "horseracing_ops\.worker"); do kill -9 "$pid" 2>/dev/null; done ;;
      *) local p; p="$(port_of "$c")"
         [ -n "$p" ] && for pid in $(lsof -nP -iTCP:"$p" -sTCP:LISTEN -t); do kill -9 "$pid" 2>/dev/null; done ;;
    esac
    while is_up "$c" && [ "$i" -lt 60 ]; do sleep 0.5; i=$((i+1)); done
  fi
}

stop_one() {
  local c="$1"
  case "$c" in
    postgres) echo "[keep] postgres は docker 管理のため止めない (必要なら: docker stop docker-postgres-1)" ;;
    *)
      if [ -f "$LOGS/$c.pid" ]; then
        kill_tree "$(cat "$LOGS/$c.pid")"
        rm -f "$LOGS/$c.pid"
        echo "[stop] $c"
      elif is_up "$c"; then
        local p; p="$(port_of "$c")"
        if [ -n "$p" ]; then
          for pid in $(lsof -nP -iTCP:"$p" -sTCP:LISTEN -t); do kill_tree "$pid"; done
          echo "[stop] $c (pidfile 無し・ポートから停止)"
        elif [ "$c" = "worker" ]; then
          for pid in $(pgrep -f "horseracing_ops\.worker"); do kill "$pid" 2>/dev/null; done
          echo "[stop] worker (pidfile 無し・パターンから停止)"
        else
          echo "[warn] $c の pidfile が無く停止方法不明" >&2
        fi
      else
        echo "[skip] $c は稼働していない"
      fi
      ;;
  esac
}

status_one() {
  local c="$1"
  if is_up "$c"; then echo "  $c: UP"; else echo "  $c: DOWN"; fi
}

# --- エントリポイント -------------------------------------------------------
cmd="${1:-status}"
target="${2:-all}"

targets=()
if [ "$target" = "all" ]; then targets=("${COMPONENTS[@]}"); else targets=("$target"); fi

case "$cmd" in
  start)
    for c in "${targets[@]}"; do start_one "$c"; done
    # postgres が起動直後なら受付可能になるまで待つ
    if printf '%s\n' "${targets[@]}" | grep -q postgres; then
      for _ in $(seq 1 10); do
        docker exec docker-postgres-1 pg_isready -U aiuma >/dev/null 2>&1 && break
        sleep 1
      done
    fi
    ;;
  stop)
    for c in "${targets[@]}"; do stop_one "$c"; done
    ;;
  restart)
    for c in "${targets[@]}"; do stop_one "$c"; done
    # 固定 sleep ではなく実際に消えるまで待つ(worker の graceful shutdown は最大十数秒)
    for c in "${targets[@]}"; do [ "$c" = "postgres" ] || wait_gone "$c"; done
    for c in "${targets[@]}"; do start_one "$c"; done
    ;;
  status)
    echo "stack status:"
    for c in "${targets[@]}"; do status_one "$c"; done
    ;;
  *)
    echo "usage: $0 {start|stop|restart|status} [component]" >&2
    echo "components: ${COMPONENTS[*]}" >&2
    exit 1
    ;;
esac
