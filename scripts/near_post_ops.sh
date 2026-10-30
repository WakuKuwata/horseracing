#!/usr/bin/env bash
# 174 capture-only 運用の 1 日分を 1 コマンドずつにまとめた wrapper(specs/174-near-post-decision-quotes/manual.md の §1〜§3)。
#
#   scripts/near_post_ops.sh entries 2026-10-31 [--wait-for-draw]   前日 10 時過ぎ以降: list-races → 各レース scrape-entries(共有リミッタ 60 秒)→ post_time 検査
#   scripts/near_post_ops.sh morning 2026-10-31                      当日朝: robots 再取得(171・1 GET)→ near-post-capture --dry-run
#   scripts/near_post_ops.sh capture 2026-10-31                      当日: near-post-capture 本番(最初のレースの T−4 までに起動)
#   scripts/near_post_ops.sh results 2026-10-31                      夜: 各レース scrape-results(公式払戻・確定オッズ込み)→ 払戻件数検査
#   scripts/near_post_ops.sh score 2026-10-31 [2026-11-01] [cand...]  夜: 175 near-post-score(読み取り専用・JSON 1 件・候補名は任意)
#
# 新しい取得経路は作らない。既存 CLI を日付で束ねるだけ。policy(config/source-operations.json)は触らない。
#   scripts/near_post_ops.sh check 2026-10-31                        起動前: 共有リミッタの実効間隔(scrape/live が実際に使う値)を表示する
#
# すべての取得は DB 背後の共有リミッタを通る(093/172)。間隔は config/source-operations.json の
# `source_interval_seconds`(60)を環境変数 HORSERACING_NETKEIBA_MIN_INTERVAL_S に写して渡す。**この環境変数が無いと
# scrape/live の既定は 1 秒**で、2026-10-30 の 10/31 entries は 48 req を 1 req/秒で取ってしまった(実測・この wrapper の欠陥)。
# 10 未満・欠落・読めない policy では起動しない(下限は scripts/source_operations_172.py の MIN_SOURCE_INTERVAL_S と同じ・2026-10-31 に 60→10)。--wait-for-draw は枠順未確定(horse_number 欠落で
# parser が fail-closed)のあいだ 1 時間おきに 1 req だけ再試行する(最大 8 回)。
# 馬番付き出馬表(枠順)は JRA が「前日の 10 時過ぎ」に発表する(重賞以外。2026-10-29 木曜は 22 時まで空のまま=実測)。
# 前日 10 時より前に起動すると 8 回を無駄に使う。開催日の翌日分の entries は、その日の捕捉がすべて終わってから起動する
# (entries の取得が共有リミッタの 60 秒枠を使うと捕捉の 18 秒締切と取り合う)。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export DATABASE_URL="${DATABASE_URL:-postgresql+psycopg://aiuma:aiuma@localhost:15432/horseracing}"
SCRAPE_PY="$ROOT/scrape/.venv/bin/python"
LIVE_PY="$ROOT/live/.venv/bin/python"
PROFILE="$ROOT/config/.env.manual-observer"   # 171 の private profile(表示・コミット禁止)
POLICY="$ROOT/config/source-operations.json"  # 172 の policy(読むだけ・触らない)

# 情報源の間隔を policy から読み、共有リミッタの環境変数に写す(172 の apply_policy と同じ値・同じキー)。
# 継承した環境変数がより厳しければそちらを残す(弱める方向には倒さない=politeness._interval_for と同じ規律)。
SOURCE_INTERVAL_S="$("$SCRAPE_PY" - "$POLICY" <<'PY'
import json, math, os, sys
policy = json.load(open(sys.argv[1]))
value = policy.get("source_interval_seconds")
if type(value) not in (int, float) or not math.isfinite(value) or value < 10:
    sys.exit("source_interval_seconds must be a finite number >= 10: %r" % (value,))
inherited = os.environ.get("HORSERACING_NETKEIBA_MIN_INTERVAL_S")
if inherited:
    try:
        value = max(value, float(inherited))
    except ValueError:
        sys.exit("inherited HORSERACING_NETKEIBA_MIN_INTERVAL_S is not a number: %r" % (inherited,))
print(int(value) if float(value).is_integer() else value)
PY
)" || { echo "policy interval unusable ($POLICY); not starting" >&2; exit 2; }
export HORSERACING_NETKEIBA_MIN_INTERVAL_S="$SOURCE_INTERVAL_S"
export HORSERACING_SHARED_LIMITER=1

usage() { sed -n '2,15p' "$0"; exit 2; }
[ $# -ge 2 ] || usage
cmd="$1"; day="$2"; shift 2
[[ "$day" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}$ ]] || { echo "date must be YYYY-MM-DD" >&2; exit 2; }

DAYDIR="$ROOT/artifacts/near_post/$day"
ARCHIVE="$ROOT/artifacts/scrape_archive/$day"
RACES="$DAYDIR/races.tsv"
mkdir -p "$DAYDIR" "$ARCHIVE"
log() { printf '%s %s\n' "$(date '+%H:%M:%S')" "$*"; }
log "source interval ${SOURCE_INTERVAL_S}s per request (shared limiter; policy $POLICY)"

list_races() {
  if [ ! -s "$RACES" ]; then
    log "list-races $day (1 req)"
    (cd "$ROOT/scrape" && "$SCRAPE_PY" -m horseracing_scrape list-races --date "$day" --urls) | grep -v '^#' > "$RACES"
  fi
  log "$(wc -l < "$RACES" | tr -d ' ') races in $RACES"
}

sql() { "$SCRAPE_PY" - "$day" "$1" <<'PY'
import sys, psycopg, os
url = os.environ["DATABASE_URL"].replace("postgresql+psycopg://", "postgresql://")
with psycopg.connect(url) as c:
    print(c.execute(sys.argv[2], {"d": sys.argv[1]}).fetchall())
PY
}

entries_one() {  # $1 = shutuba url ; prints the CLI's summary line, never aborts the script (set -e)
  (cd "$ROOT/scrape" && "$SCRAPE_PY" -m horseracing_scrape scrape-entries --url "$1" \
      --archive-dir "$ARCHIVE" --database-url "$DATABASE_URL" 2>&1 | tail -1) || true
}

case "$cmd" in
  check)
    # scrape と live(chaos_politeness)は同じ source_interval_s を読むので、ここで見える値が両方の実効値。
    (cd "$ROOT/scrape" && "$SCRAPE_PY" - <<'PY'
from horseracing_scrape.politeness import source_interval_s
print("effective netkeiba interval: %.0fs" % source_interval_s("netkeiba.com"))
PY
    )
    ;;
  entries)
    wait_for_draw=0; [ "${1:-}" = "--wait-for-draw" ] && wait_for_draw=1
    list_races
    first_url="$(head -1 "$RACES" | cut -f2)"
    skip_first=0
    if [ "$wait_for_draw" = 1 ]; then
      for attempt in 1 2 3 4 5 6 7 8; do
        out="$(entries_one "$first_url")"; log "probe $attempt: $out"
        case "$out" in *"status=failed"*) log "枠順未確定とみなし 60 分待つ"; sleep 3600 ;; *) skip_first=1; break ;; esac
        [ "$attempt" = 8 ] && { log "枠順が出ないまま 8 回。終了"; exit 3; }
      done
    fi
    n=0; failed=0
    while IFS=$'\t' read -r rid shutuba _rest; do
      n=$((n+1))
      if [ "$n" = 1 ] && [ "$skip_first" = 1 ]; then log "entries 1: $rid (probe で取得済み)"; continue; fi
      log "entries $n: $rid"
      line="$(entries_one "$shutuba")"; echo "    $line"
      case "$line" in *"status=failed"*|"") failed=$((failed+1)) ;; esac
    done < "$RACES"
    log "entries failed: $failed / $n"
    log "post_time check (n, with_post_time, with_horse_number_rows):"
    sql "select count(*), count(post_time), (select count(*) from race_horses h join races r using(race_id) where r.race_date=%(d)s and h.horse_number is not null) from races where race_date=%(d)s"
    ;;
  morning)
    stamp="$(date +%Y%m%d-%H%M%S)"
    [ -r "$PROFILE" ] || { echo "profile missing: config/.env.manual-observer" >&2; exit 2; }
    log "robots refresh (171, 1 GET)"
    (cd "$ROOT" && "$SCRAPE_PY" -m scripts.roi_explore.robots_refresh_171 --profile "$PROFILE" --out "$ROOT/artifacts/robots_refresh/$stamp")
    log "near-post-capture --dry-run"
    (cd "$ROOT/live" && "$LIVE_PY" -m horseracing_live near-post-capture --date "$day" --dry-run --out "$DAYDIR/dry-$stamp.json" --features-dir "$ROOT/artifacts/near_post")
    ;;
  capture)
    stamp="$(date +%Y%m%d-%H%M%S)"
    log "near-post-capture LIVE (ends after the last race)"
    (cd "$ROOT/live" && "$LIVE_PY" -m horseracing_live near-post-capture --date "$day" --out "$DAYDIR/capture-$stamp.json" --features-dir "$ROOT/artifacts/near_post")
    ;;
  results)
    list_races
    n=0
    while IFS=$'\t' read -r rid _shutuba result _rest; do
      n=$((n+1)); log "results $n: $rid"
      (cd "$ROOT/scrape" && "$SCRAPE_PY" -m horseracing_scrape scrape-results --url "$result" \
          --archive-dir "$ARCHIVE" --database-url "$DATABASE_URL" 2>&1 | tail -1 | sed 's/^/    /') || true
    done < "$RACES"
    log "settlement check (races, races_with_results, official_win_payout_rows):"
    sql "select count(distinct r.race_id), count(distinct res.race_id), (select count(*) from official_win_payouts p join races x using(race_id) where x.race_date=%(d)s) from races r left join race_results res using(race_id) where r.race_date=%(d)s"
    ;;
  score)
    to="$day"; cands=()
    if [ $# -ge 1 ] && [[ "$1" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}$ ]]; then to="$1"; shift; fi
    for c in "$@"; do cands+=(--candidate "$c"); done
    stamp="$(date +%Y%m%d-%H%M%S)"
    mkdir -p "$ROOT/artifacts/near_post/score"
    log "near-post-score $day..$to ${cands[*]:-}"
    (cd "$ROOT/live" && "$LIVE_PY" -m horseracing_live near-post-score --from "$day" --to "$to" "${cands[@]}" \
        --features-dir "$ROOT/artifacts/near_post" --out "$ROOT/artifacts/near_post/score/${day}_${to}_$stamp.json")
    ;;
  *) usage ;;
esac
