# Contract: betting — win odds-cap selection

## 命名規約(統一・codex 衝突回避)

exotic 経路 `kelly_recommend.py` の既存 `odds_cap`(推定 exotic 上限)と区別するため、win 経路の命名を固定する:

| 層 | 名前 |
|---|---|
| 内部選定関数 `ev.select_ev_bets` の引数 | `odds_cap`（win 専用ファイル内で曖昧なし） |
| 公開生成関数 `recommend.generate_recommendations` の引数 | **`win_odds_cap`**（exotic と分離） |
| CLI フラグ | **`--win-odds-cap`** |
| logic_version フラグ | `;oddscap=<v>` |
| 採用ゲート strategy | `OddsCappedEVStrategy` / `name=ev_oddscap21` |

`generate_recommendations` は受け取った `win_odds_cap` を `select_ev_bets(odds_cap=win_odds_cap)` に渡す。

## `ev.select_ev_bets(horses, *, threshold, stake, odds_cap=None)`

- **追加引数** `odds_cap: float | None = None`(win 用上限。exotic 経路の既存 `odds_cap`=推定 exotic 上限とは別物・別関数=命名衝突なし。CLI/引数名は `win_odds_cap` で分離)。
- `odds_cap is None` → 現行と**完全に同一挙動**(戻り値バイト同等)。
- **cap フィルタは `select_ev_bets` の内部ループにのみ置き、`eligible_started()`/`renormalized_started_probs()` は変更しない**(codex: `eligible_started()` を変えると同関数を使う `FavoriteROIBaseline`/`UniformROIBaseline` まで cap されてしまう)。
- `odds_cap` 指定時: `renormalized_started_probs` は**全 started 馬**で従来どおり算出(cap 除外馬も分母に残す=勝ちうるが賭けない、odds-missing と同扱い)。ループ内で EV 判定の直前に `float(h["odds"]) >= odds_cap` の馬を `continue`(bet しない)。cap 内かつ EV≥threshold の馬のみ Bet を返す。
- 上限のみ(下限なし)。cap 判定は odds のみ・race_results 非参照(leak boundary)。

## `recommend.generate_recommendations(session, *, prediction_run_id, threshold, stake, logic_version=None, cfg=None, p_calibrator=None, win_odds_cap=None)`

- **追加引数** `win_odds_cap: float | None = None` を `select_ev_bets(odds_cap=win_odds_cap)` に透過。
- `default_logic_version` に `win_odds_cap` を渡し、`win_odds_cap is not None` のときのみ `;oddscap=<v>` を追記。`win_odds_cap=None` で lv バイト同等。
- Kelly sizing(`_win_stake_fractions`)は cap 後の bets に対して従来どおり(allocation 群は cap 内 bets のみ=相互排他性維持)。

## `strategies.OddsCappedEVStrategy(threshold, odds_cap)`(採用ゲート用)

- `name = f"ev_oddscap{int(odds_cap)}"`、`bets_for_race(horses, *, stake) -> select_ev_bets(horses, threshold=threshold, stake=stake, odds_cap=odds_cap)`。
- 既存 `EVStrategy`(cap なし)・`FavoriteROIBaseline`・`UniformROIBaseline` と同じ Strategy protocol。

## CLI + policy-aware 冪等性

- `betting recommend-serve --race-id <id> [--win-odds-cap <v>]`(既定 None=現行)。
- `betting recommend-backfill --from --to [--win-odds-cap <v>]`。
- `--win-odds-cap` 省略時は現行と冪等・バイト同等。既定 ON への切替(cap=21)は US3 ゲート合格後に orchestration/serving の既定を変更。
- **policy-aware 冪等性(codex 指摘の最大リスク)**: `cli.py::_generate_product_set()`/`recommend_backfill()` は現在 (run, bet_type=win) group 既存なら skip する。これでは cap opt-in 版を legacy win 済み run に追加生成できない/二重生成しうる。→ 冪等キーを **(run, bet_type, win policy)** に細分化(policy=cap 無効 / cap=21 等を logic_version の `oddscap` で識別)。045 の win/exotic group 細分化と同型。異 policy は別 group として追補、同 policy 再実行は skip。

## テスト

- `test_win_odds_cap_none_is_byte_identical_select_ev_bets`: cap=None で recommendation 行の全列 + lv が現行と一致(実 DB 1 レース)。
- `test_odds_cap_filters_after_started_renorm_not_before`: cap=21 で 21+ 馬が bet されず、cap 内馬の win_prob/EV が cap なしと一致(分母保持)。
- `test_odds_cap_does_not_change_favorite_or_uniform_baselines`: cap 引数は `FavoriteROIBaseline`/`UniformROIBaseline`(eligible_started 経由)の出力を変えない。
- `test_win_kelly_allocation_only_sees_capped_candidates`: Kelly allocation 群は cap 内 bets のみ(相互排他性維持)。
- `test_odds_cap_logic_version`: cap 指定時のみ `;oddscap=21.0` が付く(無効時バイト同等)。
- `test_recommend_serve_policy_aware_idempotency_legacy_win_plus_cap`: legacy win 済み run に cap policy を追補でき、同 policy 再実行は skip。
- `test_exotic_recommendations_ignore_win_odds_cap`: exotic 経路(kelly_recommend)は win_odds_cap の影響を受けない。
- leak-guard: cap 経路で race_results を読まない・cap 値がモデル特徴に流入しない。
