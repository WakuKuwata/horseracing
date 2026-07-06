# Implementation Plan: 複数モデル切り替え基盤(用途ラベル + レース詳細でのモデル切替)

**Branch**: `057-model-switching` | **Date**: 2026-07-06 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/057-model-switching/spec.md`

## Summary

予測 API を「採用モデル固定」から「モデル選択可能(既定=採用モデル)」へ拡張し、各モデルに用途ラベル(`display_name` / `purpose`)を持たせ、front レース詳細でモデルを切り替えられるようにする。予測は既に (レース×モデル) 単位で永続化済みのため、**新規予測ロジック・ライブ計算・特徴量変更は一切なく**、(a) model_versions への用途メタ列追加(migration 0011)、(b) 予測 run 選択の model_version 化 + 応答への `available_models` 純追加、(c) front のモデルセレクタ、の 3 層の薄い結線。将来の B1(過去走オッズ履歴特徴の精度最優先モデル)を意思決定支援モデルと別用途で共存させる土台。

**設計の核心**: 予測応答に `available_models`(このレースに永続化済み run を持つモデルの一覧 + 表示情報 + どれが active/選択中か)を 1 フィールド純追加する。これで front は 1 リクエストでセレクタを描画でき、404 スパム(run の無いモデルを選ばせる)を避けられる。`?model_version=` は defensive に残し、run 不在は typed 404。

## Technical Context

**Language/Version**: Python 3.12(api/db/serving)、TypeScript + React + Vite(front/admin)

**Primary Dependencies**: FastAPI + pydantic(api、read-only)、SQLAlchemy 2.0 + Alembic(db)、openapi-typescript + openapi-fetch + TanStack Query + MSW/Vitest(front/admin)

**Storage**: PostgreSQL 16。`model_versions`(PK=`model_version`)に nullable 2 列追加。`prediction_runs` は (race_id, model_version) で既に複数 run 共存。

**Testing**: pytest + testcontainers(api/db)、Vitest + RTL + MSW(front/admin)、openapi drift-check(front/admin byte 一致)

**Target Platform**: ローカル単一オペレータ(localhost)。認証スコープ外。

**Project Type**: 既存 web(api backend + front SPA + admin SPA)への純追加。

**Performance Goals**: 予測取得は 1 レース O(定数)クエリ。`available_models` は 1 追加クエリ(distinct model_version among runs join model_versions)。ライブ計算なし。

**Constraints**: API read-only(全 path GET)不変・OpenAPI 純追加・FEATURE_VERSION 不変・リーク境界不変・後方互換(model 未指定=現行と同一 run 選択・同一馬確率)。

**Scale/Scope**: 影響パッケージ = db(migration + model)、api(selection/router/schema/queries)、front(queries/セレクタ/レース詳細)、admin(レジストリに用途列表示)、training/CLI(用途メタ書込コマンド)。

## Constitution Check

*GATE: Phase 0 前に PASS 必須。Phase 1 後に再確認。*

- [x] **I. データ契約**: `raceId` 12桁契約は既存 router 正規表現を踏襲・不変。model_version(技術ID)は不変(リネーム禁止=FR-001)。ラベル体系 `label_schema` は別概念で不変、追加は表示用 `display_name`/`purpose`。**PASS**
- [x] **II. リーク防止 (NON-NEGOTIABLE)**: 本 feature は**特徴量に一切触れない**(表示/serving 選択の配管のみ)。市場オッズを特徴化しない。`display_name`/`purpose` はモデル素性で特徴量に流入しない。B1(過去走オッズ特徴)は憲法 II に従い**別 spec**で利用可能タイミング/評価を定義してから。FEATURE_VERSION 不変。**PASS(該当変更なし)**
- [x] **III. 評価先行 (NON-NEGOTIABLE)**: 本 feature は model/feature を変更しない(予測ロジック不変・p バイト不変)ため walk-forward 評価の対象外。採用ゲート・ECE には触れない。FR-009 で「eval 合格 ≠ 自動採用」を明文化し III の採用規律を弱めない。**N/A(モデル変更なし)**
- [x] **IV. 確率整合性**: 予測値は既存 run の永続値をそのまま読むだけ。正規化・除外・Unknown 扱いは既存経路不変。**PASS(不変)**
- [x] **V. 再現性・監査**: 応答の監査エンベロープ(model_version/logic_version/computed_at)は既存どおり。モデル選択時も「どの run を見ているか」を返す。用途メタは静的メタ(監査対象外)。**PASS**
- [x] **VI. feature 分割規律**: UI(front セレクタ)より先に API/DB 契約(migration 0011 + 応答 `available_models` + `?model_version=`)を本 plan / contracts で確定。予測系テーブルは既存契約に純追加のみ。**PASS**
- [~] **品質ゲート(codex second opinion)**: 横断 refactor + migration + API 契約変更のため本来 MUST-codex。**codex unavailable**(環境未インストール・本セッション 2 回起動失敗)→ single-opinion + 下記セルフレビュー checklist を記録(CLAUDE.md「codex 使えない場合はセルフレビュー」規定)。**代替実施・記録済**

### セルフレビュー checklist(codex 代替)

| 観点 | リスク | 対応 |
|---|---|---|
| 後方互換 | model 未指定応答が変わる | run 選択・馬確率は完全不変。`available_models` は**追加フィールド**(既存フィールド不変)。既存の個別フィールド assert は緑、応答全体を厳密比較する型テストのみ新フィールド追記。 |
| 暗黙フォールバック | 選択モデルの run が無いとき active を返し誤認 | typed 404(`prediction_unavailable`)を返す(FR-005)。front は `available_models` からしか選ばせないので通常不発だが defensive に実装。 |
| migration head 波及 | head assert テスト(features/live 等、0008/0009 前例)が 0010→0011 で赤 | head assert を持つテストを洗い出し 0011 へ更新(tasks に明示)。 |
| read-only 侵犯 | 用途メタ書込を API に足すと read-only 破壊 | 書込は **CLI**(training/registry 側)に置き API は読むだけ。全 path GET 不変テスト維持。 |
| 命名衝突 | 新列 `label` が既存 `label_schema` と紛らわしい | `display_name` / `purpose` を採用。 |
| active 概念の混線 | eval 合格モデルを自動 active 化 | adoption ロジックは一切変更しない。`display_name`/`purpose` は adoption_status と独立(FR-009)。 |
| OpenAPI drift | front/admin snapshot 不一致で契約フォーク | 生成 → front/admin 両 openapi.json + schema.d.ts 再生成 → drift-check(byte 一致)緑を維持。 |
| 空 available_models | run が 1 つも無いレース | 空配列 + typed-empty(既存の予測未生成表示)。セレクタは非表示 or 無効。 |

## Project Structure

### Documentation (this feature)

```text
specs/057-model-switching/
├── plan.md              # This file
├── research.md          # Phase 0: 非自明設計判断の集約
├── data-model.md        # Phase 1: model_versions 追加列 + 応答 available_models 契約
├── quickstart.md        # Phase 1: 実 DB E2E 検証手順
├── contracts/
│   └── api.md           # 予測エンドポイント拡張 + /models 拡張の契約
└── tasks.md             # /speckit-tasks で生成(本コマンド対象外)
```

### Source Code (repository root)

```text
db/
├── migrations/versions/0011_model_purpose.py    # 新: display_name/purpose nullable 列
└── src/horseracing_db/models/prediction.py       # ModelVersion に 2 列追加

api/src/horseracing_api/
├── selection.py            # select_prediction_run に model_version 任意引数
├── routers/predictions.py  # ?model_version= + typed 404 + available_models 組立
├── routers/models.py       # _row に display_name/purpose 透過
├── queries.py              # available_models 用クエリ + list_model_versions 透過
└── schemas.py              # PredictionResponse に available_models、ModelVersionRow に用途列

front/src/
├── api/queries.ts          # usePredictions に modelVersion 引数、useModels(任意)
├── api/schema.d.ts         # 再生成(openapi 純追加)
├── openapi.json            # 再生成(admin と byte 一致)
├── components/ModelSelector.tsx  # 新: モデル選択 UI(採用バッジ・未生成状態)
└── pages/RaceDetailPage.tsx      # セレクタ結線・選択 state・?model_version= 再取得

admin/src/
├── api/schema.d.ts / openapi.json           # 再生成(front と byte 一致)
└── pages/ModelRegistryPage.tsx, ModelDetailPage.tsx  # 用途列表示

training/ (or db CLI)
└── registry CLI: set-model-label --model-version --display-name --purpose  # 用途メタ書込(read-write, API 非経由)
```

**Structure Decision**: 既存 web 構成(api/front/admin/db)への純追加。新規パッケージなし。書込は既存 CLI 層に 1 コマンド追加(API は read-only 維持)。

## 非自明な設計判断(research.md に詳細)

1. **応答に `available_models` を純追加**(vs 別 GET エンドポイント / 全モデル羅列): 1 リクエストでセレクタ描画・404 回避・run を持つモデルだけ提示。read-only・純追加で契約フォークなし。
2. **用途メタは列 `display_name`/`purpose`**(vs metrics_summary JSONB): 用途はメトリクスでなくモデル素性。021 規律(metrics_summary=eval 転記)を汚さない。migration 0011 は 0008-0010 と同型の軽微追加。
3. **書込は CLI**(vs admin 書込アクション): API/admin の read-only 思想維持。用途設定は低頻度のオペレータ操作で CLI が自然。admin 書込は 053 の ops 経由が必要で過剰。
4. **typed 404 + no fallback**: 見ているモデルの誤認防止(FR-005)。`available_models` で通常回避しつつ defensive に残す。

## Complexity Tracking

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| migration 0011(スキーマ変更) | モデルの用途は第一級の素性で、レジストリ/セレクタが機械可読に参照する。技術 ID と分離必須(FR-001) | metrics_summary JSONB 流用は 021 の「metrics_summary=eval 転記のみ」規律を汚し、用途とメトリクスを混線させる。0008-0010 と同型の nullable 追加で憲法 VI 正当化済 |
| CLI 書込コマンド追加 | 用途メタを設定する手段が必要 | API に書込を足すと 014 read-only 不変を破る。admin ops 経由(053)は低頻度メタ設定に過剰 |
