---
description: "Task list for 結合確率エンジン"
---

# Tasks: 結合確率エンジン (Joint Probability Engine)

**Input**: Design documents from `specs/009-joint-probability-engine/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/

**Tests**: 含む。spec の Independent Test と憲法 III/IV のため test タスクを生成する。
**手計算 golden・整合性不変条件(周辺=harville)・再正規化順序が最重要テスト**(035/036 の確率校正ミス対策)。

**Source of truth**: PL 式・ワイド/複勝・再正規化順序・整合性自己検査・校正評価は research.md / data-model.md /
contracts/。`harville_topk` は Feature 003、metrics は Feature 003。

## Format: `[ID] [P?] [Story] Description`

- **[P]**: 並列実行可(異なるファイル・依存なし)
- パスはリポジトリ root 基準。確率パッケージは `probability/`

---

## Phase 1: Setup

- [X] T001 `probability/` のディレクトリ構成を plan.md 通りに作成(`probability/src/horseracing_probability/`, `probability/tests/{unit,integration}/`)
- [X] T002 `probability/pyproject.toml` を作成し依存定義(`horseracing-db`/`horseracing-eval` をパス依存、numpy, sqlalchemy>=2.0。dev: pytest, testcontainers[postgres], ruff)
- [X] T003 [P] `probability/pyproject.toml` に ruff 設定 + `[tool.pytest.ini_options]`(integration マーカー、tests E501 ignore)+ `__init__.py`(`PROBABILITY_LOGIC_VERSION`)を追加

---

## Phase 2: Foundational (Blocking Prerequisites)

**⚠️ CRITICAL**: 完了までユーザーストーリー着手不可

- [X] T004 `probability/src/horseracing_probability/engine.py` の基盤: `JointProbabilities` dataclass + 正規化/clip ヘルパ(入力=出走母集団の win dict → Σ=1 再正規化 → `[eps,1-eps]` clip → 再正規化。**再正規化を PL 分母計算より先に**、INV-J1/J6。取消・除外の除去は呼び出し側責務)(contracts/engine.md, R2)

**Checkpoint**: 基盤完成(正規化・clip・出力型)

---

## Phase 3: User Story 1 - 単勝確率から全券種の的中確率を整合的に導出 (Priority: P1) 🎯 MVP

**Goal**: 単勝確率 → 全 7 券種の組み合わせ確率を PL で導出、整合性保証。

**Independent Test**: 既知入力(N=3/4・一様)で手計算 golden と一致、整合性不変条件(Σ=1・無順序=順序和・
周辺=harville・範囲・単調)をすべて満たす。

### Tests for User Story 1 ⚠️

- [X] T005 [P] [US1] ユニット(最重要): N=3/4・一様の**手計算 golden**で win/place/exacta/quinella/wide/trifecta/trio の値が許容内一致(SC-001)— `probability/tests/unit/test_golden.py`
- [X] T006 [P] [US1] ユニット(最重要): 整合性 — `Σ馬単=1`・`Σ三連単=1`・`quinella=exacta双方向和`・`wide=Σ_k trio`・`wide>=quinella`・**`trifecta周辺=harville_topk.top3`**・`exacta周辺=harville_topk.top2`・包含∈[0,1]・単調(SC-002)— `probability/tests/unit/test_consistency.py`
- [X] T007 [P] [US1] ユニット: 取消・除外を除外し残存馬で再正規化してから派生、取消馬の確率 0(SC-003)。端点(p→1, 1−Σ→0)でゼロ割/範囲逸脱なし、決定論(SC-004)。複勝の頭数依存(5–7=top2/8+=top3/**≤4=None**)・小頭数縮退(**wide は N<3 で None、trifecta/trio は空 dict**、contracts/engine.md と一致)(SC-006)— `probability/tests/unit/test_edge_renorm.py`

### Implementation for User Story 1

- [X] T008 [US1] `engine.py`: `joint_probabilities(win_probs, field_size=None, eps=...)`(PL: exacta `p_i·p_j/(1−p_i)`、trifecta 逐次。無順序=順序和、`wide=Σ_k trio`、`place=harville top-N`、win passthrough。分母事前キャッシュ)(FR-001/002/003/005, R1/R4)
- [X] T009 [US1] `probability/src/horseracing_probability/consistency.py`: `check_joint_consistency`(Σ=1・無順序=順序和・周辺=harville・範囲・単調を fail-fast 検査)(contracts/engine.md, FR-006)

**Checkpoint**: US1 単独で全券種確率 + 整合性保証が成立(MVP の核、憲法 P0)

---

## Phase 4: User Story 2 - 結合確率の校正を過去データで評価 (Priority: P1)

**Goal**: エンジンの結合確率の校正を過去データで評価し、独立積 baseline と比較。

**Independent Test**: 合成/過去データで、実現組み合わせの NLL/Brier が独立積 baseline と同一条件で比較され、PL が
baseline を悪化させない。

### Tests for User Story 2 ⚠️

- [X] T010 [P] [US2] ユニット: `independent_product_joint`(exacta∝p_i·p_j / trifecta∝p_i·p_j·p_k を Σ=1 再正規化)が PL と異なる分布を返す。`evaluate_calibration` が実現組み合わせの NLL/Brier を算出 — `probability/tests/unit/test_calibration.py`
- [X] T011 [P] [US2] 統合: 実 DB(合成 race_predictions + race_results)で `evaluate_calibration` が PL と independent_product の CalibrationReport を同一レース集合で返し、確率導出が結果/オッズ非参照(SC-005/007)— `probability/tests/integration/test_calibration_db.py`

### Implementation for User Story 2

- [X] T012 [US2] `probability/src/horseracing_probability/calibration.py`: `independent_product_joint` + `evaluate_calibration(session, start_date, end_date, bet_type)`(race_predictions→確率、race_results→実現組み合わせ、`eval.metrics` で NLL/Brier、PL vs baseline。取消・除外/未完走/同着を規則で扱う)(contracts/calibration.md, FR-009, R5/R6)

**Checkpoint**: US1+US2 = 確率エンジン + 校正評価(評価先行)完成

---

## Phase 5: User Story 3 - レースの組み合わせ確率を CLI で表示 (Priority: P2)

**Goal**: prediction_run/レース指定で券種別 上位 K 組み合わせ確率を表示。

**Independent Test**: prediction_run 指定で各券種の上位 K 組み合わせ確率と整合性メタが表示される。

### Tests for User Story 3 ⚠️

- [X] T013 [P] [US3] 統合: CLI `show` が prediction_run の race_predictions から各券種の上位 K 組み合わせ確率を表示(整合性検査を通る)— `probability/tests/integration/test_cli_show.py`

### Implementation for User Story 3

- [X] T014 [US3] `probability/src/horseracing_probability/cli.py` + `__main__.py`: `show --prediction-run/--race-id --top K`(race_predictions→joint_probabilities→上位表示)、`calibrate --from --to --bet-type`(校正評価表示)

**Checkpoint**: US1+US2+US3 = エンジン + 評価 + CLI が完成

---

## Phase 6: Polish & Cross-Cutting Concerns

- [X] T015 [P] `probability/README.md` を作成(概要・PL 式・ワイド=trio 第3頭和・再正規化順序・整合性(周辺=harville)・複勝N依存・校正評価・リーク境界・exotic オッズ/EV は将来)
- [X] T016 ruff クリーン + 全テスト green を確認(`probability/`: `uv run ruff check`, `uv run pytest`)
- [X] T017 (ローカル・任意) 実データ(2008 取込 + active モデルの prediction_run)で `show` と `calibrate` を実行し、整合性・PL vs 独立積 baseline の校正を確認

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: 依存なし
- **Foundational (Phase 2)**: Setup 完了に依存。全ストーリーを BLOCK(正規化/clip/出力型)
- **US1 (Phase 3)**: Foundational 後。確率エンジン中核(MVP、憲法 P0)
- **US2 (Phase 4)**: US1(engine)を使って校正評価
- **US3 (Phase 5)**: US1/US2 を CLI で公開
- **Polish (Phase 6)**: 望むストーリー完了後

### User Story Dependencies

- **US1 (P1)**: Foundational 後。engine + consistency
- **US2 (P1)**: US1 の engine に依存(校正評価が joint_probabilities を呼ぶ)
- **US3 (P2)**: US1/US2 の後

### Within Each User Story

- テストを先に書き FAIL を確認 → 実装
- **手計算 golden(T005)・整合性=周辺一致(T006)・再正規化順序(T007)を最優先で固定**
- engine 基盤 → joint_probabilities → consistency → calibration → cli の順

### Parallel Opportunities

- Setup の T003、各ストーリーの test タスク [P] は並列可
- US1 の test(T005/T006/T007)は並列可。実装 engine/consistency は順次(同一/関連ファイル)
- Polish の T015 は並列可

---

## Implementation Strategy

### MVP First (US1 = P1 MVP)

1. Setup → Foundational(正規化/clip/出力型)
2. US1: PL で全券種導出 + 整合性(周辺=harville)→ 憲法 P0「結合確率エンジン」完成
3. ここで exotic 確率の基盤が完成(将来の exotic EV/推奨の入力)

### Incremental Delivery

1. Setup + Foundational
2. US1 → 結合確率エンジン + 整合性(MVP)
3. US2 → 校正評価 + 独立積 baseline(評価先行)
4. US3 → CLI
5. Polish → README・実データスモーク

---

## Notes

- [P] = 異なるファイル・依存なし
- **codex 確率レビューが核**: ①ワイド=Σ_k trio(独立積禁止)②除外→再正規化→clip→派生 の固定順序(再正規化を PL
  分母より先)③harville の分母 skip を継承しない ④**joint 周辺=harville_topk** を自己検査(独立実装の一致)
- リーク境界: 確率導出は結果/オッズ非参照。校正評価のみ結果を採点に使う
- スキーマ変更なし。exotic オッズ取得・推定オッズ変換・exotic EV/推奨・同着確率モデルは将来(P0)
