---
description: "Task list for feature 066 race-dispersion-readout"
---

# Tasks: race dispersion & p/q divergence readout(荒れ度・意見差の読み計器)

**Input**: Design documents from `/specs/066-race-dispersion-readout/`

**Prerequisites**: plan.md・spec.md・research.md・data-model.md・contracts/predictions-additions.md・quickstart.md

**Tests**: spec の Required Tests(11本)が明示されているためテストタスクを含める(leak-guard・契約・表示規律は本 repo で MUST)。

## Format: `[ID] [P?] [Story] Description`

- **[P]**: 別ファイルで依存なし=並列可
- **[Story]**: US1(軸A)/US2(軸B)/US3(診断)。Setup/Foundational/Polish は無印

## Path Conventions

Web 構成(既存 `api/` `eval/` `training/` `front/` に純追加)。スキーマ変更ゼロ・migration なし。

---

## Phase 1: Setup(共有・最小)

**Purpose**: 新パッケージ無し。境界 artifact の置き場と定数だけ用意。

- [X] T001 `specs/066-race-dispersion-readout/` の artifacts を確認し、境界 artifact の出力先(`artifacts/dispersion_bands/` 規約、非コミット=`.gitignore` 済確認)と logic_version トークン `dispbands-v1` を決める。
- [X] T002 [P] バンド enum(`firm`/`somewhat_firm`/`standard`/`somewhat_open`/`open`)と表示ラベル(堅い/やや堅い/標準/やや波乱/波乱含み)を api・front で共有する単一定数として `api/src/horseracing_api/dispersion.py`(新規・空骨組)と `front/src/lib/dispersionLabels.ts`(新規)に定義。

---

## Phase 2: Foundational(全 US のブロッキング前提)

**Purpose**: 境界フィット(結果非参照)と read-time 純関数コア、リーク境界テストの土台。US1/US2/US3 はこれに依存。

- [X] T003 [P] `eval/src/horseracing_eval/dispersion_bands.py`(新規)に予測器非依存の `normalized_entropy(q_vector) -> float`(`-Σq·ln q / ln N`、N<2 は None)と `favorite_win_prob`/`top3_cumulative` の純関数を実装(取消除外済み canonical q を入力)。
- [X] T004 `eval/src/horseracing_eval/dispersion_bands.py` に `fit_boundary(session, *, fit_from, fit_to, field_buckets="global") -> DispersionBoundary` を実装。fit 窓内レースの**正規化エントロピー分布の5分位のみ**から edges を算出(`(race_date, race_id)` 厳密前規律・結果を一切参照しない)。`splits.expanding_folds`/`segment_edge` の収集機構を参照(改変しない)。
- [X] T005 [P] DispersionBoundary artifact の直列化(metric/field_size_buckets/fit_window/as_of/version/quintile_edges/n_races_fit を JSON、DB 書込なし・決定論再生成可)を `eval/.../dispersion_bands.py` に実装。
- [X] T006 `training/src/horseracing_training/cli.py` に `dispersion-bands --fit-from --fit-to [--field-buckets]` サブコマンドを追加(境界フィット→artifact 出力)。`--diagnose-*` は US3 で追加。
- [X] T007 [P] `api/src/horseracing_api/dispersion.py` に read-time 純関数コアを実装: canonical field(021 `market_win_probs`/`canonical_win_probs` 再利用)から `normalized_entropy`/`favorite_win_prob`/`top3_cumulative` を計算し、artifact edges で `band` を割当てる `assign_band(entropy, edges)`。取消は q 正規化前に除外(010/021)。
- [X] T008 [P] リーク境界テスト土台 `features/tests/unit/test_feature066_leak_guard.py`(新規): 表示軸トークン(`normalized_entropy`/`favorite_win_prob`/`race_dispersion`/`race_divergence` 等)が registry・materialized_columns に**出現しない**ことを assert(`display_axis_tokens_absent_from_model_input_features_registry_materialized_columns`)。
- [X] T009 [P] behavioral leak-guard `api/tests/.../test_dispersion_leak.py`(新規): 表示軸の計算(dispersion.py)を呼んでも decision-support 経路の選択 p がバイト不変(`display_axis_mutation_does_not_change_decision_support_p`)。**「全 odds 変更で全モデル不変」は主張しない**(060 market-offset があるため)——アサートは「新 display 集計が feature/training に入らない」に限定。
- [X] T010 [P] 境界フィットのリークテスト `eval/tests/.../test_dispersion_bands.py`(新規): `frozen_boundaries_strictly_before_date_race_id`(edges が対象より厳密前のみ由来)・`boundary_fit_invariant_to_target_and_future_results`(結果を変えても edges 不変)・`field_size_bucket_or_entropy_small_large_synthetic_case`(≤5/16+ 合成で正規化エントロピーが頭数跨ぎで頑健)。

**Checkpoint**: 境界 artifact が結果非参照で再現でき、read-time コアと leak-guard 土台が緑。

---

## Phase 3: User Story 1 - 軸A 決着集中度の表示(Priority: P1)🎯 MVP

**Goal**: q由来の5段バンド + 生数値 + 校正 p 差分を予測応答に純追加し front で表示。q 欠損は unavailable(p フォールバック禁止)。

**Independent Test**: started 全頭にオッズのあるレースで band/数値/model_delta が出て、q 欠損レースで unavailable(理由付き)になり p フォールバックしないこと(quickstart §2,§3)。

- [X] T011 [US1] `api/src/horseracing_api/dispersion.py` に `build_race_dispersion(session, race_id, run) -> RaceDispersion|None` を実装: available 判定(canonical field 全頭に有効 q)・band/entropy/max q/top3・`model_delta`(校正 p 由来集中度と q 由来の差分・`canonical_consistent=false` で null)・`unavailable_reason`(`no_market_odds`/`partial_market_odds`)・odds_as_of/odds_source・`is_pseudo=true`・`boundary_version`。q 欠損は band/数値/model_delta を全 null(p フォールバック禁止)。
- [X] T012 [US1] `api/src/horseracing_api/schemas.py` に `RaceDispersion` pydantic モデルを追加し、predictions 応答トップレベルに `race_dispersion: RaceDispersion | None` を**純追加**(既存フィールド不変)。
- [X] T013 [US1] predictions router(`api/.../routers/…`)で `build_race_dispersion` を呼び応答に同梱(新エンドポイント作らず既存応答に純追加・GET-only)。境界 artifact をロード(パス/version 解決)。**artifact 不在時は fail-closed でなく `band=null`/`boundary_version=null`(生数値は q があれば表示)=表示計器を落とさない(F8)**。`odds_source` は既存 021 の `final`/`prerace` を透過(独自値を作らない・F1)。
- [X] T014 [P] [US1] API テスト `api/tests/.../test_race_dispersion.py`: `axis_a_q_missing_returns_unavailable_no_p_fallback`・取消除外で canonical field 再正規化・band が凍結 edges 由来・is_pseudo/odds_as_of surface。
- [X] T015 [US1] OpenAPI 再生成(`front/openapi.json`・`admin/openapi.json`)+ `schema.d.ts` 再生成、`openapi_pure_additive_drift_check` 緑。
- [X] T016 [P] [US1] front `front/src/components/RaceDispersionPanel.tsx`(新規): 5段バンド + **生数値併記**(本命勝率/上位3頭/正規化エントロピー)+ p差分(direction)+ pseudo/source バッジ + closing-leaning 開示 + unavailable の正直な空状態。損益色・妙味/危険/edge 語・ソート無し。formatNum で null 安全。
- [X] T017 [P] [US1] front テスト `RaceDispersionPanel.test.tsx`: `front_pseudo_badge_required_for_q_aggregate`・unavailable 空状態・生数値併記・`front_no_profit_edge_value_copy_no_red_green_sorting`(軸A分)。

**Checkpoint**: US1 単独で MVP。軸A が正直に表示され、q欠損で unavailable。

---

## Phase 4: User Story 2 - 軸B モデル p vs 市場 q 意見差(Priority: P2)

**Goal**: race-level 中立サマリ + 既存 040 バッジ(無改変)+ 全馬 p/q 展開の3層。canonical_consistent=false で抑制、057 どのモデル p か明示。

**Independent Test**: p/q 揃いレースで summary/favorite_direction/underrated_longshots/rank_agreement が中立文言で出て、既存 divergence_band が未変更、canonical_consistent=false で抑制(quickstart §4)。

- [X] T018 [US2] `api/src/horseracing_api/dispersion.py` に `build_race_divergence(session, race_id, run) -> RaceDivergence|None`: q1位馬に既存 `divergence_band` を適用し**写像 `market_higher→model_lower`/`model_higher→model_higher`/`similar→similar`(F2)**で `favorite_direction`・モデル上位N(p top3)に入る低人気の事実リスト `underrated_longshots`・`rank_agreement`=**top3 集合一致率(重なり/3、Kendall τ 不採用・F6)**・中立 `summary` 文言・`model_version`(057)。`canonical_consistent=false`/q欠損 は available=false で全 null。
- [X] T019 [US2] `api/.../schemas.py` に `RaceDivergence` を追加、predictions 応答に `race_divergence: RaceDivergence | None` を純追加(既存 per-horse `divergence` は無改変)。router 同梱。
- [X] T020 [P] [US2] API テスト `test_race_divergence.py`: `partial_q_or_canonical_inconsistent_suppresses_axis_b`・既存 `divergence_band` 未変更(040 回帰)・中立文言(買い/妙味語なし)・model_version が選択モデルを指す(057)。
- [X] T021 [US2] OpenAPI 再生成 + drift-check 緑(race_divergence 純追加)。
- [X] T022 [P] [US2] front `front/src/components/RaceDivergenceSummary.tsx`(新規): 一言サマリ(中立)+ 抑制状態。`HorseEntriesTable.tsx` に全馬 p/q 展開の軽微結線(既存 040 バッジ・行展開挙動は不変)。
- [X] T023 [P] [US2] front テスト `RaceDivergenceSummary.test.tsx`: 中立文言・canonical_consistent=false 抑制・`front_no_profit_edge_value_copy_no_red_green_sorting`(軸B分)・乖離ソート無し。

**Checkpoint**: US1+US2 で人間の「買う/買わない」「人気/穴」判断材料が揃う。

---

## Phase 5: User Story 3 - バンド校正の OOS 診断(Priority: P3・SECONDARY)

**Goal**: 047 同型 walk-forward OOS でバンド別 realized chaos を CI 付き検証。採否ゲートにしない。区別不能でも併合せず開示。

**Independent Test**: fit 窓後の OOS 窓でバンド別 n/本命敗北率/高配当率/CI/separated が出て、fit 窓レースが混ざらず、区別不能を開示(quickstart §5)。

- [X] T024 [US3] `eval/src/horseracing_eval/dispersion_bands.py` に `diagnose_bands(session, *, boundary, diagnose_from, diagnose_to) -> list[DispersionBandDiagnostic]`: walk-forward OOS 収集(segment_edge 収集機構流用)で凍結 edges によりバンド割当→バンド別 realized chaos・Wilson/cluster-bootstrap CI・`separated_from_prev`。**予約規則(data-model E4・結果を見る前に固定)を実装: 本命敗北率=q1位馬の敗北(同着で q1位が勝ち馬に含まれれば敗北にしない)・high_payout=1着馬の実現単勝オッズ ≥ 10.0(F5)・cancellation/void は分母除外し n_void に計上(F9)**。fit 窓と OOS 窓を分離(fit 窓内を OOS ラベルしない)。
- [X] T025 [US3] `training/.../cli.py` の `dispersion-bands` に `--diagnose-from --diagnose-to` を追加(境界フィット→OOS 診断テーブル表示)。**バンドを採否ゲート・閾値調整に使わない**旨を出力に明記(047 SECONDARY)。`--persist`(diagnostic_runs 流用)は deferred。
- [X] T026 [P] [US3] eval テスト `test_dispersion_bands.py`(拡張): fit/OOS 窓分離・`separated_from_prev=false` でも境界を再フィット/併合しない・void/dead heat 予約定義・決定論。

**Checkpoint**: 計器の健全性を OOS で正直に裏取り(SECONDARY)。

---

## Phase 6: Polish & Cross-Cutting

- [X] T027 [P] `api_get_only_no_training_betting_write_imports`: api の import-graph 境界テストに dispersion.py が betting/training を import しない・全 path GET を追加確認。
- [X] T028 [P] front の横断表示規律テスト(軸A+軸B 統合): 損益色・妙味/危険/edge/value 語・荒れ度/乖離ソートが無い(`front_no_profit_edge_value_copy_no_red_green_sorting` 統合)。
- [X] T029 実 DB E2E([quickstart.md](quickstart.md) 全手順): 境界フィット→軸A 応答→unavailable→軸B 3層→US3 診断→リーク/契約テストを通し、[[local-db-setup]] スタックで確認。
- [X] T030 [P] 憲法チェック再確認(II リーク境界 behavioral・IV canonical field・V 監査 artifact/pseudo・VI OpenAPI 純追加/read-only)を plan.md の Constitution Check に対して機械的に照合し、逸脱ゼロを記録。

---

## Dependencies & Execution Order

- **Setup(T001-T002)** → **Foundational(T003-T010)** → US1/US2/US3。
- **Foundational がブロッキング**: 境界フィット(T004-T006)と read-time コア(T007)と leak-guard 土台(T008-T010)。
- **US1(T011-T017)**: Foundational 後。MVP。
- **US2(T018-T023)**: Foundational 後。US1 と独立(別オブジェクト・別コンポーネント)だが OpenAPI 再生成は US1 の後に順序化推奨(snapshot 競合回避)。
- **US3(T024-T026)**: Foundational(境界)後。US1/US2 と独立。
- **Polish(T027-T030)**: 全 US 後。

## Parallel Opportunities

- Foundational 内: T003/T005/T008/T009/T010 は別ファイルで [P]。
- US1 内: T014/T016/T017 は [P](test/front は実装 T011-T013 後)。
- US2 内: T020/T022/T023 は [P]。
- OpenAPI 再生成(T015/T021)は snapshot 競合のため直列。

## Independent Test Criteria

- **US1**: q揃いで band+生数値+p差分、q欠損で unavailable かつ p フォールバック無し。
- **US2**: 3層の中立表示、040 バッジ無改変、canonical_consistent=false 抑制、model_version 明示。
- **US3**: OOS バンド別 realized chaos + CI、fit/OOS 分離、区別不能を併合せず開示。

## Suggested MVP Scope

**US1(軸A)のみ**で MVP 成立(「買う/見送り」の第一材料)。US2 で「人気/穴」材料を追加、US3 は健全性の裏取り(SECONDARY)。

## Required Tests → Task 対応(spec 11本・F3)

| Required Test | Task |
|---|---|
| `axis_a_q_missing_returns_unavailable_no_p_fallback` | T014 |
| `partial_q_or_canonical_inconsistent_suppresses_axis_b` | T014(partial q 軸A)/ T020(canonical suppress 軸B) |
| `frozen_boundaries_strictly_before_date_race_id` | T010 |
| `boundary_fit_invariant_to_target_and_future_results` | T010 |
| `field_size_bucket_or_entropy_small_large_synthetic_case` | T010 |
| `display_axis_tokens_absent_from_model_input_features_registry_materialized_columns` | T008 |
| `display_axis_mutation_does_not_change_decision_support_p` | T009 |
| `api_get_only_no_training_betting_write_imports` | T027 |
| `openapi_pure_additive_drift_check` | T015 / T021 |
| `front_pseudo_badge_required_for_q_aggregate` | T017 |
| `front_no_profit_edge_value_copy_no_red_green_sorting` | T017(軸A)/ T023(軸B)/ T028(統合) |

## Format Validation

全タスクが `- [ ] Txxx [P?] [US?] 記述 + ファイルパス` 形式。Setup/Foundational/Polish は Story ラベル無し、US フェーズは [US1]/[US2]/[US3] 付き。
