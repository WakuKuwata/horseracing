---
description: "Task list for 評価ハーネスと baseline"
---

# Tasks: 評価ハーネスと baseline (Evaluation Harness & Baseline)

**Input**: Design documents from `specs/003-eval-harness/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/

**Tests**: 含む。spec の Independent Test と憲法 III(評価先行)/IV(確率整合性)のため test タスクを
生成する。指標の数値正しさは合成データで、baseline は実 DB で検証する。

**Source of truth**: 窓スキーム・指標定義・Harville・ECE・許容誤差は research.md / data-model.md。
スキーマ・labels・model_versions・validation は Feature 001(`db/`)。

## Format: `[ID] [P?] [Story] Description`

- **[P]**: 並列実行可(異なるファイル・依存なし)
- パスはリポジトリ root 基準。評価パッケージは `eval/`

---

## Phase 1: Setup

- [X] T001 `eval/` のディレクトリ構成を plan.md 通りに作成(`eval/src/horseracing_eval/`, `eval/tests/{unit,integration}/`)
- [X] T002 `eval/pyproject.toml` を作成し依存定義(`horseracing-db` をパス依存 `../db`、numpy>=1.26, scikit-learn>=1.4, sqlalchemy>=2.0。dev: pytest, testcontainers[postgres], ruff)
- [X] T003 [P] `eval/pyproject.toml` に ruff 設定と `[tool.pytest.ini_options]`(integration マーカー)を追加

---

## Phase 2: Foundational (Blocking Prerequisites)

**⚠️ CRITICAL**: 完了までユーザーストーリー着手不可

- [X] T004 `eval/src/horseracing_eval/predictor.py`: `Predictor` Protocol、`RaceContext`、`Prediction`(contracts/predictor.md。fit no-op 可 / predict_race が全頭 win/top2/top3 を返す)
- [X] T005 `eval/src/horseracing_eval/dataset.py`: DB から評価データセット構築(started 母集団=entry_status、ラベル=`labels.derive_labels` の finished のみ、odds 取得)。data-model の母集団規約に従う
- [X] T006 `eval/src/horseracing_eval/splits.py`: expanding-window walk-forward fold(race_date 基準、2007 初期 train 専用、valid は 2008 から年次、train は valid より厳密に前)
- [X] T007 `eval/tests/conftest.py`: testcontainers PostgreSQL16 + `db/` の alembic を head まで適用、session、テスト間 truncate、合成データ投入ヘルパ

**Checkpoint**: 基盤完成

---

## Phase 3: User Story 1 - walk-forward 評価で予測品質を測れる (Priority: P1) 🎯 MVP

**Goal**: Predictor を expanding walk-forward で評価し、label 別の予測品質指標を決定論的に出す。整合性違反は fail-fast。

**Independent Test**: 合成データで各指標が手計算の期待値と一致し、整合性違反が fail-fast されることを検証。

### Tests for User Story 1 ⚠️

- [X] T008 [P] [US1] ユニット: `check_consistency` が範囲外(win>top2 等)・レース内合計逸脱(許容超過)を `ConsistencyError` で fail-fast、許容内は通す(SC-002)— `eval/tests/unit/test_consistency.py`
- [X] T009 [P] [US1] ユニット: LogLoss/Brier/AUC/NDCG/ECE が合成データ(既知の確率・着順)で手計算の期待値と一致、label 別・頭数別 ECE(SC-001)— `eval/tests/unit/test_metrics.py`
- [X] T010 [P] [US1] ユニット: expanding fold が train を valid より厳密に前にし、2007 を train 専用にする。**leakage test: どの fold でも valid 年の race_date が train に 1 件も漏れない**ことを明示検証(憲法 II/品質ゲート)。stub Predictor でハーネスを 2 回実行し決定論的に一致(SC-006)— `eval/tests/unit/test_splits_harness.py`

### Implementation for User Story 1

- [X] T011 [US1] `eval/src/horseracing_eval/consistency.py`: `check_consistency`(各馬 0<=win<=top2<=top3<=1 厳格 + レース内合計の label 別絶対誤差 既定 0.05/0.10/0.15、違反は `ConsistencyError`)
- [X] T012 [US1] `eval/src/horseracing_eval/metrics.py`: LogLoss/Brier/AUC/NDCG(sklearn)+ ECE(自前、等幅 bin・**bin 数 configurable**・label 別・頭数別)。安定ソート・空クラス時の AUC=None
- [X] T013 [US1] `eval/src/horseracing_eval/harness.py`: `evaluate(predictor, ...)` expanding walk-forward(fit(train)→predict(valid)→consistency 検証→集計)。overall + by_fold + by_field_size_ece、決定論的、空 fold/全馬非完走レースはスキップ記録

**Checkpoint**: US1 単独で予測品質評価が機能・テスト可能

---

## Phase 4: User Story 2 - baseline を測って「超えるべきバー」を確立する (Priority: P1)

**Goal**: 市場 baseline(人気順)と一様 baseline を Predictor として実装し、実データで評価して `model_versions` に保存。

**Independent Test**: 2007 取込データで両 baseline を walk-forward 評価でき、市場が一様を LogLoss で上回り、結果が metrics_summary に保存される。

### Tests for User Story 2 ⚠️

- [X] T014 [P] [US2] ユニット: MarketBaseline(1/odds 正規化 + Harville top2/top3、単調・Σ≈1/2/3、odds null/0 は微小ウェイト)と UniformBaseline(1/N cap、**N<3 少頭数の挙動も**)が整合性を満たす。MarketBaseline がリーク参照線マーカーを公開する — `eval/tests/unit/test_baselines.py`
- [X] T015 [P] [US2] 統合: 取込データ(合成 or 実)で市場・一様 baseline を walk-forward 評価し、label 別指標が算出され、市場が一様を LogLoss で上回る(SC-003/SC-004)— `eval/tests/integration/test_baseline_eval.py`
- [X] T016 [P] [US2] 統合: baseline 結果を `model_versions`(model_family='baseline')の metrics_summary に保存し再読込できる(SC-005)— `eval/tests/integration/test_store.py`

### Implementation for User Story 2

- [X] T017 [US2] `eval/src/horseracing_eval/baselines.py`: `MarketBaseline`(Harville、`is_leaky_reference=True` 等のマーカーで「結果確定時値=参照線専用」を明示)、`UniformBaseline`(Predictor 実装)
- [X] T018 [US2] `eval/src/horseracing_eval/store.py`: `save_baseline(session, model_version, result)` → `model_versions` 行 + metrics_summary(data-model の jsonb 形)
- [X] T019 [US2] `eval/src/horseracing_eval/cli.py` + `__main__.py`: argparse `evaluate-baseline --baseline market|uniform`(評価→保存→サマリ表示)

**Checkpoint**: US1+US2 が独立して機能(P1 = MVP 完了、baseline 比較が成立)

---

## Phase 5: User Story 3 - 運用品質(疑似ROI等)を測れる (Priority: P2)

**Goal**: 単勝シミュレーションで運用指標を出す。

**Independent Test**: 合成オッズ・着順で ROI/的中率/最大DD が期待値どおり算出される。

### Tests for User Story 3 ⚠️

- [X] T020 [P] [US3] ユニット: 疑似ROI/回収率/的中率/見送り率/最大ドローダウン/最大連敗数が合成データで期待値と一致 — `eval/tests/unit/test_operational.py`

### Implementation for User Story 3

- [X] T021 [US3] `eval/src/horseracing_eval/operational.py`: 単勝馬券シミュレーション(結果確定時オッズ=疑似評価、最小単勝ルール、閾値・見送り設定可能)。連系・推定オッズは deferred
- [X] T022 [US3] `harness.py`/`cli.py`: 運用指標を任意で算出・metrics_summary に追加

**Checkpoint**: 運用指標が算出可能

---

## Phase 6: User Story 4 - 評価結果を永続化し比較できる (Priority: P2)

**Goal**: fold 別・全体の評価を正規化保存し、モデル間比較レポートを出す。

**Independent Test**: 2 Predictor の評価を保存し、同一条件の指標差分が比較レポートで確認できる。

### Tests for User Story 4 ⚠️

- [X] T023 [P] [US4] 統合: fold 別 + 全体が保存され、2 Predictor の同一条件比較レポートが出る — `eval/tests/integration/test_compare.py`

### Implementation for User Story 4

- [~] T024 [US4] **deferred (not needed yet)**: `eval_runs` / `walkforward_window_results` の正規化テーブル。FR-015 は「必要なら」拡張と規定しており、fold 別結果は `metrics_summary.by_fold`(jsonb)に保存済みで `report.compare` が同一条件比較を満たすため、専用テーブルは検索・多モデル UI が要るまで保留(スキーマ変更を避ける)
- [X] T025 [US4] `eval/src/horseracing_eval/report.py` + store: fold 別結果を永続化し、複数 Predictor の比較レポートを出力

**Checkpoint**: 評価結果の永続化・比較が機能

---

## Phase 7: Polish & Cross-Cutting Concerns

- [X] T026 [P] `eval/README.md` を作成(CLI・テスト・依存・評価方針)。**FR-013 の責務境界/provenance を明文化**: baseline=参照線(結果確定 odds/popularity を使う)、特徴量フィーチャー=これらをモデル特徴量に使わない、serving=発走前のみ、の境界を記述
- [X] T027 ruff クリーン + 全テスト green を確認(`eval/` と `db/`: `uv run ruff check`, `uv run pytest`)
- [X] T028 (ローカル・任意) 実データ検証済み: 2007+2008 取込(計6905 eval races, errors=0)で market が uniform を全 label で LogLoss 上回り(win 0.1984<0.2504, top2 0.3143<0.3996, top3 0.3991<0.5066)、ECE も低い(win 0.0017)。SC-003/004/005 実データ確認

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: 依存なし
- **Foundational (Phase 2)**: Setup 完了に依存。全ストーリーを BLOCK(predictor/dataset/splits/conftest)
- **US1 (Phase 3)**: Foundational 後。評価ハーネスの中核(MVP)
- **US2 (Phase 4)**: US1(harness/metrics/consistency)に依存。baseline を差して実データ評価
- **US3 (Phase 5)**: US1 に依存(運用指標を追加)。US2 非依存
- **US4 (Phase 6)**: US1/US2 に依存(評価結果を正規化保存)。db migration 0005 を含む
- **Polish (Phase 7)**: 望むストーリー完了後

### User Story Dependencies

- **US1 (P1)**: Foundational 後に着手。他ストーリー非依存
- **US2 (P1)**: US1 の harness を使う。baselines/store/cli を追加
- **US3 (P2)**: US1 に運用指標を追加。US2 非依存
- **US4 (P2)**: US1/US2 の結果を保存。schema 拡張(db/)を伴う

### Within Each User Story

- テストを先に書き FAIL を確認 → 実装
- predictor/dataset/splits(基盤)→ consistency/metrics → harness → baselines/store/cli の順
- 指標は合成データで数値正しさを固定してから実装

### Parallel Opportunities

- Setup の T003、各ストーリーの test タスク [P] は並列可
- US1 完了後、US2 と US3 は概念的に並列(別ファイル中心)。US4 は US2 の後
- Polish の T026 は並列可

---

## Parallel Example: User Story 1

```bash
# US1 テストを並列起動(先に FAIL 確認):
Task: "unit consistency in eval/tests/unit/test_consistency.py"
Task: "unit metrics in eval/tests/unit/test_metrics.py"
Task: "unit splits+harness determinism in eval/tests/unit/test_splits_harness.py"
```

---

## Implementation Strategy

### MVP First (US1 + US2 = P1)

1. Setup → Foundational
2. US1: consistency + metrics + harness(合成データで数値・整合性・決定論を固定)→ 検証
3. US2: 市場・一様 baseline を実装し実データで評価、market が uniform を上回ることを確認、metrics_summary に保存 → 検証
4. ここで「予測品質を baseline と同一条件で比較できる」評価先行基盤が完成 → 特徴量・学習フィーチャーが着手可

### Incremental Delivery

1. Setup + Foundational
2. US1 → 予測品質評価(MVP の核)
3. US2 → baseline 比較(MVP 完成、憲法 III の採用条件が測れる)
4. US3 → 運用 ROI 指標
5. US4 → 永続化・比較レポート
6. Polish → README・lint・実データスモーク

---

## Notes

- [P] = 異なるファイル・依存なし
- 指標の数値正しさは合成データで手計算と突き合わせる(最重要)。整合性違反は fail-fast(憲法 IV)
- 窓スキーム・許容誤差・ECE bin・Harville は research.md / data-model.md を正本
- 市場 baseline の odds/popularity は「結果確定時値=参照線専用」。モデル特徴量に使わない(FR-013)
- baseline は `model_versions.metrics_summary` に保存(MVP スキーマ変更なし)。US4 の正規化保存は P2
