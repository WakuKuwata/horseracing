# 174 運用手順(capture-only の開催日)

前提: `collection_paused=true` のまま。worker は起動しない。ops の更新 POST は 503 のまま。以下はすべて人が起動する。

**短縮形(2026-10-29 追加)**: 以下 §1〜§3 を日付 1 つで束ねた wrapper が `scripts/near_post_ops.sh` にある(既存 CLI を順に呼ぶだけ・policy は触らない・取得は共有リミッタ 60 秒)。

```bash
scripts/near_post_ops.sh entries 2026-10-31 --wait-for-draw   # 前日 10 時過ぎ以降(枠順未確定なら 1 時間おきに 1 req で再試行・最大 8 回)
scripts/near_post_ops.sh morning 2026-10-31                    # 当日朝: robots 再取得(171)+ dry-run
scripts/near_post_ops.sh capture 2026-10-31                    # 当日: 本番(最初のレースの T−4 までに)
scripts/near_post_ops.sh results 2026-10-31                    # 夜: 結果+公式払戻
```

レース一覧は `artifacts/near_post/<date>/races.tsv`、取得ページの原本は `artifacts/scrape_archive/<date>/`、要約 JSON は `artifacts/near_post/<date>/` に残る。馬番付き出走表(枠順)は JRA が**前日の 10 時過ぎ**に発表する(重賞以外・JRA 告知 2026-10 で確認。土曜分は金曜、日曜分は土曜)。それより前の出走表は parser が `missing required int: horse_number` で fail-closed になり何も書かない(2026-10-29 木曜は 14:50〜21:57 の 9 回すべて枠・馬番セルが空=実測。騎手・斤量は出ている)。開催日の翌日分の entries は、その日の捕捉がすべて終わった夕方に起動する(entries の取得が共有リミッタを使うと捕捉の 18 秒締切と競合する)。

## 0. 承認の登録(開催日より前・1 回)

`config/source-operations.json` に 3 キーを同時に足す(列挙に無い日は CLI が拒否する):

```json
"capture_only_dates": ["2026-11-07", "2026-11-08"],
"capture_only_approved_by": "owner",
"capture_only_approved_at": "2026-10-29T10:00:00+09:00"
```

`scripts/source_operations_172.py` の `load_policy` がこの形を検証する(3 つ同時・ISO 日付・重複なし・`collection_paused` は true のまま)。

## 1. 前日(出馬表と発走時刻)

```bash
cd scrape && uv run python -m horseracing_scrape list-races --date 2026-11-07
```

出た race_id ごとに `scrape-entries`(URL 指定)を 60 秒間隔で実行する(36 req)。`races.post_time` が全レースで入ることを確認:

```bash
psql "$DATABASE_URL" -c "select count(*) n, count(post_time) with_post from races where race_date='2026-11-07'"
```

## 2. 当日(起動 24 時間以内に robots)

```bash
cd training && uv run python ../scripts/roi_explore/robots_refresh_171.py --out ../artifacts/robots_refresh/$(date +%Y%m%d)
```

試運転(fetch も保存もしない。時刻とゲートだけ):

```bash
cd live && uv run python -m horseracing_live near-post-capture --date 2026-11-07 --dry-run --out ../artifacts/near_post/2026-11-07-dry.json
```

本番(最初のレースの T−4 分までに起動。最終レース後に自分で終了する):

```bash
cd live && uv run python -m horseracing_live near-post-capture --date 2026-11-07 --out ../artifacts/near_post/2026-11-07.json
```

- 起動時に policy の日付・今日(JST)・robots の鮮度・出走表の有無を検査し、満たさなければ終了コード 2(何も取得しない)。
- 1 レース 1 req・締切 18 秒・共有リミッタ 60 秒。拒否(`FetchRefused`)が出たら以後のレースは `stopped_after_refusal` で終了コード 3。
- 要約 JSON の `in_window_capture_rate`(受入 ≥0.95)と `official_lag_median_s`(受入 ≤60)を開催日ごとに残す。

## 3. 夜または翌朝(結果と公式払戻)

各レースの結果ページを `scrape-results`(URL 指定)で取り込む(36 req)。139 の `apply_final_odds` と `official_win_payouts` が同時に入る。精算は読み取り時に結合するので、`decision_quotes` 側の操作は無い。

## 4. 確認クエリ

```sql
select race_id, capture_target, seconds_to_post, official_at, captured_at, n, mix_status
from decision_quotes where captured_at::date = '2026-11-07' order by captured_at;
```

同じレースに 2 行はできない(UNIQUE)。行の更新・削除はトリガで拒否される。

## 5. やってはいけないこと

- `capture_only_dates` に無い日の起動、`collection_paused` を false にすること、worker の起動、refresh の反復。
- T−10 の 2 点目(v1 では持たない)。
- 保存行の訂正(append-only)。出走取消などは読み取り側が `field_digest` で検出する。


## §4 夜の 4 番目(175・採点)

`results` の後に `scripts/near_post_ops.sh score <日付> [<終端日>] [候補名...]` を 1 回。読み取り専用で
`artifacts/near_post/score/<from>_<to>_<stamp>.json` を 1 件書く(上書きしない)。先頭行は 300 点未満のあいだ
三値が `insufficient` である旨。funnel の合計が行数に一致すること、`payout_race_missing` が 0 であること
(0 でなければ `results` の取り直し)を見る。候補名は `specs/175-decision-quote-scoring/candidates.json` の
`name`(現在 `ens15-norm148-v1`)。候補を付けると朝の parquet(`artifacts/near_post/<日付>/features-*.parquet`・
`capture`/`morning` が書く)から現行を再予測し、ビット一致したレースだけで候補を精算する。
