# Tasks: 予測根拠の実効寄与化(レース内センタリング)

**Input**: Design documents from `/specs/089-explanation-race-centering/`

**Prerequisites**: plan.md, spec.md, research.md(D1-D10 codex 採否), data-model.md,
contracts/explanation-v2.md, quickstart.md

**Tests**: 含む(憲法の品質ゲート: 整合性検査・確率不変・表示退行の各テストは非交渉)。

**Organization**: US1(保存時センタリング=MVP)→ US2(表示・API)→ US3(backfill 経路と
実効性監査)。作業場所は worktree `.claude/worktrees/089-explanation-race-centering`
(ブランチ `worktree-089-explanation-race-centering`・base=ローカル main 7ade9ce)。

## Format: `[ID] [P?] [Story] Description`

## Phase 1: Setup

**Purpose**: ベースライン確認(壊す前の緑を固定)

- [X] T001 変更前ベースラインの緑を確認: `uv run --project training pytest training/tests/unit/test_explanation.py -q` と `uv run --project serving pytest serving/tests -q` を worktree で実行し、既存の explanation/serving テストが全緑であることを記録する

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: v1/v2 が共存できる土台(定数分離)— US1 実装の前提

- [X] T002 training/src/horseracing_training/explanation.py: `METHOD_VERSION = 1` を `METHOD_VERSION_V1 = 1` / `METHOD_VERSION_V2 = 2` に分離し、v1 出力経路は挙動バイト不変のままにする(単一定数の 2 への書き換えは binary=v1 と衝突するため。research D6 / codex #8)。既存テストが無修正で緑のままであることを確認

**Checkpoint**: 定数分離済み・既存テスト緑 → US1 実装開始可

---

## Phase 3: User Story 1 - 保存時のレース内実効寄与化 (Priority: P1) 🎯 MVP

**Goal**: race-softmax 系(非 offset)モデルの説明を、レース内センタリング済み寄与で選定・
保存する v2 に切り替える。予測確率はバイト不変。

**Independent Test**: 単体テストで手計算 fixture の v2 出力一致+serving 回帰で確率バイト
不変+新馬相当 fixture で全馬同値特徴が top-K に出ないこと。

### Tests for User Story 1(実装と同一 PR 内・先に書いて赤を確認)

- [X] T003 [P] [US1] training/tests/unit/test_explanation.py: 手計算 fixture テストを追加 — 3頭×4特徴の小行列で centered 値・`score_centered`(=Σcentered=z−mean z の恒等式)・`other_contribution_centered`・`centering_population_size` を厳密値で検証(contracts/explanation-v2.md §1 手順 1-7)
- [X] T004 [P] [US1] training/tests/unit/test_explanation.py: 候補除外テスト — (a)全馬 NaN 特徴が top-K に出ない (b)全馬同値の非 NaN 特徴も出ない (c)NaN と非 NaN 混在は除外されない (d)候補 K 未満なら items が K 未満(水増しなし) (e)1 行バッチは items=[]・score_centered=0・population_size=1
- [X] T005 [P] [US1] training/tests/unit/test_explanation.py: 不変条件テスト — INV-E5(center=False で現行実装とバイト同一=method_version 1・centered キーなし)・INV-E4(per-feature レース内総和≈0 生成時検査)・INV-E4b(score_centered==Σitems.centered+other_centered)・INV-E3(タイは特徴名昇順の決定性)・`expected_raw_scores` 照合失敗/NaN 混入でレース atomic 全行 None(予測経路は例外を出さない)

### Implementation for User Story 1

- [X] T006 [US1] training/src/horseracing_training/explanation.py: `compute_explanations(booster, X, feature_cols, *, k=5, center_within_group=False, expected_raw_scores=None)` を実装 — contracts/explanation-v2.md §1 の手順 1-7 を固定(全行 pred_contrib → 有限性+raw 照合[供給時・rtol 1e-6・自己参照検査の廃止] → 全行列平均で centered → INV-E4 → 全馬同値特徴の候補除外[NaN 同値含む・混在は非同値] → |centered| 降順タイ特徴名昇順で K 未満許容 → v2 dict 構築)。防御 try/except は pred_contrib のみでなくセンタリング・ソート・JSON 化を含む経路全体に拡張。T003-T005 が緑になること
- [X] T007 [US1] serving/src/horseracing_serving/predictor.py: predict_race の説明呼び出しを更新 — `center = (model.objective in WinModel.SOFTMAX_OBJECTIVES and model.market_offset is None)`(SOFTMAX_OBJECTIVES は training predictor の集合を import=文字列二重管理禁止)とし、`expected_raw_scores` は **center=True のときのみ** `raw` を供給する(center=False では渡さない — offset モデルの `raw` は log-q offset 込みで pred_contrib 合算[tree margin]と恒常的に不一致になり、無条件供給は v1 説明を全滅させる=analyze U1)。呼び出し側にも try/except(失敗→全行 None・予測無傷)
- [X] T008 [P] [US1] serving/tests: 回帰テストを追加 — (a)説明変更前後・center on/off で predictions/snapshots がバイト一致(INV-E2/SC-002) (b)pl_topk(非 offset)モデル→method_version 2+centered フィールドあり (c)binary モデル→method_version 1(centered キーなし) (d)market-offset モデル→**explanation が None ではなく** method_version 1 で保存される(offset 込み raw を照合に使うと全滅する U1 欠陥経路の再発防止アサート) (e)booster None→全行 None
- [X] T009 [P] [US1] specs/040-prediction-explanation/contracts/prediction-explanation.md: 冒頭に「本契約は v1(method_version=1)。v2(レース内センタリング)は specs/089-explanation-race-centering/contracts/explanation-v2.md を正とする」を追記(FR-016・codex #12)

**Checkpoint**: 保存側は完成 — 単体+serving 回帰緑・確率バイト不変を機械確認済み

---

## Phase 4: User Story 2 - 表示の意味論更新 (Priority: P2)

**Goal**: API が v2 フィールドを透過し(additive)、front が method_version 厳密分岐で
正しいラベル・注記・主値を表示する。

**Independent Test**: api テストで v1/v2 行の応答形状確認+front テストで
v1/v2/null/未知版/形式不整合/items 空の 6 状態表示確認。

- [X] T010 [US2] api/src/horseracing_api/schemas.py: additive 追加 — `ExplanationItem.contribution_centered: float | None = None`、`Explanation.score_centered: float | None = None` / `other_contribution_centered: float | None = None` / `centering_population_size: int | None = None`(追加しないと `model_validate` が v2 キーを黙って落とす=research D7。既存フィールドの削除・改名禁止)
- [X] T011 [P] [US2] api/tests: v1 保存行(キーなし)→新フィールド全て null・v2 保存行→値が透過されることのテストを追加。read-only(全 path GET)不変テストが緑のままであること
- [X] T012 [US2] openapi 同期: api から openapi.json を再生成し front/openapi.json・admin/openapi.json snapshot を更新、`pnpm gen:types` で front/src/api/schema.d.ts・admin/src/api/schema.d.ts を再生成、front/admin の check:openapi(drift-check)が緑・差分が additive のみであることを確認
- [X] T013 [US2] front/src/components/ExplanationPanel.tsx: method_version 分岐を実装 — `=== 2` 厳密一致で v2 表示(主値=contribution_centered・タイトル「レース内でのスコア寄与(上位k要因)」・注記「同一レース内の平均に対する、レース内正規化前の相対スコア寄与です(最終確率の内訳ではありません)」+既存因果注記・「その他の特徴(合算)」= other_contribution_centered・items 空は「このレースでは比較できる差がありません」)、`=== 1` は現行表示バイト同等、未知版と centered 欠落/非有限を含む v2 は「未提供」系表示(生値フォールバック禁止)(contracts §4)
- [X] T014 [P] [US2] front/src/components/ExplanationPanel.test.tsx: 6 状態のテストを追加 — v1 行(従来表示・退行なし)/v2 行(centered 主表示・注記・その他=centered)/null(未提供)/未知 method_version=3(未提供)/v2 で contribution_centered 欠落(未提供=形式不整合)/v2 で items 空(比較対象なし文言)

**Checkpoint**: US1+US2 で新規予測の保存→API→表示が一気通貫。v1 過去行の表示退行なし

---

## Phase 5: User Story 3 - 既存予測の再生成経路と実効性の監査 (Priority: P3)

**Goal**: 既存 `predict-backfill --force` で過去レースが v2 化されること、新馬戦で誤解表示が
実際に消えたことを実 DB で確認し、測定値を spec に転記する。

**Independent Test**: quickstart.md 手順 3 の実 DB E2E。

- [X] T015 [US3] 実 DB E2E(quickstart §3): 新馬戦を含む開催日を `predict-backfill --force --use-materialized` で再生成し、(a)再生成 run の explanation が method_version=2 で centered 系フィールドを持つ (b)新馬戦の top-5 に value 全馬 NaN 特徴(prev_finish/days_since_last/class_transition)が 1 件も出ない=SC-001 (c)保存値で score_centered≈Σcentered+other_centered=SC-003b (d)経験馬レースでは実値差の特徴(prev_finish の実値等)が引き続き根拠に残る=SC-007、を確認
- [X] T016 [P] [US3] 実 DB E2E: v1/v2 混在確認 — 未再生成レースの v1 行が front で従来表示のまま(SC-004)・market-offset candidate(lgbm-060-mkt 等)で予測した場合 v1 のままであること(モデルが serving 可能な場合のみ・不能なら単体テストで代替し記録)
- [X] T017 [US3] 測定結果の転記: T015 の実測値(新馬戦 n・全馬同値特徴の top-5 出現率 0%・変更前 29〜99.3% との対比)を specs/089-explanation-race-centering/spec.md の SC-001 と research.md D1 に追記

**Checkpoint**: 全 US 完了 — 誤解表示の解消が実 DB で定量確認済み

---

## Phase 6: Polish & Cross-Cutting Concerns

- [X] T018 [P] 全パッケージ検証: `uv run --project training pytest training/tests -q`・`uv run --project serving pytest serving/tests -q`・`uv run --project api pytest api/tests -q`・front/admin の `pnpm test`+`pnpm build`+tsc/eslint・ruff(変更ファイル)がすべて緑
- [X] T019 quickstart.md の全手順を通しで実施し、手順どおりに再現することを確認(乖離があれば quickstart を実態に合わせて修正)
- [X] T020 CLAUDE.md の SPECKIT 区間の 089 エントリを「実装完了」に更新(実測値・テスト数・残作業を記載。agent-context スクリプトは使わず Edit で手動)

---

## Dependencies & Execution Order

### Phase Dependencies

- Phase 1(T001)→ Phase 2(T002)→ US1(T003-T009)→ US2(T010-T014)→ US3(T015-T017)→ Polish
- US2 は US1 の v2 保存形式に依存(fixture は contracts の JSON 例で先行可能だが、統合確認は US1 後)
- US3 は US1+US2 完了が前提(実 DB E2E は表示まで確認するため)

### Within-Story Order

- US1: T003-T005(テスト・並列可)→ T006 → T007 →(T008・T009 並列可)
- US2: T010 →(T011 並列可)→ T012 → T013 →(T014 並列可)
- US3: T015 →(T016 並列可)→ T017

### Parallel Opportunities

- T003/T004/T005(同一ファイルだが独立テストケース群 — 実務上は連続実装)
- T008 と T009(serving テスト / 040 契約文書)
- T011(api テスト)は T010 直後に T012 と独立で先行可
- T014(front テスト)は T013 と同時進行可
- T016 は T015 と別レース対象で並列可

## Implementation Strategy

**MVP = US1**: 保存側だけで「新規予測の保存意味論の是正」が完結し、単体+回帰で独立検証
できる(表示は v1 のまま壊れない — API/front は未知キーを無視するだけ)。US2 で利用者に
届き、US3 で過去分と実効性を締める。各 checkpoint で停止・検証可能。

## Notes

- 予測確率バイト不変(INV-E2)は US1 の回帰テスト(T008a)が唯一の機械ゲート — 最初に赤/緑を確認する
- v1 バイト同一(INV-E5)は T002 のリファクタ段階から常に緑を維持する
- 保存済み v1 行の書き換え・削除は全フェーズで禁止(append-only)
- openapi 差分は additive のみ — 削除・改名が出たら設計ミスとして停止
