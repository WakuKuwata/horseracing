# Implementation Plan: 馬・騎手プロフィールページ

**Branch**: `029-horse-jockey-pages` | **Date**: 2026-06-29 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/029-horse-jockey-pages/spec.md`

## Summary

レース詳細の馬名／騎手名をリンク化し、`/horses/{id}`・`/jockeys/{id}` のプロフィールページへ遷移できるようにする。馬ページは識別＋血統（名前）＋通算成績（出走/勝/連対/複勝/平均着順）、騎手ページは騎乗成績を表示し、それぞれレース別履歴を別 paged endpoint で出す。実装は **014 read-only API への additive な read 拡張**（新 GET endpoint 群）＋ **front の新ページ**。成績は `race_horses`＋`race_results` の集約（事実）で算出し、モデル特徴量とは UI/型/経路で分離。**特徴量/parquet 表示は対象外（defer）**、**スキーマ変更なし**、**read-only 不変**を維持。

## Technical Context

**Language/Version**: Python 3.12（api）、TypeScript / React 18（front）

**Primary Dependencies**: FastAPI + pydantic v2 + SQLAlchemy 2.0（api、依存は db + probability のみ＝既存どおり features/training/eval に依存しない）；React + Vite + @tanstack/react-query + openapi-typescript（front）

**Storage**: PostgreSQL 16。**スキーマ変更なし**（既存 `horses`/`jockeys`/`race_horses`/`race_results` を read 集約）。新インデックスも追加しない（read 性能はページング上限で抑える）。

**Testing**: pytest + testcontainers（api 集約クエリ・契約・read-only 不変）、Vitest + RTL + MSW（front ページ・リンク化・状態遷移）、openapi drift-check。

**Target Platform**: 既存 014 API（uvicorn）＋ 015 front（Vite）。新サービスなし。

**Project Type**: web（既存 `api/` への read endpoint 追加 ＋ `front/` への画面追加）。

**Performance Goals**: プロフィール表示は単発集約クエリ（N+1 なし）。履歴はページング（`page_size` 上限）で件数を抑える。

**Constraints**: read-only（全 GET・書き込み手段を増やさない・app_ro 維持）。api は features/parquet に依存しない（最小依存）。事実集計とモデル特徴量を分離（II）。表示値をモデル特徴に流さない（leak-guard）。

**Scale/Scope**: 1 頭/1 騎手あたり通算履歴は最大数十〜百件規模 → ページングで対応。

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- [x] **I. データ契約**: 馬/騎手は既存識別子（`id_mappings` 経由、surrogate `nk:` を含む）で解決。名前一致の guess-join は作らない。血統は ID 未投入のため名前表示（ID ベース結合は deferred）。→ **PASS**
- [x] **II. リーク防止 (NON-NEGOTIABLE)**: 本 feature はモデル特徴量を作らない。表示する成績は確定実績の集計（事実）で、モデル予測特徴量とは別経路・別型。表示値はモデル特徴に流さない（leak-guard test）。odds/results を特徴量化しない。→ **PASS**
- [x] **III. 評価先行 (NON-NEGOTIABLE)**: モデル/特徴量の変更なし。→ **N/A**
- [x] **IV. 確率整合性**: 確率生成に触れない。実績ゼロは 0 件として明示し Unknown と 0 を区別（FR-014）。→ **PASS（該当部のみ）**
- [x] **V. 再現性・監査**: 予測/推奨の保存はしない（read のみ）。推定/疑似値は表示しない（事実のみ）。→ **PASS（該当なし）**
- [x] **VI. feature 分割規律**: UI 着手前に新 read endpoint の OpenAPI 契約を確定（contracts/）。read（app_ro）経路に write を増やさない。**新テーブルなし**。→ **PASS**
- [x] **品質ゲート**: 設計の非自明点（endpoint 分割・成績集計の母数・契約 additive 変更・UI 分離）について codex second opinion を取得（spec 前のアーキ方針、plan 前の実装論点）、両案差分と採用根拠を [research.md](./research.md) に記録。→ **PASS**

**結論: 全ゲート PASS（III は N/A）。スキーマ変更なしのため Complexity Tracking の正当化記載は不要。**

## Project Structure

### Documentation (this feature)

```text
specs/029-horse-jockey-pages/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── horse-jockey-api.yaml   # 新 read endpoint の OpenAPI 契約
└── tasks.md                    # /speckit-tasks（本コマンドでは未作成）
```

### Source Code (repository root)

```text
api/src/horseracing_api/
├── routers/horses.py     # GET /horses/{id}, GET /horses/{id}/history (paged)
├── routers/jockeys.py    # GET /jockeys/{id}, GET /jockeys/{id}/history (paged)
├── queries.py            # + get_horse / horse_career_stats / horse_history
│                         #   + get_jockey / jockey_career_stats / jockey_history（単発集約、N+1 なし）
├── schemas.py            # + HorseProfile / HorseHistoryRow / JockeyProfile / JockeyHistoryRow
│                         #   + HorseEntry に jockey_id / trainer_id を additive 追加（FR-010）
└── app.py                # include_router(horses, jockeys)

front/src/
├── pages/HorseDetailPage.tsx     # 基本+血統+通算成績+履歴
├── pages/JockeyDetailPage.tsx    # 基本+騎乗成績+履歴
├── components/HorseEntriesTable.tsx  # 馬名/騎手名をリンク化（nk: surrogate は非リンク）
├── api/queries.ts                # useHorseProfile/useHorseHistory/useJockeyProfile/useJockeyHistory
├── api/types.ts                  # 新型の re-export
├── api/schema.d.ts + openapi.json# 014 から再生成（committed snapshot + drift-check）
└── router.tsx                    # /horses/:horseId, /jockeys/:jockeyId
```

**Structure Decision**: 既存 `api/`（014 read-only）に read endpoint を additive 追加し、既存 `front/` SPA にページを足す。新パッケージ・新サービス・新スキーマは無し。成績は事実集計として `predictions`（モデル p/q）とは別の型・hook・コンポーネントに分離する。

## Complexity Tracking

> スキーマ変更なし・憲法ゲート全 PASS のため、正当化を要する違反は無し。

設計上の「単純な代替を退けた」判断は research.md に記録（要約）:

| 判断 | 採用 | 退けた案と理由 |
|------|------|----------------|
| プロフィールと履歴の分割 | profile（識別+血統+集計）と history（paged）を別 endpoint | 1 レスポンスに全履歴同梱は大量履歴で肥大。既存 `Page[T]` パターンでページング |
| 成績の母数 | 勝率/連対率/複勝率＝出走数(started) 基準、平均着順＝完走のみ | 完走基準だと取消で母数が動き直感とズレる。日本競馬慣行に合わせ出走数基準 |
| 特徴量表示 | 本 feature では出さない（事実のみ） | api→features/pandas 結合は read-only/最小依存と衝突。後続 feature へ |
