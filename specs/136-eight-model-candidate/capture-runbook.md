# 133 capture 運用手順（2026-09-20 作成・翌開催日から適用）

**これは 133 の律速。統計ではなく取りこぼしで決着が決まる。**

> **先に必ず**（`capture` も `score` も `DATABASE_URL` が無いと
> `RuntimeError: DATABASE_URL is not set` で即死する。実行して確認済み）
>
> ```bash
> export DATABASE_URL="postgresql+psycopg://aiuma:aiuma@localhost:15432/horseracing"
> ```
主条件 `preweight` は **capture でしか埋まらない**（`from-production` は `serving` しか書かない
— `paired_prospective.py:855`）。現在 **24 開催日中 1 日**しか埋まっていない。

## 手順 0（**先に必要**）— その開催日を DB に取り込む

**2026-09-20 時点で 9/21 のレースは DB に 1 件も無い。** この状態で capture を回しても
**0 件しか記録されない**（capture は DB のレースを列挙する）。

取り込みは**自動では起きない**。`refresh_day` の実行履歴は時刻がばらばらで、
**手動実行**であることが分かる。実績:

| 実行 (JST) | 対象日 | 結果 |
|---|---|---|
| 9/12 23:13 | **2026-09-13** | 24 レース discover |
| 9/19 19:44 | **2026-09-20** | 24 レース discover |
| 9/20 09:15 | 2026-09-20 | 再取得 |

**つまり前日の夜に翌日ぶんを叩いている。** 9/20 のレースと出馬表は
**9/19 19:44 JST に作成されている**（DB の `created_at` 実査）。

```bash
curl -X POST http://127.0.0.1:8001/ops/v1/days/2026-09-21/refresh
```

エンドポイントは **DB にまだレースが無い日付も受け付ける**（worker が netkeiba から discover する）。
その日に開催が無ければ 0 件で返る。開催の有無は discover を回すまで確定しない。

**2026-09-21（月・敬老の日）の実績**: 開催はあったが **阪神のみ 12 レース**だった。
過去 7 年の敬老の日はいずれも 24 レースで、私はそれを根拠に 24 レースと見込んだが**外れた**。
**開催の有無は過去実績で推測できるが、レース数と発走時刻は discover しないと分からない。**
参考（DB 実査）:

| 年 | 敬老の日 | レース数 |
|---|---|---:|
| 2025 | 09-15 (Mon) | 24 |
| 2024 | 09-16 (Mon) | 24 |
| 2023 | 09-18 (Mon) | 24 |
| 2022 | 09-19 (Mon) | 24 |
| 2021 | 09-20 (Mon) | 24 |
| 2020 | 09-21 (Mon) | 24 |
| 2019 | 09-16 (Mon) | 24 |

また 9/19（日 05）・9/20（日 06）と続いているので、同一開催の 3 日目にあたる。
**「月曜だから開催は無い」と判断しないこと。**

**前日夜（推奨）**に流し、翌朝の capture の前にもう一度確認する:

```bash
docker exec docker-postgres-1 psql -U aiuma -d horseracing -At \
  -c "SELECT count(*) FROM races WHERE race_date='2026-09-21';"
```

**0 のまま朝を迎えたら、その開催日の主条件は取れない。**

## 実行時刻 — 09:45 JST より前。推奨 09:15 と 09:40 の 2 回

実測（`records/days/` の台帳より）:

| 日 | capture 実行 (JST) | 第 1 レース発走 (JST) | 結果 |
|---|---|---|---|
| 9/13 | **09:43:36** | 09:45 | 24/24 取得 |
| 9/19 | **未実行** | 09:45 | **主条件を恒久的に喪失** |
| 9/20 | **10:00:00** | 09:45 | 1R 2 レースが発走済みで取得不能 |
| **9/21** | **未実行** | 10:00 | **12 レースが丸ごと 133 に無い**（取込は 08:39 に成功していた） |

**9/21 の扱いは利用者判断で「保留」**（2026-09-22）。`from-production` は**実行しない**。
12 レースすべてに発走前の `mix-129-nj6` 本番 run があるので記録の作成自体は後からでも可能だが、
書けるのは副条件 `serving` だけで、主条件は `capture` でしか埋まらず発走済みなので不可。
結果が既知になった後で記録を足す判断になるため、**窓内の全日へ一律に適用する規則を先に決めてから**
でなければ選択バイアスになる。**急がなくてよい**（保留レコードにも過去日にも期限は無い）。

**第 1 レースの発走は日によって動く。** 実測: 9/13・9/19・9/20 は **09:45**、9/21 は **10:00**。
capture 自体は約 26 秒で終わる。**09:45 を上限として扱えば全日で安全側**になる。

**2 回走らせるのが正しい**。理由は失敗の非対称性:

- **早すぎた場合**: まだ出馬表・オッズが取り込まれていないレースが skip されるだけ。
  **2 回目で回収できる**（同一レースの再記録は `record already exists (append-only)` で
  skip されるので重複しない — `paired_prospective.py:690`）。
- **遅すぎた場合**: 発走済みレースは `capture` が正しく拒否し、**主条件は二度と埋まらない**。

## コマンド

```bash
cd /Users/kuwatawaku/workspace/horseracing
export DATABASE_URL="postgresql+psycopg://aiuma:aiuma@localhost:15432/horseracing"
W=artifacts/129-candidate-mixture-serving
R=artifacts/133-prospective-paired-comparison/records
serving/.venv/bin/python -m horseracing_serving.paired_prospective capture \
  --date $(date +%F) \
  --bundle $W/bundle.json \
  --bundle-sha256 28f39715a9c1dba13755ee44f3a439099d5aaffd94685ac11e4d23166b388471 \
  --anchor $W/anchor/anchor.json \
  --out-dir $R
```

`--date` は**開催日**（JST）。前日夜に流すなら明示的に翌日の日付を書くこと。

## 実行後に確認すること

1. 台帳 `$R/days/<date>.*.json` の `dispositions` を見て、**skip の理由**を読む。
   `Prospective capture requires a future scheduled start` が出ていたら**遅すぎた**。
2. **発走済み skip があっても、その日の残りは記録される**
   （`capture` はレース単位で理由つき skip し、続行する — `paired_prospective.py:539,562`）。
   重複記録も per-race の skip で、実行全体は失敗しない（`:686-693`）。
3. 記録件数がその日のレース数と一致するか。
   ```bash
   docker exec docker-postgres-1 psql -U aiuma -d horseracing -At \
     -c "SELECT count(*) FROM races WHERE race_date='$(date +%F)';"
   ls $R/capture | wc -l
   ```
4. 主条件が入っているか（`regimes` に `preweight` があること）。
   `from-production` で埋めた日は `serving` しか入らない。

## 開催日の夜にやること（2 つある）

結果は**当日中には出揃わない**。実測（`race_results.created_at`・JST）:

| 開催日 | 最終発走 | 結果行の作成 |
|---|---|---|
| 9/13 | — | 09-13 15:36 → **09-14 08:11** |
| 9/19 | — | 09-19 09:50 → **09-20 09:18** |
| 9/20 | **16:30** | （本日進行中） |

**最後の数件は翌朝に入る。** したがって夜に流すべきは 2 つ:

```bash
# 1. その日の結果を取り込む（最終発走 16:30 JST より後に）
curl -X POST http://127.0.0.1:8001/ops/v1/days/2026-09-20/refresh

# 2. 翌開催日のレースを DB に入れる（これをしないと翌朝の capture が 0 件）
curl -X POST http://127.0.0.1:8001/ops/v1/days/2026-09-21/refresh
```

翌朝、capture の前に **1 をもう一度**流すと前日の取りこぼしが埋まる（結果の最後の数件は翌朝に来るため）。

結果が揃ったかの確認:

```bash
docker exec docker-postgres-1 psql -U aiuma -d horseracing -At -c "
SELECT race_date::text||' races='||count(*)::text||' with_results='||count(*) FILTER (WHERE has_res)::text
FROM (SELECT r.race_id, r.race_date, EXISTS(SELECT 1 FROM race_results x WHERE x.race_id=r.race_id) has_res
      FROM races r WHERE r.race_date>=CURRENT_DATE-2) t GROUP BY race_date ORDER BY race_date;"
```

## 結果が入ってからの interim

結果は当日夜〜翌朝の取込後。interim は**いつ流してもよい**（登録の `interim_policy` が明示的に許可）。
**`--mode final` は窓終了（12/28 JST）まで絶対に流さない。**

```bash
export DATABASE_URL="postgresql+psycopg://aiuma:aiuma@localhost:15432/horseracing"
P=specs/133-prospective-paired-comparison
R=artifacts/133-prospective-paired-comparison/records
serving/.venv/bin/python -m horseracing_serving.paired_prospective score \
  --records $R/capture --records $R/production_snapshot \
  --records artifacts/129-candidate-mixture-serving/shadow/prospective \
  --preregistration $P/preregistration.json \
  --mode interim --regime preweight \
  --output artifacts/136-eight-model-candidate/133-status/interim-preweight-$(date +%Y%m%d-%H%M).json
```

`--looks-log-dir` を**付けなければ** `$P/evidence/looks.jsonl` に追記されない。
公式の look として残したいときだけ付ける。出力は `status=INTERIM_STATUS_NOT_A_VERDICT`・
`verdict=null`・`can_adopt=false` になっているはず。なっていなければ止めて調べること。

**この score コマンドは 9/20 に実行して動作を確認した**（`n_races=23 / n_days=1 /
diff_winner_nll=-0.034810632`・独立担当の別実行と一致・`looks.jsonl` は 6 行のまま）。

`--regime serving` は副条件。**正式判定は出ない**（`SECONDARY_SUMMARY_NOT_A_VERDICT`）ので、
記述用途としてのみ読む。

## 記録そのものを失わないこと（唯一の実リスク）

**`artifacts/` は gitignore されている**（`.gitignore:20`）。つまり
`artifacts/133-prospective-paired-comparison/records/` は **evidence の唯一の原本**である。

- **消さない・移動しない・そのディレクトリを持たないチェックアウトに切り替えない。**
- 12/28 まで残す。バックアップ対象に入れる。

逆に、**記録の側には運用上の締切が無い**ことを独立担当が現行コードで確認した
（全文 `artifacts/136-eight-model-candidate/133-status/133-pending-safety.md`）:

- **保留レコードに期限・GC・失効は無い。** 記録は毎回ファイルから読み直され、
  `pending`/`excluded` はその都度計算されて記録に書き戻されない（`:1086-1090`, `:1214-1248`）。
  書き込みは create-only なので、誤って再実行しても既存記録を壊せない（`:120-142`, `:868-870`）。
  `score` は何も書き換えない。
- **結果が一部しか入っていない状態は `results_incomplete` の PENDING** になり、
  次回実行で採点に回復する（`:1234-1235`）。結果 0 件のガードは field 比較より**前**にあるので
  （`:1223-1224`）、「結果がまだ来ていない」だけで誤除外されることはない。
- **`field_changed_after_capture` は capture 後の取消が原因で、運用では避けられない。**
  実 DB で 2 件とも取消と確認（9/13 `202606040404`・9/19 `202606040508`）。
  早く取り込んでも、遅らせても、再実行しなくても変わらない。発生率は確定 48 レース中 2 件（約 4%）で、
  **レースを時々失うだけで、開催日ごと失うことはない。**

→ **今夜あわてて何かする必要は無い。** 今夜必要なのは上の 2 つの `refresh_day` だけ。

## 残り日数の現実

登録窓 2026-09-13 〜 2026-12-28・`min_eval_days` = **24**。
主条件は現在 **1 日**（9/20 の結果が入れば 2 日）。残る開催日は概算 29〜32 日に対し
**22 日必要**で、**取りこぼせるのは 7〜10 日**。窓は延長されない。

**1 日落とすごとに余裕が 1 日減る。** 9/19 で既に 1 日使っている。
