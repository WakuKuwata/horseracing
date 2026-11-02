#!/usr/bin/env bash
# 176 JRA 公式サイト経路の運用 wrapper(specs/176-jra-official-source/manual.md)。near_post_ops.sh と同型。
#
#   scripts/jra_ops.sh entries 2026-11-01 [--race-id RID ...] [--dry-run]   出馬表+変更情報(索引 D → 開催日 → 出馬表 1 ページ/場 + accessI)
#   scripts/jra_ops.sh odds    2026-11-01 [--race-id RID ...]               単複オッズ(1 レース 1 ページ・--race-id 無しは全レース)
#   scripts/jra_ops.sh results 2026-11-01                                  レース結果(払戻 同梱・job は jra_results + jra_payouts)
#   scripts/jra_ops.sh payouts 2026-11-01                                  払戻金ページ(結果ページに払戻が無い日の補完)
#   scripts/jra_ops.sh baba    2026-11-01                                  馬場情報(含水率・クッション値・accessJ)= migration 0024 以降
#   scripts/jra_ops.sh fetch   2026-11-01 --save-dir DIR [--kinds ...]      収集工程(業務表・job 不変・オッズ raw は保存しない)
#   scripts/jra_ops.sh audit   2026-11-01 --from-dir DIR                    照合工程(DB 読み取り専用)
#
# 間隔は config/source-operations.json の `jra_official_interval_seconds` を HORSERACING_JRA_MIN_INTERVAL_S に写す。
# この wrapper は便利のためで安全境界ではない: CLI 自身が同じ検査(policy・env・共有リミッタ・DB)をして落ちる。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export DATABASE_URL="${DATABASE_URL:-postgresql+psycopg://aiuma:aiuma@localhost:15432/horseracing}"
SCRAPE_PY="$ROOT/scrape/.venv/bin/python"
POLICY="$ROOT/config/source-operations.json"

usage() { sed -n '2,13p' "$0"; exit 2; }
[ $# -ge 2 ] || usage
cmd="$1"; day="$2"; shift 2
[[ "$day" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}$ ]] || { echo "date must be YYYY-MM-DD" >&2; exit 2; }
case "$cmd" in entries|odds|results|payouts|baba|fetch|audit) ;; *) usage ;; esac

# policy → env (同じ鍵・同じ値。継承した値がより長ければそれを採る=短縮しない)
JRA_INTERVAL_S="$("$SCRAPE_PY" - "$POLICY" <<'PY'
import json, math, os, sys
policy = json.load(open(sys.argv[1]))
value = policy.get("jra_official_interval_seconds")
if type(value) not in (int, float) or isinstance(value, bool) or not math.isfinite(value) or value < 10:
    sys.exit("jra_official_interval_seconds must be a finite number >= 10: %r" % (value,))
inherited = os.environ.get("HORSERACING_JRA_MIN_INTERVAL_S")
if inherited:
    try:
        value = max(value, float(inherited))
    except ValueError:
        sys.exit("inherited HORSERACING_JRA_MIN_INTERVAL_S is not a number: %r" % (inherited,))
print(int(value) if float(value).is_integer() else value)
PY
)" || { echo "policy jra interval unusable ($POLICY); not starting" >&2; exit 2; }
export HORSERACING_JRA_MIN_INTERVAL_S="$JRA_INTERVAL_S"
export HORSERACING_SHARED_LIMITER=1

log() { printf '%s %s\n' "$(date '+%H:%M:%S')" "$*"; }
log "jra interval ${JRA_INTERVAL_S}s per request (shared limiter; policy $POLICY)"
exec "$SCRAPE_PY" -m horseracing_scrape "jra-$cmd" --date "$day" --policy "$POLICY" --json "$@"
