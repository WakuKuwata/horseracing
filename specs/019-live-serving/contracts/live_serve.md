# Contract: ライブ serving（CLI / orchestration）

新規 `live/`（horseracing-live）が scrape(008)/serving(006)/betting(011,016) を結線。スキーマ変更なし。

## コマンド（例）

```
live live-serve <race_id> [--model-version <mv>] [--no-recommend] \
  [--p-gamma <g>] [--q-gamma <g>] [--haircut-type relative --haircut 0.05] [--bankroll 100]
live list-pending --date <YYYY-MM-DD>        # result-pending かつ valid race_id を列挙
```

## live-serve フロー（guard-first, fail-closed）

1. **guard**: valid_race_id（`^[0-9]{12}$`）/ result_pending（race_results 行なし）を満たすか。違反→拒否（書込なし）。
2. **scrape**（任意・URL 駆動）: `--scrape-entries-url`/`--scrape-odds-url` 指定時のみ 008 `scrape_entries`/
   `scrape_odds`（idempotent、pre-race odds は result-pending のみ上書き）。無指定時は既存 DB 状態で続行
   （008 を別途実行済み前提）。**race_id→netkeiba URL 自動逆引きは deferred**。
3. **guard**: entries_complete（started≥1・horse_number 揃い・重複/頭数整合）。違反→fail-closed。
4. **predict**: `run_serving(race_id, model_version)` → prediction_run/race_predictions（as-of、結果非参照、
   cutoff=race_date、check_consistency）。新馬/unmapped は Unknown + 出走頭数に含む。
5. **guard**: odds_present（対象出走集合に pre-race win オッズ）。欠損→推奨スキップ（予測は保持）。
6. **recommend**: 009→010(pre-race odds)→011/016。estimated（double-pseudo）。使用オッズ値 + computed_at +
   logic_version を recommendations に append-only 保存。013/017 校正器 opt-in。live Kelly は shadow。
7. **report**: LiveServeReport（guards/scrape/prediction_run/recommendations/odds_as_of/computed_at/shadow）。

## 不変条件

- result-pending かつ valid id かつ entries 完全でなければ予測しない。odds 欠損で推奨しない（fail-closed）。
- features は結果を読まない（II）。odds/stake はモデル特徴に戻さない。cutoff=race_date。
- 推奨は append-only + 使用オッズ値保存（as_of 単独依存しない）。決定論（同一入力→同一出力）。
- live Kelly は shadow（実資金執行なし）。スキーマ変更なし。

## 評価（結果不在の代替、US3）

- **p パリティ**: 過去レースで live 経路（cutoff=race_date）== retrospective の予測 p 一致（リーク無し）。
  オッズ依存の推奨/EV は過去パリティ対象外（過去 pre-race odds 非保持）。
- **リーク境界**: race_results 変更で予測不変。
- **prospective**: computed_at + 使用オッズ値で残し後日 backtest（007/011/016）投入可能。
