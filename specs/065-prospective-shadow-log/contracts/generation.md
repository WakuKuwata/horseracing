# Contract: generation — prospective win recommendation (betting + live)

## `recommend.generate_recommendations(session, *, prediction_run_id, ..., win_odds_cap=None, prospective=False, odds_asof=None)`

- **追加引数** `prospective: bool = False`・`odds_asof: datetime | None = None`。
- `prospective=False`(既定)→ 現行と**完全に同一挙動**(logic_version・行バイト同等)。
- `prospective=True` → logic_version に `;prospective=1;odds_asof=<iso>` を追記。**custom logic_version が渡されても marker が消えないよう、custom/default 解決後に付与**(または prospective 時は custom logic を拒否)=codex 指摘。それ以外の選定/確率/Kelly は不変。odds_asof は ISO8601 の**オッズ捕捉時刻**(収集フローの scrape/capture 時刻・RaceHorse.updated_at は使わない)。
- 予測 p・選定・凍結オッズ(market_odds_used)の意味論は不変。marker は監査文字列のみ。

## `live.orchestrate.collect_prospective(session, *, date | date_from/date_to) -> ProspectiveReport`（新）

- 対象 = 指定日/範囲の **result-pending**(`guards.is_result_pending`)かつ発走前オッズのある started 馬を持つレース。
- 各レースで(**capture 規律**=codex 指摘):
  1. **同一フローで発走前オッズを fresh scrape**(008)し、その **capture 時刻**を odds_asof にする(RaceHorse.updated_at は使わない)。
  2. post_time が既知なら **capture < post_time** を要求。post_time 未知なら「弱保証」フラグを marker/集計で区別。
  3. **scrape 直後・insert 直前に** `guards.is_result_pending` を再確認(fail-closed)。**advisory-lock**(race/model/policy)で check-then-insert 競合を防ぐ。
  4. アクティブ run に対し **WIN 推奨**(`generate_recommendations(prospective=True, odds_asof=...)`)を生成 — live_serve の exotic 経路(`generate_kelly_recommendations`)ではなく **WIN 経路を明示的に呼ぶ**。
- **run 跨ぎ policy-aware 冪等**: (race, model, prospective marker) 群が既存なら skip。run 単位の `_has_win_group` は live append-only の新 run で重複するため、**run をまたいで**同一 (race, model, prospective policy) の既存を検出する(codex 指摘)。backfill 群・oddscap policy とは別群(4通り: legacy/cap/prospective/prospective+cap が衝突しない)。
- exotic は対象外(win-only)。one-shot per (race, policy)=締切直前 late-market の cherry-pick を防ぐ。

## CLI

- `live collect-prospective --date <d>`(単日)/ `--from --to`(範囲)。既存 scrape/settle は別コマンドのまま束ねる(019/050 流用)。
- 冪等(再実行で重複記録なし)。result-pending でないレースは必ず skip(理由付き)。

## テスト

- `test_prospective_off_is_byte_identical`: prospective=False で recommendation 行 + lv が現行と一致。
- `test_prospective_marker_appended_after_custom_logic_or_rejected`: custom logic_version でも marker が消えない(解決後付与 or 拒否)。
- `test_has_win_group_distinguishes_prospective_and_oddscap_policy`: legacy/cap/prospective/prospective+cap が衝突しない(run 跨ぎ)。
- `test_generate_recommendations_prospective_rejects_result_present_race`: 結果ありレースは生成拒否(fail-closed)。
- `test_collect_prospective_rechecks_pending_after_scrape_before_insert`: フロー途中で結果が入ると marker を付けない。
- `test_collect_prospective_repeated_runs_no_duplicate_across_prediction_runs`: run 跨ぎで prospective 行が重複しない((race,model,policy)冪等)。
- `test_collect_prospective_skips_stale_or_missing_odds`: 古い/部分オッズでは marker 付き行を作らない。
- `test_collect_prospective_generates_win_rows_not_exotic_only`: WIN 経路を生成(exotic のみでない)。
- `test_backfill_not_marked_prospective`: 044/064 backfill 経路は marker を付けない。
- leak-guard: marker/凍結オッズ/結果がモデル特徴に流入しない・selection は results 非参照。
