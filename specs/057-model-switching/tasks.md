---
description: "Task list for 057 model-switching implementation"
---

# Tasks: 複数モデル切り替え基盤(用途ラベル + レース詳細でのモデル切替)

**Input**: Design documents from `specs/057-model-switching/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/api.md, quickstart.md

**Tests**: 含む(憲法 III「評価先行」+ 品質ゲートでテスト必須。ただし本 feature はモデル変更なしのため walk-forward 評価は対象外 = 契約/後方互換/リーク境界/read-only テストが中心)。

**Organization**: user story ごとにグループ化。US1(用途ラベル)/US2(任意モデル予測)は P1、US3(front セレクタ)は P2。

**作業ディレクトリ**: worktree `.claude/worktrees/057-model-switching`(ブランチ `057-model-switching`)。以下のパスは repo ルート相対。

## Format: `[ID] [P?] [Story] Description`

---

## Phase 1: Setup

**Purpose**: 変更前のグリーンベースライン確立(回帰検知の基準)。

- [x] T001 worktree で既存テストのグリーンベースラインを取得(`uv run --project db pytest`・`--project api pytest`・`front` と `admin` の `pnpm test` / `pnpm check:openapi`)。変更前の全緑を記録。

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: スキーマ変更。US1 の用途表示・US2 の available_models 表示が依存するため全 user story に先行。

**⚠️ CRITICAL**: このフェーズ完了まで US1/US2 のバックエンド作業は開始不可。

- [x] T002 migration 追加 `db/migrations/versions/0011_model_purpose.py`: `revision="0011_model_purpose"` / `down_revision="0010_raw_column_features"`(実 head の full slug)。`model_versions` に `display_name TEXT NULL` / `purpose TEXT NULL` を add、downgrade で 2 列 drop。既存列・PK は不変。
- [x] T003 `db/src/horseracing_db/models/prediction.py` の `ModelVersion` に `display_name: Mapped[str | None]` / `purpose: Mapped[str | None]`(`mapped_column(Text)`)を追加。既存 `label_schema` と別物であることをコメントで明記。
- [x] T004 [P] alembic head を assert するテスト(`versions[-1].startswith("0010_")`)を `startswith("0011_")` に更新。**6 箇所**: `features/tests/unit/test_feature020_leak_guard.py:34`・`test_feature021_leak_guard.py:29`・`test_feature023_leak_guard.py:35`・`test_feature040_leak_guard.py:31`・`test_materialize_fallback_columns.py:65`・`live/tests/unit/test_no_schema_change.py:15`(0008/0009 の波及前例に倣う。念のため `grep -rn 'startswith("0010_")' features/ live/ db/` で取りこぼし確認)。
- [x] T005 db 統合テスト `db/tests/integration/`: migration up で 2 列が存在・既存行は NULL・downgrade で drop を確認(testcontainers)。

**Checkpoint**: スキーマ準備完了 — US1/US2 バックエンド開始可。

---

## Phase 3: User Story 1 - 用途ラベル (Priority: P1) 🎯 MVP(薄い)

**Goal**: 各モデルに人間可読の用途(display_name/purpose)を設定・表示できる。技術 ID は不変。

**Independent Test**: CLI で用途を設定 → `/api/v1/models` と admin レジストリに透過表示(未設定=null)、`model_version` は不変。

### Implementation for User Story 1

- [x] T006 [P] [US1] 用途メタ書込 CLI を追加(training/registry 層、例 `set-model-label --model-version <mv> --display-name <name> --purpose <text>`): `model_versions` を DB read-write で更新(冪等上書き・空文字は NULL 扱い)。**API には書込を足さない**(read-only 維持)。
- [x] T007 [P] [US1] `api/src/horseracing_api/schemas.py` の `ModelVersionRow` に `display_name: str | None = None` / `purpose: str | None = None` を純追加。
- [x] T008 [US1] `api/src/horseracing_api/routers/models.py` `_row` で `display_name`/`purpose` を透過(転記のみ・021 規律)。必要なら `api/src/horseracing_api/queries.py` `list_model_versions` が ORM 行から両列を取得することを確認。
- [x] T009 [US1] api 統合テスト: `/models` が display_name/purpose を返す(設定済み + 未設定 null)、500 にならない。
- [x] T010 [US1] CLI テスト: `set-model-label` が設定・上書きする、未指定は NULL のまま、**空文字指定は NULL として保存(空文字 "" を格納しない、data-model 準拠)**、存在しない model_version は明示エラー。**FR-009 ガード**: 書込後に対象行の `adoption_status` が不変であること(用途メタ設定が採用状態に触れない=「用途設定 ≠ 昇格」)をアサート。
- [x] T011 [US1] admin `ModelRegistryPage.tsx` / `ModelDetailPage.tsx` に用途(display_name/purpose)列を表示(未設定=「未設定」)。**T018(型再生成)後に着手**。
- [x] T012 [US1] admin Vitest: レジストリ/詳細が display_name/purpose を描画・null を安全表示。

**Checkpoint**: モデルの用途が API/admin で言葉で判別できる(SC-001)。

---

## Phase 4: User Story 2 - 任意モデルで予測取得 (Priority: P1)

**Goal**: 予測をモデル指定で取得できる。省略時=採用モデル(完全後方互換)。run 不在=typed 404。応答に available_models。

**Independent Test**: 1 レースに 2 モデルの run を永続化 → 未指定で採用モデル、指定で該当モデル、run 無し指定で 404(no-fallback)。API 単体でテスト可能。

### Implementation for User Story 2

- [x] T013 [US2] `api/src/horseracing_api/selection.py` `select_prediction_run(session, race_id, model_version: str | None = None)`: 指定時は `PredictionRun.model_version == model_version` で絞り `computed_at DESC → run_id DESC`(active-first case を外す)、未指定時は現行完全維持、該当なし `None`。
- [x] T014 [US2] `api/src/horseracing_api/queries.py` に `available_models_for_race(session, race_id, selected_model_version)`: `prediction_runs` の distinct `model_version` を `model_versions` に JOIN し display_name/purpose/adoption_status + is_selected を返す(1 クエリ・read-only)。**決定的順序 active-first → created_at DESC → model_version**(051 `list_model_versions` と同一、憲法 V 再現性)。
- [x] T015 [P] [US2] `api/src/horseracing_api/schemas.py` に `AvailableModel`(model_version/display_name/purpose/adoption_status/is_selected)+ `PredictionResponse.available_models: list[AvailableModel] = []` を純追加。
- [x] T016 [US2] `api/src/horseracing_api/routers/predictions.py`: `model_version: str | None = Query(default=None)` を追加、`select_prediction_run` に伝播、指定かつ run 不在は typed 404 `prediction_unavailable`(**active フォールバック禁止**)、応答に `available_models` を組立。未指定経路は挙動不変。
- [x] T017 [US2] api テスト: (a) 未指定=導入前と同一 run・同一馬確率(後方互換 SC-002)、(b) 指定=そのモデルの run、(c) run 無し指定→404 no-fallback、(d) 不在 model_version→404(500 でない)、(e) available_models の内容・is_selected・**決定的順序(active-first → created_at DESC → model_version)**、(f) 指定モデルに run 複数時の tie-break(computed_at DESC → prediction_run_id DESC)、(g) active 不在レースで未指定=既存 typed-empty 維持。
- [x] T018 [US2] **契約確定(共有)**: front/admin 両方の `openapi.json` + `schema.d.ts` を再生成(US1+US2 の追加を取り込む)。front↔admin snapshot byte 一致 + drift-check(`pnpm check:openapi`)緑。**T011(admin 表示)/US3(front)はこのタスクに依存**。
- [x] T019 [US2] read-only 不変テスト(全 path GET)緑を確認、OpenAPI が純追加(削除・変更なし)であることを確認。**FR-009 ガード**: 本 feature が追加/変更する経路(予測 API・models API・available_models クエリ)が `adoption_status` を一切書き換えないこと(採用ロジック無改変=API から自動昇格しない)をテスト/レビューで固定。

**Checkpoint**: API がモデル指定で切替可能、未指定は完全後方互換。

---

## Phase 5: User Story 3 - front セレクタ (Priority: P2)

**Goal**: レース詳細で予測モデルを切り替え。既定=採用モデル・採用バッジ・「未生成」を独立状態表示。

**Independent Test**: レース詳細で既定=採用モデル(バッジ付)→ 別モデル選択で再取得・切替 → run 無しモデルで「未生成」状態(loading/empty/error と別)。

**前提**: T018(型再生成)完了済み。

### Implementation for User Story 3

- [x] T020 [US3] `front/src/api/queries.ts` `usePredictions(raceId, modelVersion?)`: queryKey に modelVersion を含め、`?model_version=` を付与。未指定時は現行と同一。
- [x] T021 [P] [US3] `front/src/components/ModelSelector.tsx` 新規: `available_models` を選択肢に描画、採用モデルにバッジ、選択中を明示、onChange で選択を親に通知。
- [x] T022 [US3] `front/src/pages/RaceDetailPage.tsx`: 選択モデル state(既定=応答の active/選択 run)、ModelSelector 結線、選択を usePredictions に渡し再取得、「どのモデルを見ているか」常時明示、選択モデル run 不在(404)を **loading/empty/error と別の「未生成」状態**(専用 testid)で表示。
- [x] T023 [US3] front Vitest+MSW: (a) 既定で採用モデル + バッジ、(b) 別モデル選択で `?model_version=` 付き再取得・表示切替、(c) run 無しモデル→専用「未生成」testid(loading/empty/error と区別)、(d) **非採用モデルを選択中も採用バッジは採用モデル側に正しく付く**(選択中 ≠ 採用の区別・FR-006 の「常時明示 + 採用を視覚的に区別」)。

**Checkpoint**: 全 user story が独立に機能。

---

## Phase 6: Polish & Cross-Cutting

- [x] T024 E2E は **testcontainer 実 Postgres 16** で全経路検証済み(migration 0011 up/down・set-model-label 書込/空→NULL/adoption 不変・?model_version 選択・404 no-fallback・available_models/is_selected/決定的順序・front 切替/未生成・admin 用途表示)。**プロダクト DB への migration 0011 適用は deploy 手順**(ユーザーの実データを変更するため本実装では未適用。quickstart.md の手順で適用可能)。
- [x] T025 [P] Lint/型クリーン: ruff(db/api)、tsc + eslint(front/admin)。
- [x] T026 全スイート緑: db / api / front / admin + drift-check。既存 api テスト無改修で緑(SC-002/SC-005)。
- [x] T027 spec.md Status を実装完了へ更新、結果を plan/spec 要約 + CLAUDE.md ポインタに反映。メモリに 057 の学び(切替基盤・available_models 設計)を記録。

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (P1)**: 即開始可。
- **Foundational (P2)**: Setup 後。**全 user story のバックエンドをブロック**。
- **US1 / US2 (P1)**: Foundational 後。バックエンド部分は並行可(別ファイル中心)。
- **US3 (P2)**: **T018(型再生成)後**。T018 は T008(US1 backend)+ T016(US2 backend)に依存。
- **Polish**: 全 story 後。

### 重要な cross-story 依存(共有契約)

- **T018(openapi 再生成 + drift-check)** が US1 と US2 のバックエンド契約変更を両方取り込む。
  - T011(admin 用途表示)と US3(front 全体)は T018 に依存。
  - つまり admin 表示と front は「US1 backend(T008)+ US2 backend(T016)+ T018」が揃ってから。

### Within each story

- Tests は実装後に緑化(本 feature はモデル非変更のため TDD 必須ではないが、後方互換/404/read-only の回帰テストを実装と同 PR で追加)。
- Model → service/query → endpoint → UI の順。

### Parallel Opportunities

- T004(head-assert)は T002/T003 と別ファイルで並行可。
- US1 の T006(CLI)/T007(schema)は並行可。
- US2 の T015(schema)は T013/T014 と別ファイルで並行可。
- US3 の T021(ModelSelector)は T020 と並行可。
- US1 backend(T006-T010)と US2 backend(T013-T017)は概ね並行可(触るファイルが分離)。ただし双方 `api/schemas.py` を編集するため schema 追加は 1 タスクにまとめるか順序化(T007 と T015 は同一ファイル → 直列化推奨)。

---

## Implementation Strategy

### MVP スコープ

- **最小 MVP = US1 + US2**(backend): モデルに用途が付き、API がモデル指定で切替可能。UI 無しでも API/CLI で価値と検証が成立。
- **完成増分 = + US3**: front でオペレータが実レースを見ながら切替。

### Incremental Delivery

1. Setup + Foundational(migration/ORM)→ スキーマ準備。
2. US1(用途ラベル)→ /models・admin で用途可視。
3. US2(任意モデル予測)→ API 切替 + available_models。
4. T018 で契約確定(front/admin 型再生成・drift 緑)。
5. US3(front セレクタ)→ 画面で切替。
6. Polish(E2E・lint・全緑・記録)。

---

## Notes

- codex unavailable(環境未インストール)→ 実装中の非自明判断は plan.md のセルフレビュー checklist を随時参照。復旧したら T017/T023 の後方互換・404・read-only 周りに second opinion を取ると効果的。
- 触るファイルの衝突注意: `api/schemas.py`(T007/T015)と `openapi.json`×2(T018)は直列。
- リーク境界・FEATURE_VERSION は不変(特徴量非関与)。leak-guard テストは変更不要。
- 各タスク/論理単位でコミット。チェックポイントで story を独立検証。
