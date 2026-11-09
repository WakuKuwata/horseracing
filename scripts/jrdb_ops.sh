#!/usr/bin/env bash
# 178 JRDB 経路の運用 wrapper(specs/178-jrdb-primary-source/manual.md)。jra_ops.sh と同型。
#
#   scripts/jrdb_ops.sh fetch   2026-11-07 [--kinds BAC KYI UKC HJC SED TYB]   日次ファイル(会員ページ・単体 zip)
#   scripts/jrdb_ops.sh archive 2008 [2009] [--kinds SED KYI ...]               年度パック(開催日 08:00〜19:00 は CLI が拒否)
#   scripts/jrdb_ops.sh parity  2008 --files-dir ABS [--out ABS]               段 1 の照合(DB 読み取り専用)
#   scripts/jrdb_ops.sh persons --files-dir ABS [--profiles ABS] [--codes-from ABS] [--write]   人物写像(既定 --dry-run)
#   scripts/jrdb_ops.sh persons-collect jockey|trainer --index-url URL... --index-post URL CNAME... [--max-pages N]   JRA 公式の人物ページ収集(R3・1 回限り・10 秒間隔)
#   scripts/jrdb_ops.sh entries 2026-11-07 --profile current        前日: BAC+KYI(+UKC) → races/race_horses/horses
#   scripts/jrdb_ops.sh prerace 2026-11-08 --profile current [--venue E|W|L]   当日: TYB → 馬体重・取消
#   scripts/jrdb_ops.sh results 2026-11-08 --profile current        木曜: SED 正式版+SRB → race_results/race_laps/確定オッズ
#   scripts/jrdb_ops.sh payouts 2026-11-08 --profile current        夜: HJC → official_win_payouts/exotic_odds
#   scripts/jrdb_ops.sh audit   2026-11-08                          来歴・jrdb_files・artifacts の整合(READ ONLY)
#   scripts/jrdb_ops.sh show-held [2026-11-08]                      held 行と現行版の差分(READ ONLY・要求 0 件)
#   scripts/jrdb_ops.sh apply-held FILE_ID --profile current [--yes --replaces CURRENT_ID|none]  held の承認(--yes 無しは差分表示だけ)
#   scripts/jrdb_ops.sh repair  COLUMN 2025-10-11 2026-11-08 [--dry-run]   段 3a: 現行 DB の 1 列(基準合格列のみ)
#   scripts/jrdb_ops.sh rebuild 1999-01-01 2026-11-08 [--db-url URL]       段 3b: 別 DB(省略時は env DATABASE_URL_JRDB・現行と同じなら CLI が拒否)
#
# 認証情報は config/.env.jrdb(gitignore・JRDB_MEMBER_USER / JRDB_MEMBER_PASSWORD)から読む。値は echo しない。
# 間隔は config/source-operations.json の `jrdb_interval_seconds`(既定 5・下限 2)を HORSERACING_JRDB_MIN_INTERVAL_S に写す。
# この wrapper は便利のためで安全境界ではない: CLI 自身が同じ検査(policy・env・認証・制限時間帯)をして落ちる。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export DATABASE_URL="${DATABASE_URL:-postgresql+psycopg://aiuma:aiuma@localhost:15432/horseracing}"
SCRAPE_PY="$ROOT/scrape/.venv/bin/python"
POLICY="$ROOT/config/source-operations.json"
ENV_FILE="$ROOT/config/.env.jrdb"

usage() { sed -n '2,21p' "$0"; exit 2; }
[ $# -ge 1 ] || usage
cmd="$1"; shift
case "$cmd" in fetch|archive|parity|persons|persons-collect|entries|prerace|results|payouts|audit|repair|rebuild|show-held|apply-held) ;; *) usage ;; esac

if [ -f "$ENV_FILE" ]; then
  # shellcheck disable=SC1090
  set -a; . "$ENV_FILE"; set +a
fi

JRDB_INTERVAL_S="$("$SCRAPE_PY" - "$POLICY" <<'PY'
import json, math, os, sys
policy = json.load(open(sys.argv[1]))
value = policy.get("jrdb_interval_seconds", 5)
if type(value) not in (int, float) or isinstance(value, bool) or not math.isfinite(value) or value < 2:
    sys.exit("jrdb_interval_seconds must be a finite number >= 2: %r" % (value,))
inherited = os.environ.get("HORSERACING_JRDB_MIN_INTERVAL_S")
if inherited:
    try:
        value = max(value, float(inherited))
    except ValueError:
        sys.exit("inherited HORSERACING_JRDB_MIN_INTERVAL_S is not a number")
print(int(value) if float(value).is_integer() else value)
PY
)" || { echo "policy jrdb interval unusable ($POLICY); not starting" >&2; exit 2; }
export HORSERACING_JRDB_MIN_INTERVAL_S="$JRDB_INTERVAL_S"

log() { printf '%s %s\n' "$(date '+%H:%M:%S')" "$*"; }
log "jrdb interval ${JRDB_INTERVAL_S}s per request (policy $POLICY; credentials from ${ENV_FILE##*/}: $([ -n "${JRDB_MEMBER_USER:-}" ] && echo present || echo missing))"

run() { exec "$SCRAPE_PY" -m horseracing_scrape.jrdb.cli "$@"; }

case "$cmd" in
  fetch)
    [ $# -ge 1 ] || usage
    day="$1"; shift
    [[ "$day" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}$ ]] || { echo "date must be YYYY-MM-DD" >&2; exit 2; }
    run jrdb-fetch --date "$day" --policy "$POLICY" --json "$@"
    ;;
  archive)
    [ $# -ge 1 ] || usage
    y0="$1"; shift
    y1="$y0"
    if [ $# -ge 1 ] && [[ "$1" =~ ^[0-9]{4}$ ]]; then y1="$1"; shift; fi
    run jrdb-fetch --archive --from "$y0" --to "$y1" --policy "$POLICY" --json "$@"
    ;;
  parity)
    [ $# -ge 1 ] || usage
    year="$1"; shift
    run jrdb-parity --year "$year" --json "$@"
    ;;
  persons)
    if [ "${1:-}" = "--write" ]; then shift; run jrdb-persons --json "$@"; fi
    run jrdb-persons --dry-run --json "$@"
    ;;
  persons-collect)
    # JRA 公式サイトへ行くので 176 と同じ間隔(policy jra_official_interval_seconds・下限 10)と共有リミッタ
    [ $# -ge 1 ] || usage
    kind="$1"; shift
    JRA_INTERVAL_S="$("$SCRAPE_PY" - "$POLICY" <<'JRAPY'
import json, math, os, sys
policy = json.load(open(sys.argv[1]))
value = policy.get("jra_official_interval_seconds")
if type(value) not in (int, float) or isinstance(value, bool) or not math.isfinite(value) or value < 10:
    sys.exit("jra_official_interval_seconds must be a finite number >= 10: %r" % (value,))
inherited = os.environ.get("HORSERACING_JRA_MIN_INTERVAL_S")
if inherited:
    value = max(value, float(inherited))
print(int(value) if float(value).is_integer() else value)
JRAPY
)" || { echo "policy jra interval unusable ($POLICY); not starting" >&2; exit 2; }
    export HORSERACING_JRA_MIN_INTERVAL_S="$JRA_INTERVAL_S" HORSERACING_SHARED_LIMITER=1
    log "jra interval ${JRA_INTERVAL_S}s per request (shared limiter)"
    run jrdb-persons-collect --kind "$kind" --policy "$POLICY" --json "$@"
    ;;
  entries|prerace|results|payouts|audit)
    [ $# -ge 1 ] || usage
    day="$1"; shift
    [[ "$day" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}$ ]] || { echo "date must be YYYY-MM-DD" >&2; exit 2; }
    run "jrdb-$cmd" --date "$day" --policy "$POLICY" --json "$@"
    ;;
  repair)
    [ $# -ge 3 ] || usage
    col="$1"; d0="$2"; d1="$3"; shift 3
    [[ "$d0" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}$ && "$d1" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}$ ]] || { echo "dates must be YYYY-MM-DD" >&2; exit 2; }
    run jrdb-repair --column "$col" --from "$d0" --to "$d1" \
      --files-dir "$ROOT/artifacts/jrdb" --parity-dir "$ROOT/artifacts/jrdb/parity" --policy "$POLICY" --json "$@"
    ;;
  show-held)
    if [ $# -ge 1 ] && [[ "$1" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}$ ]]; then
      day="$1"; shift
      run jrdb-fetch --show-held --date "$day" --policy "$POLICY" --json "$@"
    fi
    run jrdb-fetch --show-held --policy "$POLICY" --json "$@"
    ;;
  apply-held)
    [ $# -ge 1 ] || usage
    file_id="$1"; shift
    run jrdb-apply-held "$file_id" --policy "$POLICY" --json "$@"
    ;;
  rebuild)
    [ $# -ge 2 ] || usage
    d0="$1"; d1="$2"; shift 2
    [[ "$d0" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}$ && "$d1" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}$ ]] || { echo "dates must be YYYY-MM-DD" >&2; exit 2; }
    target="${DATABASE_URL_JRDB:-}"
    if [ "${1:-}" = "--db-url" ]; then target="$2"; shift 2; fi
    [ -n "$target" ] || { echo "rebuild needs --db-url URL or env DATABASE_URL_JRDB (never the current DB)" >&2; exit 2; }
    run jrdb-rebuild --from "$d0" --to "$d1" --files-dir "$ROOT/artifacts/jrdb" \
      --db-url "$target" --reference-db-url "$DATABASE_URL" --json "$@"
    ;;
esac
