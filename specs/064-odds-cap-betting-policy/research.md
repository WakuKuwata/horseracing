# Phase 0 Research: odds-cap betting policy + honest display

各判断は Decision / Rationale / Alternatives の形式。codex second opinion(plan 段並走)の反映は末尾 §R9。

## R1. cap を「独立引数」にする vs KellyConfig に入れる

**Decision**: cap は `select_ev_bets` / `generate_recommendations` の**独立キーワード引数 `odds_cap: float | None`** とする。KellyConfig には入れない。

**Rationale**: win 経路 `generate_recommendations(cfg: KellyConfig | None)` は cfg=None(flat)でも動く。cap を KellyConfig に入れると flat 推奨に cap を適用できない。cap は**選定段**(どの馬を買うか)、Kelly は**sizing 段**(いくら賭けるか)で概念が直交=引数を分けると flat/Kelly 両方に効き、p≠q・確率不変の境界も明瞭。exotic 経路の既存 `odds_cap`(推定 exotic オッズ上限=別物)とは関数が別なので衝突しない。

**Alternatives**: KellyConfig 拡張(却下: flat 非対応・sizing と選定の混線)、環境変数(却下: 監査不能)。

## R2. cap 無効時のバイト同等をどう担保するか

**Decision**: `odds_cap=None`(既定)で、(a)`select_ev_bets` の反復・renorm・戻り値、(b)`logic_version` 文字列、(c)Kelly allocation 群、が現行と**完全一致**。cap 記述は `odds_cap is not None` のときだけ lv に `;oddscap=<v>` を追記。回帰テスト `test_odds_cap_none_is_byte_identical`(実 DB 1 レースで recommendation 行の全列一致)。

**Rationale**: 後方互換(SC-002)。現行 active な永続化推奨・serving を壊さない。lv に条件付き追記することで cap 有効時のみ監査文字列が変わる(058/046 の logic_version 条件付き追記前例)。

**Alternatives**: 常に `;oddscap=none` を付ける(却下: 既存 lv とバイト非同等になり過去比較が割れる)。

## R3. cap 除外馬を確率分母に残すか

**Decision**: **残す**。cap 超オッズ馬は `renormalized_started_probs` の分母に入れたまま、`eligible_started` のループで **bet 対象から外す**(odds-missing 馬と同じ扱い)。

**Rationale**: cap 超の馬も実際に勝ちうる → p の再正規化から抜くと他馬の p が歪み win_prob が変わる(IV 違反)。cap は「賭けない」判断であって「存在しない」ではない。既存 ev.py が odds-missing 馬を「分母に残すが賭けない」で処理する設計と一致(codex 由来の既存 fix)。win_prob バイト不変。

**Alternatives**: 分母から除外(却下: p 変化=IV 違反・serving 予測との乖離)。

## R4. 採用ゲート harness の構成

**Decision**: `eval/policy_gate.py`(新)= walk-forward OOS(fold 毎 fit → valid predict、market_edge と同じ expanding_folds)で各レースの (p, odds, 結果) を集め、**`betting.roi.score_backtest` を strategy 別に呼ぶ**。strategy=現行 `EVStrategy(threshold=1.0)` と新 `OddsCappedEVStrategy(threshold=1.0, odds_cap=21)`、baseline=既存 `FavoriteROIBaseline`/`UniformROIBaseline` + no-bet(自明 ×1.0)。指標=RoiReport(recovery/hit/skip/maxDD/losing_streak)+ fold 別・odds帯別・log growth を付加。predictor は training の LightGBMPredictor を注入(eval は predictor-agnostic=循環回避、020/047 前例)。

**Rationale**: 既存 strategies.py(EV/Favorite/Uniform)+ roi.py(score_backtest)が proposal-doc 指標をほぼ提供済。walk-forward driver は本セッションの scratchpad スクリプトを production predictor で formalize。eval は betting に依存してよい(betting→eval の逆でなければ循環しない)か要確認 → **依存方向は eval が betting.strategies/roi を import(betting は eval を import 不可)**。既に betting は eval.stage_discount を import しているため、eval→betting import はグラフ的に不可の可能性 → §R9/実装時に境界テストで確認し、循環するなら strategy/score を eval 側に薄く再実装(win-only の score は単純述語)。

**Alternatives**: betting 側に walk-forward を置く(却下: betting は training predictor を持たない・eval が評価の家)。

## R5. 採否バー(ROI>1 にしない)

**Decision**: 合格条件 = 「odds-cap policy の realized 回収率が現行 EV policy を上回る **かつ** 過半 fold で改善・最悪 fold 非悪化」。cap=21 は事前固定(評価結果で動かさない)。closing-oracle バイアスは両 policy に等しく効くため**相対比較は有効**(絶対 ROI は楽観の旨を明記)。

**Rationale**: 憲法 III・proposal-doc「ROI>1 で採否しない」。相対(現行比)なら closing バイアスは相殺。

**Alternatives**: 絶対 ROI 閾値(却下: closing 楽観 + 生存バイアス)。

## R6. 表示基準の read-time 算出可否(スキーマ回避)

**Decision**: (a)**no-bet ×1.00** = front 定数。(b)**odds帯別 realized 回収** = 表示中 win 行から front 派生(行は odds+realized_return を既に持つ)。(c)**本命ベタ基準** = per-race の最低オッズ馬の realized が必要で推奨行に無い → `api/backtest.py` に純関数 `favorite_realized(odds_map, finish_map)` を追加し、read-only API を**純追加**(OpenAPI 契約先行・front snapshot/drift-check 同期)。api は betting を import しない(既存 backtest.py の境界を踏襲)。

**Rationale**: (a)(b)はスキーマ・API 変更ゼロで実装可(VI 最小)。(c)のみ contract-first の read-only 追加。全て read-time・非永続・feature_snapshots 不変(II)。

**Alternatives**: 本命ベタを front で(却下: 推奨行に非推奨馬のオッズ/結果が無い)、永続化(却下: VI・不要)。

## R7. skip 理由の可視化

**Decision**: policy が推奨ゼロのレースで「見送り(全馬が上限オッズ超 / EV<1 / オッズ欠損)」を表示。理由は生成時に導出可能(cap 除外数・EV 未達数)だが、**永続化せず read-time に推論**(推奨ゼロ+レースにオッズがある=cap/EV で全落ち)して front 表示。監査に厳密さが要るなら logic_version に policy を記録済なので後追い可能。

**Rationale**: US2 の「空欄でなく見送りと言う」。read-time 推論で VVI 最小。

## R8. closing-oracle / prospective の明示

**Decision**: spec・表示・gate レポートに「過去オッズは closing 寄り=楽観、購入時点オッズ非保持(019)、真の実運用検証は今後 live refresh の prospective のみ」を明記。本 feature では過去 realized は**相対比較の材料**に限定。

## R9. codex second opinion(plan 段並走・反映済み)

`codex:codex-rescue` を plan 生成と並走で起動。総評=核心方針(cap=win 選定段の opt-in フィルタ・Kelly に混ぜない・分母保持・baseline 不変・logic_version 条件付き)は妥当。以下、採否。

**採用した指摘(artifacts に反映)**:
1. **cap は `select_ev_bets` 内の EV 判定直前に限定し、`eligible_started()` は変更しない** — `eligible_started()` を変えると同関数を使う `FavoriteROIBaseline`/`UniformROIBaseline` まで cap されてしまう。→ contracts/betting.md を「cap フィルタは select_ev_bets のループ内(eligible_started は不変)」に厳密化。テスト `test_odds_cap_does_not_change_favorite_or_uniform_baselines` 追加。
2. **policy-aware 冪等性(最大の実装リスク)** — `cli.py::_generate_product_set()`/`recommend_backfill()` は win group 既存なら skip する。cap opt-in 版を legacy win 済み run に追加生成できない/policy 別に二重生成しうる。→ 冪等キーを (run, bet_type) から **(run, bet_type, policy=cap 有無/値)** に細分化(045 の win/exotic group 細分化の前例)。contracts/betting.md に追記・テスト `test_recommend_serve_policy_aware_idempotency_legacy_win_plus_cap`。
3. **`items=[]` では skip 理由を区別できない** — 全馬 cap 超 / EV 未達 / 未生成 / run 無し が全部空配列。→ 表示 API に **policy skip status を純追加**(front 推論だけにしない)。contracts/display.md を強化・テスト `test_recommendations_api_empty_policy_status_distinguishes_cap_skip`。
4. **採用ゲート harness は既存流用不可** — `betting/backtest.py`(単一 model 期間 backtest)も `eval/operational.py`(最高 EV 1 頭のみ)も 064 の全馬買い walk-forward 比較には使えない。→ 新規 driver。production pl_topk/features-016 を使うため **driver は training 側(predictor を持つ)+ scorer は eval 側の純関数**(`market_gate.py` 型)。contracts/eval-gate.md を修正(依存方向: training→eval→betting.roi/strategies、循環しないことを境界テストで確認)。
5. **cap 値の selection leak** — `[6,21)` 等の比較を同一 OOS レポートに**表示**するのは可だが、そこから cap を**選ぶ**のは leak。→ R5 を強化: cap=21 は事前登録固定、レポートは複数 cap を透明性で併記してよいが採用選択は事前登録値のみ。テスト `test_policy_gate_rejects_cap_selection_from_oos_results`。
6. **ベースライン誤読防止の文言規律** — no-bet/本命ベタは「儲かる戦略」でなく「**資金を減らさない基準/市場ベースライン**」と表示。利益語・緑赤・ランキング・単発レース勝敗強調は禁止。→ contracts/display.md に明記・テスト `test_front_honest_baselines_not_profit_language_not_colored`。
7. **表示 summary が「表示中 rows 由来」で 1 レースの偶然と OOS policy 限界が混ざる** — → 過去実績サマリは「retrospective・in-sample・closing 楽観・将来利益でない」の固定注記を強化(049/021 規律)。

**採用しない指摘**: なし(すべて設計改善として取り込み)。

**保留**: 本命ベタ基準を「小 API 追加」か「recommendations 応答への純追加フィールド」か、は tasks/実装時に確定(どちらも OpenAPI 純追加・read-only・betting 非 import で契約は同一)。

**追加テスト**: 上記に加え codex 提案の `test_win_odds_cap_none_is_byte_identical_select_ev_bets` / `test_odds_cap_filters_after_started_renorm_not_before` / `test_win_kelly_allocation_only_sees_capped_candidates` / `test_exotic_recommendations_ignore_win_odds_cap` / `test_policy_gate_uses_same_folds_same_race_set_current_vs_cap` / `test_leak_guard_policy_metrics_not_in_features_training_serving` を tasks に採用。

**残リスク**: ①closing-oracle により絶対回収は楽観=相対比較のみ有効(固定注記で緩和・prospective は別 feature)。②production pl_topk walk-forward は長時間ジョブ(proxy 済のため忠実性確認に限定)。③既存 exotic `odds_cap`(推定 exotic 上限)との命名衝突 → win 側は `win_odds_cap`/独立 `WinSelectionPolicy` で命名分離。
