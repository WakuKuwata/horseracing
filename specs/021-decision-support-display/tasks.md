---
description: "Task list — 意思決定支援の表示強化 (021)"
---

# Tasks: 意思決定支援の表示強化 (Decision-Support Display)

**Input**: [plan.md](plan.md) / [spec.md](spec.md) / [research.md](research.md) / [data-model.md](data-model.md) / [contracts/api.md](contracts/api.md)

**Tests**: 憲法品質ゲート（leak / 契約 drift / pseudo invariant）に従い test タスクを含める。

**Organization**: user story 単位。MVP = US1。

## Format: `[ID] [P?] [Story] Description`

---

## Phase 1: Setup

- [X] T001 契約確定: [contracts/api.md](contracts/api.md) を確認し、`api/src/horseracing_api/schemas.py` に追加するフィールド名/型/nullability/source ラベルを fix（UI 実装前、憲法 VI / R7）
- [X] T002 [P] 実 DB 前提を確認（horseracing DB head=0006・2007–2024、[[local-db-setup]]）。US2 用に walk-forward OOS reliability を含む `model_versions` 行が必要なことを quickstart に沿って把握

## Phase 2: Foundational（全 US の前提）

- [X] T003 `api/src/horseracing_api/schemas.py` に read-only スキーマを追加: `HorsePrediction.market_win_prob: float|None` / `data_backing: Literal["weak","medium","strong"]|None`、`PredictionResponse` に `market_prob_source`/`canonical_consistent`/`odds_as_of`/`odds_source`、新 `CalibrationResponse`/`Bin`（`Bin` は `realized_ci_low`/`realized_ci_high`/`count`/`suppressed` を含む＝FR-006b）（data-model §1/§4/§5、contracts §1/§2）。EV/控除率・q' フィールドは追加しない（021 スコープ外, analyze G1/G2）
- [X] T004 `api/src/horseracing_api/selection.py` に「同一 canonical field で p と q を算出」するヘルパを追加（`canonical_win_probs` の母集団で `market_implied_win_probs`(010) を適用し field 上で再正規化、有効オッズ無→null、母集団不一致→`canonical_consistent=false`）（R1, 憲法 IV）

**Checkpoint**: スキーマ契約と p/q 同一母集団ヘルパが揃う。

---

## Phase 3: User Story 1 - モデル p と市場 q の併記 (P1, MVP)

**Goal**: 各馬の p と q を別フィールドで返し、front が中立に併記。

**Independent Test**: predictions に `win`(p) と `market_win_prob`(q) が別フィールドで入り、有効オッズ無は null、`canonical_consistent=true` で Σq≈1、front が中立提示（色/ソート/利益語なし）+ q に PseudoBadge。

### 実装
- [X] T005 [US1] `api/src/horseracing_api/routers/predictions.py`: T004 ヘルパで各 horse に `market_win_prob` を充填し、`market_prob_source="win_odds_vote_share"`/`canonical_consistent`/`odds_as_of`/`odds_source` を設定（write しない、既存 run 選択ロジック不変）
- [X] T006 [P] [US1] `front/src/components/PQCompare.tsx` を新規: p と q を併記し p−q を**中立提示**（緑赤・利益語・p−q ソート/ハイライト禁止, R3）、q に `<PseudoBadge>`（市場推定/FL bias 含む）、`odds_as_of`/`odds_source` 表示。`market_win_prob=null` は `formatNum`→未提供
- [X] T007 [P] [US1] `front/src/` レース詳細に PQCompare を組み込み、「市場 q の方が予測上手い(020)」注記を表示（FR-017）

### US1 テスト
- [X] T008 [P] [US1] `api/tests/integration/test_predictions_market_q.py`: p と q が同一 canonical field（Σq≈1、頭数一致）、有効オッズ無→`market_win_prob=null`（0 でない）、スクラッチ除外整合、`canonical_consistent` の真偽、エンドポイントが GET/read-only（write 関数未呼出）
- [X] T009 [P] [US1] `front/src/__tests__/pqcompare.test.tsx`: p/q 併記表示、q に pseudo ラベル必須、**中立提示（損益色/利益語/edge ソートが無い）**、null 安全（未提供）、`canonical_consistent=false` 時は乖離を出さない（SC-001/002/007/008）

**Checkpoint**: US1 単独で意思決定支援の中核（p/q 併記）が成立。

---

## Phase 4: User Story 2 - 予測校正の可視化 (P2)

**Goal**: walk-forward OOS reliability を read-only で公開・可視化。

**Independent Test**: `/models/{mv}/calibration` が `oos=true`・`source="walk_forward_oos"`・件数付き bins・ece・valid_years を返し、未収録は 404 typed。front が件数付き reliability 図を retrospective/OOS 明示で描画。

### 実装
- [X] T010 [US2] `eval/src/horseracing_eval/harness.py`: walk-forward OOS の reliability bins（pred_lo/hi・pred_mean・realized_rate・**realized_ci_low/high＝Wilson 信頼区間**・count、等幅、少数 bin は `suppressed`）と全体 ECE/n_total/valid_years を `EvalResult` summary に出力（既存 ECE binning を拡張、独自指標を作らない, R5/FR-006b/analyze U1/憲法 III）
- [X] T011 [US2] `training/src/horseracing_training/artifacts.py`（save_model_version 経路）: adoption 時に T010 の reliability を `model_versions.metrics_summary`(JSONB) へ追記（スキーマ変更なし、R2/R8）
- [X] T012 [US2] `api/src/horseracing_api/routers/calibration.py` を新規 + `queries.py`: `GET /api/v1/models/{model_version}/calibration` が metrics_summary を read し `CalibrationResponse` を返す（再計算しない、未収録→404 typed `calibration_unavailable`）。`app.py` に router 登録
- [X] T013 [P] [US2] `front/src/components/CalibrationChart.tsx` を新規 + レース/モデル画面に組込: 予測 vs 実現の reliability 図を**件数 + 信頼区間（realized_ci_low/high）付き**で描画（FR-006b）、`source`/OOS/model_version/valid_years/n_total を監査表示、`suppressed` bin は明示

### US2 テスト
- [X] T014 [P] [US2] `eval/tests/integration/test_reliability_bins.py`: OOS reliability bins が算出され（件数・realized_rate・ece）、少数 bin が `suppressed`、in-sample でなく walk-forward 由来であること
- [X] T015 [P] [US2] `api/tests/integration/test_calibration_endpoint.py`: metrics_summary から read、`oos=true`/`source`、bins 件数、未収録 model_version→404 typed、GET/read-only

**Checkpoint**: US1 + US2 で確率の信頼性まで提示できる。

---

## Phase 5: User Story 3 - データ裏付け（条件カバレッジ） (P3, 採用判定先行)

**Goal**: リーク安全な「データ裏付け」を**過去 OOS で妥当性確認できた場合のみ**併記（不可なら defer）。

**Independent Test**: data_backing が weak/medium/strong で付与され、weak 群が校正/誤差で劣ることを検証済み、指標が結果/オッズ不使用（事前情報のみ）。

### 採用判定（実装前ゲート, 憲法 III）
- [X] T016 [US3] `eval/` にスクリプト/関数: 候補指標（馬の過去出走数 Unknown 有無 + field_size の粗いカテゴリ）で過去 OOS を層別し「weak 群の校正(ECE)/誤差が medium/strong より悪い」かを検証。**結果を plan/research に記録し採否を決定**（妥当でなければ T017–T020 を defer し US3 をスキップ, FR-012）
### 実装（T016 が採用の場合のみ）
- [X] T017 [US3] `api/src/horseracing_api/`（selection/predictions）: data_backing をリーク安全に算出（事前情報のみ、結果/オッズ/表示派生値 不使用）し `HorsePrediction.data_backing` に充填
- [X] T018 [P] [US3] `front/src/components/DataBackingBadge.tsx` + レース詳細組込: 裏付け弱を区別表示し「的中確信ではない」明示（粗い注意バッジ）

### US3 テスト（採用時）
- [X] T019 [P] [US3] `api/tests/integration/test_data_backing.py`: data_backing が事前情報のみで算出（結果/オッズ非依存）、カテゴリ妥当、null 安全
- [X] T020 [P] [US3] `front/src/__tests__/databacking.test.tsx`: 裏付け弱の区別表示と「的中確信でない」注記

**Checkpoint**: US3 は検証に通れば併記、通らなければ defer（正直）。

---

## Phase 6: Polish & 横断

- [X] T021 [P] 契約同期: `front/openapi.json` を live `/openapi.json` から再生成 + 生成型更新、drift-check test 緑（SC-005, 憲法 VI）
- [X] T022 [P] leak-guard test（`api/tests/` or `features/tests/`）: `market_win_prob`/`data_backing`/reliability/EV 等の表示派生値・オッズ・結果が `model_input_features` に出現しないことを assert（SC-006, 憲法 II / R9）
- [X] T023 [P] front pseudo invariant test 拡張: q/q'/recomputed 校正含め「ラベルなし pseudo/推定値 0 件」を単一 PseudoBadge 経路で保証（SC-002, 憲法 V）
- [X] T024 read-only invariant test: 新エンドポイント/フィールドが GET のみ・write 関数（`generate_*`）を呼ばない（014 規約）
- [X] T025 lint/test ゲート: `uv run ruff check` + `uv run pytest`（api/eval/training）、`pnpm test` + 型 drift（front）緑
- [ ] T026 実 DB スモーク（[quickstart.md](quickstart.md)）: US1（p/q 併記）・US2（calibration）・US3（採否）を実データで確認
- [X] T027 [P] `CLAUDE.md` に 021 の 1 行サマリを追記（014–020 と同形式: p/q 同一 canonical field 併記・中立提示・OOS reliability を metrics_summary 経由 read・データ裏付けは検証先行で採否・read-only/スキーマ変更なし・市場優位の明示）

---

## Dependencies & Execution Order

- **Phase 1 → 2**: Setup → Foundational（T003 スキーマ・T004 p/q ヘルパ）が全 US をブロック。
- **Phase 3 (US1)**: T004 後。T005→T006/T007、テスト T008/T009[P]。
- **Phase 4 (US2)**: T010→T011→T012→T013、テスト T014/T015[P]。US1 と独立（別ファイル）だが openapi 再生成(T021)は両者後。
- **Phase 5 (US3)**: **T016 採用判定が gate**。採用時のみ T017→T018、テスト T019/T020。
- **Phase 6**: 全実装後。T021–T025[P 可]、T026、T027[P]。

### User Story 独立性
- US1（p/q 併記）= MVP、単独で価値。US2（校正）= 独立、別エンドポイント。US3（データ裏付け）= 検証先行・defer 可で最も独立。

## Parallel 実行例
- US1: T006/T007（front）と T008/T009（test）を T005 後に並行。
- US2: T013（front）と T014/T015（test）。
- Polish: T021/T022/T023/T027[P]。

## 実装戦略
1. **MVP**: Phase 1→2→3（US1 p/q 併記）で意思決定支援の中核を提示。
2. **信頼性**: US2 で OOS 校正可視化（in-sample 楽観を避ける）。
3. **過信防止**: US3 はデータ裏付けを検証先行で、通らなければ defer（憲法 III の正直さ）。
4. 各 Checkpoint で独立テスト緑。憲法 II（leak-guard・表示値非流用）/ III（OOS 校正・US3 検証先行）/ IV（p,q 同一 canonical field）/ V（pseudo ラベル・監査）/ VI（API 契約先行・drift-check・read-only）を維持。

## analyze 反映（意図的スコープの明示）
- **G1 (HIGH 解消)**: EV/控除率 表示は 021 スコープ外（契約・タスクに EV フィールドを足さない）。FR-018 は将来 EV を出す時の制約、SC-009 から EV 節を除外。
- **U1 (MEDIUM 解消)**: reliability bin に Wilson 信頼区間（realized_ci_low/high）を追加（FR-006b）→ T003/T010/T013。
- **G2 (LOW)**: 生 q と FL 補正 q'(013) の併記は意図的に deferred（021 は生 q のみ。q' を出す場合 FR-002a を将来適用）。タスク化しない＝抜けではなく意図。
- **A1 (LOW)**: predictions への q 追加は per-horse win/q のみで 009 joint に影響しない（joint は既存 `?bet_type=` 経路のまま不変）。本 feature で joint テストは新設不要。
- **I1 (解消済)**: CLAUDE.md の active-plan ポインタは 021 に更新済み（T027 で 1 行サマリも追記）。
