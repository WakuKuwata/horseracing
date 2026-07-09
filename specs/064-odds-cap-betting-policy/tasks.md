---
description: "Task list for feature 064: odds-cap betting policy + honest display"
---

# Tasks: オッズ上限つき買い目 policy + 正直な意思決定支援表示

**Input**: Design documents in `specs/064-odds-cap-betting-policy/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/{betting,eval-gate,display}.md, quickstart.md

**Tests**: 含める(憲法 II/III の leak-guard・byte-parity・walk-forward + codex 提案 11 本)。

**Organization**: user story 別。US1(P1=cap ロジック・MVP)→ US2(P2=正直表示)→ US3(P3=採用ゲート)。US1 は独立で価値(出血低減の買い目生成)。US2/US3 は US1 の cap 引数に依存。

## Format: `[ID] [P?] [Story] Description with file path`

- **[P]**: 別ファイル・依存なしで並列可
- スキーマ変更なし・migration なし(head 不変を維持)

---

## Phase 1: Setup

- [X] T001 `git status` と migration head を確認し、本 feature が **スキーマ変更ゼロ**(alembic head 不変)で進むことを記録する(`db/` の head assert テスト群が 0011 のまま緑であること)。
- [X] T002 exotic 経路 `betting/src/horseracing_betting/kelly_recommend.py` の既存 `odds_cap`(推定 exotic 上限)と、本 feature の win 用 `win_odds_cap` が**別物**であることを実装前に確認(命名衝突回避・研究 R9-③)。

---

## Phase 2: Foundational (blocking prerequisites)

**なし**（本 feature は既存選定関数への薄い結線。US1 の ev.py 変更が最初の実装単位で、US3 はそれを再利用）。

---

## Phase 3: User Story 1 — 出血を減らす odds-cap policy の既定適用 (Priority: P1) 🎯 MVP

**Goal**: win 買い目生成に上限のみの odds-cap(cap=21)を選定段フィルタとして追加。EV閾値・renorm・Kelly 不変、cap 無効時バイト同等、logic_version 記録、policy-aware 冪等。

**Independent Test**: cap=21 で 21+ 馬が推奨に出ず、cap 内馬の win_prob/EV/pseudo_odds が cap なしと一致、cap=None で現行とバイト同等。

### Tests (US1)

- [X] T003 [P] [US1] `betting/tests/unit/test_ev_odds_cap.py` に `test_win_odds_cap_none_is_byte_identical_select_ev_bets`(cap=None で `select_ev_bets` 出力が現行と一致)。
- [X] T004 [P] [US1] 同ファイルに `test_odds_cap_filters_after_started_renorm_not_before`(cap=21 で 21+ 馬は Bet されず、cap 内馬の win_prob/EV は cap なしと一致=分母保持)。
- [X] T005 [P] [US1] `betting/tests/unit/test_strategies.py` に `test_odds_cap_does_not_change_favorite_or_uniform_baselines`(cap は `eligible_started` 経由の Favorite/Uniform を変えない)。
- [X] T006 [P] [US1] `betting/tests/unit/test_win_kelly_and_topup`(該当ファイル)に `test_win_kelly_allocation_only_sees_capped_candidates`(Kelly allocation 群は cap 内 bets のみ)。
- [X] T007 [P] [US1] `betting/tests/integration/test_recommend.py` に `test_recommend_serve_policy_aware_idempotency_legacy_win_plus_cap`(legacy win run に cap policy 追補可・同 policy 再実行 skip)。
- [X] T008 [P] [US1] `betting/tests/unit/test_kelly_leak_guard.py` 等に `test_exotic_recommendations_ignore_win_odds_cap`（exotic 経路が win_odds_cap 非影響）と leak-guard(cap 経路で race_results 非参照・cap 値がモデル特徴に流入しない）。
- [X] T008b [P] [US1] `betting/tests/unit/test_ev_odds_cap.py` に `test_recommendation_reproducible_from_logic_version`（**SC-003**: 生成行の `logic_version` から cap 値を復元し、stake=stake_fraction×bankroll と併せて推奨を再現できる）。

### Implementation (US1)

- [X] T009 [US1] `betting/src/horseracing_betting/ev.py`: `select_ev_bets(horses, *, threshold, stake, odds_cap=None)` に引数追加。cap フィルタは**ループ内 EV 判定直前のみ**(`eligible_started()`/`renormalized_started_probs()` は不変)。`odds_cap is None` で完全同一挙動(T003/T004)。
- [X] T010 [US1] `betting/src/horseracing_betting/recommend.py`: `generate_recommendations(..., win_odds_cap=None)` を `select_ev_bets(odds_cap=win_odds_cap)` に透過(命名規約=contracts/betting.md)。`default_logic_version` に `win_odds_cap` を渡し、`win_odds_cap is not None` のときのみ `;oddscap=<v>` 追記(無効時バイト同等・data-model logic_version 文法)。
- [X] T011 [US1] `betting/src/horseracing_betting/strategies.py`: `OddsCappedEVStrategy(threshold, odds_cap)`(`name=f"ev_oddscap{int(odds_cap)}"`・`select_ev_bets(odds_cap=)` を呼ぶ)を追加。既存 EV/Favorite/Uniform は不変(US3 で使用)。
- [X] T012 [US1] `betting/src/horseracing_betting/cli.py`: `recommend-serve`/`recommend-backfill` に `--win-odds-cap`(既定 None=現行)。`_generate_product_set()`/`recommend_backfill()` の win 冪等キーを **(run, bet_type, win policy)** に細分化(policy は logic_version の `oddscap` で識別、045 の group 細分化前例)。異 policy は追補・同 policy は skip。
- [X] T013 [US1] 実 DB E2E(quickstart 1–2): 1 レースで cap=None がバイト同等・cap=21 が 21+ 除外/lv に `;oddscap=21.0`/全馬 21+ で推奨ゼロ正常終了を確認。betting パッケージ緑・ruff クリーン。

**Checkpoint**: US1 単独で「出血の少ない win 買い目」を生成可能(既定は opt-in=現行、cap は明示指定)。

---

## Phase 4: User Story 2 — 正直な意思決定支援としての買い目表示 (Priority: P2)

**Goal**: rec panel に 回収<1 + no-bet(×1.00)・本命ベタ基準併置・odds帯別回収・中立注記・skip 理由。疑似/実績分離維持。

**Independent Test**: 表示中 win 行から no-bet/本命ベタ基準・odds帯別が中立表示(損益色/利益語/ソートなし)、推奨ゼロで skip 理由、疑似は必ずラベル。

### Tests (US2)

- [X] T014 [P] [US2] `api/tests/unit/test_backtest.py` に `favorite_realized` の hit/void/miss/dead-heat(純関数)テスト。
- [X] T015 [P] [US2] `api/tests/` に read-only 境界(全 path GET・betting 非 import)+ OpenAPI drift-check 緑 + `win_policy_status` の値区別テスト。
- [X] T016 [P] [US2] `front/src/components/RecommendationPanel.test.tsx` に `test_front_honest_baselines_not_profit_language_not_colored`(no-bet/本命ベタ基準が併置・利益語/緑赤色/ソートなし)・skip 理由表示・「no pseudo value without a label」不変緑。

### Implementation (US2)

- [X] T017 [US2] `api/src/horseracing_api/backtest.py`: `favorite_realized(odds_map, finish_map, *, n_winners)` 純関数追加(最低オッズ馬の realized・betting 非 import・read-time)。
- [X] T018 [US2] `api/src/horseracing_api/schemas.py` + `queries.py`/`router.py`: recommendations 応答に **`win_policy_status`**(generated / skipped_all_over_cap / skipped_ev_unmet / not_generated / no_run)と **本命ベタ per-race realized 集計**を純追加(OpenAPI 純追加)。
- [X] T019 [US2] OpenAPI 再生成 + `front/openapi.json` snapshot 更新 + 型再生成 + drift-check 緑(契約先行・VI)。
- [X] T020 [US2] `front/src/components/RecommendationPanel.tsx`(`WinBacktestSummary`): no-bet(資金を減らさない基準)×1.00・本命ベタ(市場ベースライン)を併置、odds帯別 realized 回収(表示中行から派生)、中立注記(市場超過の再現優位なし・retrospective/in-sample/closing 楽観)、`win_policy_status` 由来の skip 理由表示。文言規律(利益語/色/ソート禁止)。
- [ ] T021 [US2] 実 DB E2E(quickstart 4): 1 レースで回収<1/no-bet/本命ベタ/odds帯別/中立注記/skip 理由/疑似ラベルを確認。front/api 緑・tsc/eslint/ruff クリーン。

**Checkpoint**: US1 なしでも US2 単独で「正直な表示」を提供(既存 backfill 済み推奨に対して)。

---

## Phase 5: User Story 3 — production 構成での採用ゲート最終確認 (Priority: P3)

**Goal**: production 構成(pl_topk+features-016)walk-forward OOS で 現行 vs cap policy を同一母集団・同一 fold で比較し、採否バー(相対 recovery 改善+fold 安定)で判定。

**Independent Test**: 現行 EV policy と cap policy が同一 OOS 母集団・同一 fold で採点され、fold別/odds帯別レポートと adopted 判定が出る。cap=21 は事前固定。

### Tests (US3)

- [X] T022 [P] [US3] `eval/tests/unit/test_policy_gate.py` に `test_policy_gate_uses_same_folds_same_race_set_current_vs_cap`(同一母集団・同一 fold)。
- [X] T023 [P] [US3] 同ファイルに `test_policy_gate_rejects_cap_selection_from_oos_results`(cap 値を OOS 結果から選ばない=引数固定・selection leak 拒否)と `test_policy_gate_report_by_fold_and_band`。
- [X] T024 [P] [US3] 同ファイルに `test_policy_gate_adoption_rule`(相対 recovery 改善+過半 fold 改善+最悪 fold 非悪化=合格)と closing-oracle 注記の存在。
- [X] T025 [P] [US3] import 境界テスト: training→eval→betting.roi/strategies が循環しない(循環時は eval 側 win-only scorer に再実装)。

### Implementation (US3)

- [X] T026 [US3] `eval/src/horseracing_eval/policy_gate.py`: 純 scorer `evaluate_policy_gate(oos_rows, *, cap=21.0, threshold=1.0) -> PolicyGateReport`。strategy=EV(現行)/OddsCappedEV(21)/Favorite/Uniform/no-bet を `betting.roi.score_backtest` 相当で採点、fold別・odds帯別・log growth・n_folds_improved・worst_fold_delta・adopted を算出。
- [X] T027 [US3] `training/src/horseracing_training/cli.py`: `policy-gate-eval` = walk-forward driver（LightGBMPredictor 注入・fold 毎 fit→predict→per-horse rows 収集）→ eval scorer 呼び出し。`--first-valid-year 2008 --cap 21 --objective {binary,pl_topk} --calibration isotonic --target-encode jockey_id,trainer_id`。
- [~] T028 [US3] ゲート実行: まず proxy(binary)で素振り(参考: 現行 ×0.721 → cap ×0.818・19/19年)→ **production 構成(pl_topk・長時間ジョブ nohup+監視)** で最終確認。レポートを spec/summary に記録。 【proxy ADOPTED=True 実行済(ev ×0.721→cap ×0.818・19/19 fold・worst +0.0253)。production pl_topk は long job で未実行】
- [ ] T029 [US3] ゲート合格時のみ: orchestration/serving/CLI の win 既定 `odds_cap` を `21` に切替(clarify=ゲート後既定 ON)。合格しなければ opt-in 据え置き(US2 は独立に価値)。切替後も cap 無効指定で現行再現可を確認。

**Checkpoint**: cap policy の忠実性が production 構成で確認され、合格なら既定 ON。

---

## Phase 6: Polish & Cross-Cutting

- [ ] T030 [P] 全パッケージ緑(betting/eval/api/front)・ruff/tsc/eslint・OpenAPI drift-check・migration head 不変・leak-guard 群を最終確認。
- [ ] T031 [P] メモリ更新([[betting-roi-landscape-2026-07]] に実装結果とゲート判定を追記)+ spec に採用ゲート実測を反映。
- [ ] T032 codex 最終レビュー(実装差分)を取り、指摘/非指摘を記録(高リスク領域・CLAUDE.md 方針)。

---

## Dependencies

- Setup(T001–T002)→ US1(T003–T013)。
- US1 の `select_ev_bets(odds_cap=)`(T009)は **US3 の OddsCappedEVStrategy/policy_gate**(T011/T026）の前提。
- US2(T014–T021)は US1 と**独立**(既存 backfill 済み推奨に対して表示強化可能)。ただし skip 理由の完全表示は US1 の cap 生成があるとより意味を持つ。
- US3(T022–T029)は US1(cap 選定)に依存。
- Polish(T030–T032)は全 US 後。

## Requirements Coverage (traceability)

| 要件 | タスク |
|---|---|
| FR-001 odds 上限フィルタ | T009 |
| FR-002 logic_version に cap 記録 | T010 |
| FR-003 opt-in / ゲート後既定ON | T012, T029 |
| FR-004 リーク境界(cap/odds 非特徴・results 非参照) | T008 |
| FR-005 realized + no-bet + 本命ベタ併置 | T017, T020 |
| FR-006 odds帯別回収 | T020 |
| FR-007 中立注記 | T020 |
| FR-008 skip 理由 | T018, T020 |
| FR-009 疑似ラベル分離 | T016 |
| FR-010 production 構成ゲート指標 | T026, T027, T028 |
| FR-011 採否バー(ROI>1 でない・cap 固定) | T023, T024 |
| FR-012 スキーマ不変・OpenAPI 純追加 | T001, T019 |
| SC-001 ゲートで fold 安定改善 | T028 |
| SC-002 cap 無効バイト同等 | T003, T013 |
| SC-003 logic_version から再現 | T008b, T013 |
| SC-004 表示で回収<1/no-bet/優位なし可視 | T021 |
| SC-005 leak-guard・results 非参照・api betting 非 import | T008, T015 |

全 FR/SC が ≥1 タスクにマップ(カバレッジ穴なし)。

## Parallel Opportunities

- US1 テスト T003–T008 は別ファイル/別ケースで [P] 並列。
- US2 の api(T014/T017/T018)と front(T016/T020)は契約(T019 drift-check)を挟んで段階並列。
- US3 テスト T022–T025 は [P] 並列。
- US1(betting)と US2(api/front)は担当が分かれれば並行着手可(US2 は US1 完了を待たずに既存データで開始できる）。

## MVP Scope

**US1(T001–T013)**= MVP。cap ロジック + バイト同等 + policy-aware 冪等で「出血の少ない win 買い目」を opt-in 生成。US2 で正直表示、US3 で production 忠実性確認とゲート後既定 ON。
