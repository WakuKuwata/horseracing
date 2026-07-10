---
description: "Task list for feature 065: prospective shadow-betting log"
---

# Tasks: prospective shadow-betting log

**Input**: Design documents in `specs/065-prospective-shadow-log/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/{generation,aggregation,display}.md, quickstart.md

**Tests**: 含める(憲法 II/III の leak-guard・byte-parity・冪等 + codex 提案テスト)。

**Organization**: US1(P1=前向き記録・凍結オッズ+capture 規律・MVP)→ US2(P2=read-time 集計 + 表示)→ US3(P3=運用ワンショット + 精算結線)。

## Format: `[ID] [P?] [Story] Description with file path`

- **[P]**: 別ファイル・依存なしで並列可
- **スキーマ変更なし・migration なし(head 0011 維持)**

---

## Phase 1: Setup

- [X] T001 migration head が 0011 のまま(スキーマ変更ゼロ)で進むことを確認・記録(prospective 識別=logic_version マーカー、集計=read-time)。
- [X] T002 **p≠q 前提の確認**(codex R8 保留): `serving/pipeline.py` の active モデルが market-offset(win オッズ読み=lgbm-060-mkt 系)でないことを確認。もし active が市場読みなら shadow-log の解釈に注記(計器は active の p をそのまま使うため)。

---

## Phase 2: Foundational

**なし**（既存 betting/live/api への薄い結線。US1 の betting マーカーが最初の実装単位)。

---

## Phase 3: User Story 1 — 前向きに出した買い目を凍結オッズごと記録 (Priority: P1) 🎯 MVP

**Goal**: result-pending 時に、fresh scrape した発走前オッズ(凍結=market_odds_used)で **WIN 推奨**を生成し、`;prospective=1;odds_asof=<capture ts>` マーカーで記録。marker off はバイト同等。run 跨ぎ冪等。capture 規律で closing-oracle を裏口から入れない。

**Independent Test**: result-pending レースに前向き記録 → WIN 推奨が marker 付きで生成、後で race_horses.odds を closing 更新しても評価用凍結オッズ不変、結果ありレースは拒否、再実行で run 跨ぎ重複なし。

### Tests (US1)

- [X] T003 [P] [US1] `betting/tests/unit/test_prospective_marker.py` に `test_prospective_off_is_byte_identical`(prospective=False で行 + lv が現行一致)。
- [X] T004 [P] [US1] 同ファイルに `test_prospective_marker_appended_after_custom_logic_or_rejected`(custom logic_version でも marker が消えない=解決後付与 or 拒否)。
- [X] T005 [P] [US1] `betting/tests/unit/test_prospective_marker.py` に `test_has_win_group_distinguishes_prospective_and_oddscap_policy`(legacy/cap/prospective/prospective+cap が run 跨ぎで衝突しない)。
- [X] T006 [P] [US1] `betting/tests/integration/test_recommend_serve.py` に `test_generate_recommendations_prospective_rejects_result_present_race`(結果ありは fail-closed 拒否)。
- [X] T007 [P] [US1] `live/tests/integration/test_collect_prospective.py` に `test_collect_prospective_rechecks_pending_after_scrape_before_insert`(フロー途中で結果が入ると marker を付けない)。
- [X] T008 [P] [US1] 同ファイルに `test_collect_prospective_repeated_runs_no_duplicate_across_prediction_runs`((race,model,policy)冪等・run 跨ぎ)。
- [X] T009 [P] [US1] 同ファイルに `test_collect_prospective_skips_stale_or_missing_odds` と `test_collect_prospective_generates_win_rows_not_exotic_only`。
- [ ] T010 [P] [US1] `betting/tests/unit/test_prospective_marker.py` に leak-guard(marker/凍結オッズ/結果がモデル特徴に流入しない・selection は results 非参照)+ `test_backfill_not_marked_prospective`(044/064 は marker 無し)。

### Implementation (US1)

- [X] T011 [US1] `betting/src/horseracing_betting/recommend.py`: `generate_recommendations(..., prospective=False, odds_asof=None)`。marker は **custom/default logic 解決後**に付与(prospective 時のみ `;prospective=1;odds_asof=<iso>`)。off でバイト同等。
- [X] T012 [US1] `betting/src/horseracing_betting/cli.py`: `_has_win_group` を **run 跨ぎ + prospective/oddscap policy 判別**に拡張(4政策非衝突)。`_generate_product_set`/recommend-serve に prospective 透過。
- [X] T013 [US1] `live/src/horseracing_live/orchestrate.py`: `collect_prospective(session, *, date|range, scrape_fn)` — capture 規律: (a)同一フローで発走前オッズ fresh 取得し capture 時刻を odds_asof に、(b)post_time 既知なら capture<post_time 要求・未知は weak_pretime フラグ、(c)scrape 直後・insert 直前に `is_result_pending` 再確認、(d)advisory-lock(race/model/policy)、(e)**WIN 経路**(`generate_recommendations(prospective=True)`)を明示的に呼ぶ(exotic の generate_kelly_recommendations でない)。冪等 skip。
- [X] T014 [US1] 実 DB or 合成 E2E(quickstart 1-3): marker off バイト同等・prospective 記録・記録後 closing 更新で評価不変(closing-oracle 排除)。betting/live 緑・ruff クリーン。

**Checkpoint**: US1 単独で「凍結オッズつき前向き記録」が可能(closing-oracle を構造排除)。

---

## Phase 4: User Story 2 — 前向き実績を正直に集計するビュー (Priority: P2)

**Goal**: prospective 印のある settled real-win のみを凍結オッズで集計(run 跨ぎ直接クエリ・現在オッズ非参照・void 別計上・skip-rate なし)し、closing backtest と別セクション・正直ラベル・空状態で表示。

**Independent Test**: backfill/exotic/estimated/未確定が混在しても prospective settled win のみ集計、closing 更新で不変、空で偽集計なし、front で正直ラベル・空状態・別セクション。

### Tests (US2)

- [X] T015 [P] [US2] `api/tests/unit/test_shadow_log.py` に `test_shadow_log_filters_exact_prospective_settled_real_win_only`(backfill/exotic/estimated/無効marker/未確定を全除外)。
- [X] T016 [P] [US2] 同ファイルに `test_shadow_log_uses_frozen_market_odds_after_current_odds_change`(SC-001)と `test_shadow_log_voids_excluded_from_roi_denominator`(hit=None は n_void)。
- [X] T017 [P] [US2] 同ファイルに `test_shadow_log_includes_non_active_prediction_runs`(run 跨ぎ)と `test_shadow_log_empty_is_typed_empty`。
- [X] T018 [P] [US2] `api/tests/` に read-only 境界(全 path GET・betting 非 import)+ OpenAPI drift-check 緑。
- [X] T019 [P] [US2] `front/src/components/ShadowLogPanel.test.tsx`: `test_shadow_log_panel_honest_labels`(real前向き/closingでない/利益約束せず・利益語/損益色なし)・`test_shadow_log_panel_empty_state`・`test_shadow_log_separate_from_closing_backtest`・`test_shadow_log_displays_pending_void_valued_counts`。

### Implementation (US2)

- [X] T020 [US2] `api/src/horseracing_api/backtest.py`: `shadow_log_summary(recs, *, finish_maps) -> ShadowLogSummary`（純関数）— 全 AND 述語で prospective settled real-win のみ、`win_realized`(凍結 market_odds_used)で per-rec realized、void 除外・n_pending/n_void/weak_pretime・by_month。favorite_realized/現在オッズ非参照。betting 非 import。
- [X] T021 [US2] `api/src/horseracing_api/queries.py`: prospective 推奨を **run 跨ぎで直接クエリ**(marker LIKE + bet_type=win、active-run scoped でない)+ 各レース finish_map。
- [X] T022 [US2] `api/src/horseracing_api/schemas.py` + `routers`: `GET /api/v1/shadow-log`(read-only 純追加・typed-empty)。OpenAPI 再生成 + `front/openapi.json` snapshot/型同期 + drift-check 緑。
- [X] T023 [US2] `front/src/components/ShadowLogPanel.tsx` + 結線: prospective 実績(凍結オッズ realized・確定/未確定/void 数・by_month)、正直ラベル常時、空状態、closing backtest(RecommendationPanel)と別セクション。文言規律(利益語/損益色/ソート禁止)。
- [X] T024 [US2] 実 DB or 合成 E2E(quickstart 4-5): prospective のみ集計・closing 更新で不変・空状態・表示分離。api/front 緑・tsc/eslint/ruff クリーン。

**Checkpoint**: US1 の記録があれば US2 単独で「正直な前向き実績表示」を提供。

---

## Phase 5: User Story 3 — 前向き収集を回す運用ワンショット (Priority: P3)

**Goal**: 「発走前オッズ取得 → 前向き生成 → 記録」と「結果後 精算 → 反映」を CLI ワンショットで(既存 scrape/settle 束ね・新ロジックゼロ・冪等)。

**Independent Test**: ペンディング群に前向き収集 → prospective 生成、結果投入 → 精算で shadow-log 反映、再実行で重複なし。

### Tests (US3)

- [ ] T025 [P] [US3] `live/tests/integration/test_collect_prospective.py` に `test_collect_prospective_cli_idempotent`(CLI 再実行で重複記録なし)。
- [ ] T026 [P] [US3] 同ファイルに `test_collect_prospective_skips_result_present_and_reports_reason`(結果ありは skip・理由)。

### Implementation (US3)

- [X] T027 [US3] `live/src/horseracing_live/cli.py`: `live collect-prospective --date <d> | --from --to`(T013 の collect_prospective を呼ぶ・scrape/settle は別コマンド束ね・冪等・skip 理由出力)。
- [X] T028 [US3] 精算結線: 結果確定後に既存 settle 経路(049 win_realized は shadow_log_summary が read-time 実行)で反映されることを E2E で確認(新精算ロジックなし)。

**Checkpoint**: 運用ループで計器を継続的に埋められる(データフィードがあれば)。

---

## Phase 6: Polish & Cross-Cutting

- [ ] T029 [P] 全パッケージ緑(betting/live/api/front)・ruff/tsc/eslint・OpenAPI drift・migration head 0011 不変・leak-guard 群。
- [ ] T030 [P] メモリ更新([[betting-roi-landscape-2026-07]] 系に「shadow-log = 正直な計器・going-forward・データ依存」を追記)+ spec に実装結果反映。
- [ ] T031 codex 最終レビュー(実装差分)で capture 規律・run 跨ぎ冪等・現在オッズ非参照が守られているか確認。

---

## Requirements Coverage (traceability)

| 要件 | タスク |
|---|---|
| FR-001 result-pending のみ生成 | T006, T013 |
| FR-002 凍結オッズ/computed_at/odds_asof(capture)/marker 記録 | T011, T013 |
| FR-003 backfill と機械区別(厳密 marker) | T010, T012 |
| FR-004 prospective settled のみ集計・skip-rate なし・void 別・run 跨ぎ・現在オッズ非参照 | T015, T016, T017, T020, T021 |
| FR-005 closing backtest と別セクション・正直ラベル | T019, T023 |
| FR-006 空状態を正直に | T017, T019, T023 |
| FR-007 運用結線・run 跨ぎ冪等・advisory-lock | T008, T013, T025, T027 |
| FR-008 leak 境界 | T010 |
| FR-009 疑似ラベル・win のみ | T015, T023 |
| FR-010 スキーマ変更なし | T001, T022 |
| FR-011 capture 規律(fresh scrape/capture時刻/post_time前/one-shot) | T007, T009, T013 |
| SC-001 凍結オッズ評価バイト不変 | T014, T016 |
| SC-002 非混同ゼロ | T015 |
| SC-003 正直表示 | T019, T023 |
| SC-004 冪等・result-pending 以外拒否 | T006, T008, T025 |
| SC-005 leak-guard・results 非参照・api betting 非 import | T010, T018 |

全 FR/SC が ≥1 タスクにマップ。

## Dependencies

- Setup(T001–T002)→ US1。
- US1 の `generate_recommendations(prospective=)`(T011)と `collect_prospective`(T013)は US2 集計・US3 CLI の前提。
- US2(T015–T024)は US1 の記録があれば独立にテスト可(合成 prospective 行でも可)。
- US3(T025–T028)は US1 の collect_prospective に依存(CLI は薄い wrap)。
- Polish(T029–T031)は全 US 後。

## Parallel Opportunities

- US1 テスト T003–T010、US2 テスト T015–T019、US3 テスト T025–T026 は各 [P] 並列。
- US2 の api(T020–T022)と front(T023)は契約(drift-check)を挟んで段階並列。

## MVP Scope

**US1(T001–T014)** = MVP。凍結オッズつき前向き記録 + capture 規律で closing-oracle を構造排除。US2 で正直な集計/表示、US3 で運用ワンショット。**注意**: 実データ収集は発走前オッズフィード + 未来レース ingest が前提(運用マター)。
