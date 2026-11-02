# Contract: CLI(`scrape jra-*`)と運用 wrapper

すべて `uv run --project scrape python -m horseracing_scrape <command>`(cwd は任意)。手動実行のみ(FR-022)。運用は `scripts/jra_ops.sh <command> <date>` が `config/source-operations.json` の `jra_official_interval_seconds` を `HORSERACING_JRA_MIN_INTERVAL_S` に写してから呼ぶ。

## 共通の起動時検証(どのコマンドも・1 件も送る前)

| 検査 | 失敗時 |
|---|---|
| policy ファイルが読め、`jra_official_interval_seconds` が数値・有限・≥10 | exit 2・`policy_invalid`・HTTP 0 件 |
| env `HORSERACING_JRA_MIN_INTERVAL_S` が policy 値以上(無ければ policy 値を自分で写す) | 短い値は `would_shorten_source_interval`・HTTP 0 件 |
| `HORSERACING_SHARED_LIMITER` が無効化されていない・DB に到達できる | `NoSharedLimiter`・HTTP 0 件 |
| robots キャッシュ(24h)が無ければ 1 件取得(共有スロット経由・捕捉時間帯チェックつき) | 403/429 → `FetchRefused`・停止 |

共通オプション: `--date YYYY-MM-DD`(JST・既定=今日)、`--dry-run`(取得と解析まで・業務表/job/来歴を書かない・throttle 行は動く)、`--json`(summary を JSON で標準出力)、`--database-url`。

## コマンド

| コマンド | 取るページ | 作る job(job_type) | 書く先 |
|---|---|---|---|
| `jra-entries --date D [--race-id RID ...]` | 索引 D → 開催日(各場)→ 出馬表 1 ページ/場 + 変更情報(accessI) | `jra_entries`, `jra_changes`(同じ取得ページを共有) | races / race_horses / horses / jockeys / trainers / 来歴 |
| `jra-odds --date D [--race-id RID ...]` | 索引 O → 開催日 → 単複オッズ 1 ページ/レース(`--race-id` 無しは全レース) | `jra_odds` | race_horses.odds/popularity・place_odds_* / races.*_odds_*_at / 来歴 |
| `jra-results --date D [--race-id RID ...]` | 索引 S → 開催日 → 結果 1 ページ/場(払戻は同じページの `refund_area`) | `jra_results`, `jra_payouts` | race_results / race_laps / race_horses(体重・状態) / official_win_payouts / exotic_odds / 来歴 |
| `jra-payouts --date D` | 索引 H → 開催日 → 払戻 1 ページ/場 | `jra_payouts` | official_win_payouts / exotic_odds / 来歴(結果ページに払戻が無い日の補完) |
| `jra-baba --date D` | `/keiba/baba/_data_cushion.html`・`_data_moist.html`・`POST accessJ` | `jra_baba` | jra_track_conditions(0024 以降) |
| `jra-fetch --date D --save-dir DIR [--kinds ...]` | 上記の任意のページ | なし(業務表・job 不変・throttle 行のみ) | DIR に raw + manifest(収集工程) |
| `jra-audit --date D --from-dir DIR [--json]` | なし(DIR の raw を読む) | なし(`SET TRANSACTION READ ONLY`) | 標準出力のみ |

`--race-id` を与えたコマンドは、開催日ページから当該レースのブロックだけを書く(ページは丸ごと取る)。公式経路は `race_date < 2025-10-11` のレースを書かない(`skipped_pre_netkeiba_era`)。

## 終了コード

0=SUCCEEDED / SKIPPED(unsupported_date 含む)、1=PARTIAL(隔離・不一致・解析失敗・捕捉時間帯停止を含む)、2=FAILED(起動時検証・拒否・辿り直し上限・DB 障害)。

## summary の名前つき件数(`Counts.extra` → `ingestion_jobs.summary`)

| キー | 意味 | 出す job |
|---|---|---|
| `requests_sent` | HTTP 送信数(robots・索引・辿り直し・リダイレクト先を含む)=SC-005 の測定値 | 全部 |
| `rewalks` | 索引からの辿り直し回数(上限 1) | 全部 |
| `stopped_for_capture_window` | 捕捉時間帯で止めた(1/0) | 全部 |
| `unsupported_date` | 索引に無い日付(1/0) | 全部 |
| `races_published` / `races_not_yet` / `races_cancelled` / `races_parse_failed` | 開催日ページのレース状態 | entries / results / payouts |
| `skipped_pre_netkeiba_era` | 2025-10-11 より前のレース | entries / odds / results |
| `quarantined_unknown_id` | 未知 ID を含むレース | entries / odds / results |
| `quarantined_collision` | 衝突検査で隔離したレース | entries / odds / results |
| `quarantined_cancel_unresolved` | 取消馬の馬番が復元できなかったレース | entries |
| `merge_candidates` / `approved_mapping_pending_rekey` | 同一馬候補の対(内容は error_message) | entries / audit |
| `master_name_mismatch` | 同 ID 別名のマスタ(馬/騎手/調教師) | entries / results |
| `static_mismatch` | 静的項目の不一致(報告のみ or 公式で上書き) | entries |
| `status_transitions` / `status_rejected` | 状態遷移の採用・拒否(古い出走表の started 等) | entries / changes / results |
| `post_time_changes` / `jockey_changes` | 変更情報の反映件数 | changes |
| `odds_written_races` / `odds_skipped_stale` / `odds_partial_field` / `odds_conflicts` / `odds_prev_day_final_ignored` | オッズの共通契約の判定件数 | odds |
| `results_inserted_races` / `results_mismatch` / `results_existing_races` | 結果の race-atomic 挿入と不一致 | results |
| `laps_inserted` / `laps_existing` | ハロンタイム | results |
| `payout_complete` / `payout_not_published` / `payout_not_sold` / `payout_parse_failed` / `payout_rows_written` / `payout_rows_removed` / `not_mapped_bet_types` / `refunds` | 券種ごとの状態と集合置換 | results / payouts |
| `baba_rows_written` / `baba_value_changed` / `baba_rejected_future` | 馬場 | baba |

netkeiba 側の既存 job にも Phase 1 から `quarantined_collision` / `status_rejected` / `odds_partial_field` / `odds_conflicts` / `skipped_official_payouts` / `static_mismatch` が増える(両経路共通の判定の可視化)。

## 標準出力 JSON(`--json`)

```json
{"command": "jra-entries", "date": "2026-11-01", "jobs": [{"job_type": "jra_entries", "status": "partial", "processed": 173, "written": 161, "skipped": 0, "errors": 0, "extra": {"requests_sent": 4, "quarantined_collision": 1, "...": 0}}],
 "quarantined": [{"race_id": "202605041105", "reason": "collision", "pairs": [["nk:2022105102", "2022105102"]]}],
 "mismatches": [{"race_id": "202605041111", "horse_number": 7, "field": "jockey_weight", "stored": "57.0", "official": "58.0", "stored_source": null}]}
```

`stored_source` は来歴が無ければ `null`(netkeiba と決めつけない)。
