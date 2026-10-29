# 174 実装の記録(2026-10-29)

**出荷したもの**(本番 DB への migration 適用は未実施・push なし):

| 層 | 内容 |
|---|---|
| training | `market_ev.market_structure(df)` を抽出(`build_features` は同関数を呼ぶ)。パリティテスト 2 件: 抽出前後で 13 列が check_exact で一致(index を混ぜても)・quote を差し替えると市場 13 列だけ動く |
| db | migration `0022_decision_quotes`(UNIQUE(race_id)・CHECK・append-only トリガ・TRUNCATE 拒否)+ ORM `DecisionQuote`。統合テスト 4 件(列・UNIQUE・UPDATE/DELETE/TRUNCATE 拒否・CHECK)。features/live の head 断言を 0022 に更新・`_TABLES_ADDED_AFTER_0012` に追加 |
| live | `near_post.py`: policy/日付/robots ゲート・`schedule_day`(同時刻は race_id 順で 60 秒ずつ後ろへ)・`DayFeatures`(朝 1 回・sha256)・`ProductionPredictor`(15 seed+単 seed+141 混合・141 と同じ `model_mismatch` 検査)・`capture_race`(fetch 前 9 段・fetch 後 4 段の再確認・advisory lock・18 秒締切・`begin_nested` 保存)・`run_day`(窓まで sleep・レースごとに commit・拒否で停止)。CLI `near-post-capture`。単体 14 件・統合 7 件 |
| scripts | `source_operations_172.load_policy` が `capture_only_dates`/`_approved_by`/`_approved_at` を 3 つ同時にだけ受理(ISO・重複なし・`collection_paused` は true のまま)。テスト 9 件追加(52 件緑) |
| features | leak-guard `test_decision_quotes_leak_guard.py`(features が `decision_quotes` を参照しない) |
| docs | `manual.md`(前日 entries→当日 robots+dry-run→本番→夜 results)・憲法 1.0.1(V の注記) |

## 本番経路との一致(実 DB 読み取り・2026-10-25・21 レース)

`ProductionPredictor` + `rows_with_quote`(朝の特徴行に保存 `odds_used` と現在の `popularity` を差し替え)で得た p_ens と、保存済み `market_ev_predictions.win_prob` の差: 2 レースで厳密 0、他は最大 0.0027。差は保存時点以降に `race_horses.popularity`(確定人気)や馬場・天候が更新されたことによる入力差で、経路の差ではない(odds・popularity 以外の列は同じ `build_features`)。15 seed の manifest は `ensemble_2026.json`、単 seed は `model_2026.txt`、混合は 21 レースすべて `available`。朝の特徴構築は 455 行で 14 秒(DB ロード込み)。

## ゲートの実機確認

`near-post-capture --date 2026-10-29 --dry-run`(ローカル DB)は policy の `capture_only_dates` に無いので `REFUSED … exit 2`。外部 req 0。

## セルフレビュー(codex unavailable・400)

- 集合の一致: fetch 後に started 集合の不変と quote の被覆(全 started 馬に ≥1.0 の価格)を検査し、欠ければ保存しない(テスト `quote_incomplete`/`field_mismatch`)。
- 市場 12 列: `market_structure` 1 実装を training と live が共有(研究用 `r02_drift.market_block` の複製は使わない)。
- 礼儀・締切: `make_capture_fetcher(deadline_s=18)`・共有リミッタ・robots は起動時に鮮度を要求し、プロセス内で再取得しない。
- ゲートは fail-closed: policy 不在/キー欠落/日付未登録/今日でない/robots 失効/出走表なし → 何も取得せず終了 2。
- dry-run: fetcher を作らず(None)捕捉は `would_fetch` で止まる(統合テストで fetch 0・保存 0)。
- append-only: DB トリガで UPDATE/DELETE/TRUNCATE 拒否(統合テスト)。
- 憲法 II: features の leak-guard。V: 1 レース 1 行・`race_horses.odds` 不変。
- 限界: 本番 DB への migration 0022 の適用と ops/api の再起動は運用手順(開催日前)。読み取り API/ページは未着手(後段 spec)。T−3 の実機では netkeiba の応答時間と共有リミッタの待ちが 18 秒に収まるかを試運転で確認する(086 の実測では 1 req 数秒)。

## テスト

training 693(+2・7 skip)/ db decision_quotes 4+chaos 5 / live 単体 14+統合 7(全体は別途)/ scripts 172 52 / features 49(head 断言)+2 / ruff 緑。
