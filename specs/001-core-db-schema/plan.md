# Implementation Plan: Core DB スキーマと基盤テーブル契約

**Branch**: `001-core-db-schema` | **Date**: 2026-06-21 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/001-core-db-schema/spec.md`

## Summary

このプロジェクトの全データが乗る初期スキーマ、制約、マイグレーション、データ層不変条件の検証を
提供する。`aiuma` (PostgreSQL + Alembic + SQLAlchemy) を参考実装としつつ、憲法の「初期から独立
サービス分割を強制しない」に従い、3 サーバー分割は採らず**単一の共有 `db/` パッケージ** (将来の
api / training サーバーが依存する) に集約する。コア 6 テーブル + ingestion / id 対応 + 予測・推奨の
最小契約を、ストーリー単位の 3 つの Alembic マイグレーション (`0001` core / `0002` ingestion・id /
`0003` prediction) で構築し、CHECK 制約・トリガ・再利用可能なバリデータ・testcontainers ベースの
制約テストで検証する。

## Technical Context

**Language/Version**: Python 3.12 (参考: aiuma は 3.11。3.11 でも可)

**Primary Dependencies**: SQLAlchemy 2.0 (Declarative, typed models), Alembic (手書きマイグレーション),
psycopg 3 (driver)

**Storage**: PostgreSQL 16 (`now()`, `Uuid`, `ARRAY`, `Interval`, `Numeric`, 正規表現 CHECK `~`,
トリガによる `updated_at` 自動更新を使用)

**Testing**: pytest + testcontainers[postgres] (CHECK / トリガ / マイグレーションを実 Postgres で
検証)、ユニットはバリデータ単体

**Target Platform**: Linux / macOS 開発環境、コンテナ化 PostgreSQL

**Project Type**: 単一プロジェクト (共有データパッケージ `horseracing-db`)

**Performance Goals**: 機能性能要件なし。マイグレーション適用は数秒以内。`race_date` 索引で時系列
分割クエリを実用速度に保つ。

**Constraints**: スキーマに 2007 境界のハードな日付制約を入れない (取込ポリシーとして別レイヤで
強制)。オッズ履歴は保存しない (最新値上書き)。状態は `text + CHECK` で表現 (Postgres ENUM 不使用)。

**Scale/Scope**: 2007 以降の JRA-VAN ≈ races 数万行、race_horses / race_results 各 ~100 万行規模
(18 年 × 年 ~3,400 レース × 平均 ~14 頭)。大規模ではないが `race_date` 索引は必須。

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

Constitution v1.0.0 に基づくゲート (初回評価 / Phase 1 後に再評価):

- [x] **I. データ契約**: `race_id` に `^[0-9]{12}$` CHECK、`race_number` 1–12 CHECK を migration で
  強制 (aiuma 0002 踏襲)。横断 ID は `id_mappings` 経由のみ (FK で推測結合を作らない)。ラベル物理名は
  英語可だが論理名は `1着率/2着以内率/3着以内率` を data-model に明記。**PASS**
- [x] **II. リーク防止 (NON-NEGOTIABLE)**: 本 feature はスキーマのみで特徴量を作らないため直接の
  リークは発生しない。`race_date` 保持と「対象レースより前のみ集計」を可能にする索引を提供。結果系
  (race_results) と発走前系 (race_horses) をテーブル分離し、混同を構造的に防ぐ。**PASS (該当範囲で)**
- [x] **III. 評価先行 (NON-NEGOTIABLE)**: 本 feature は評価ハーネスより前段のスキーマ整備。評価が
  依存する walk-forward の基盤 (race_date、ラベル導出可能な race_results) を提供する。学習ロジックは
  含めない。**N/A (スキーマ段階) / 基盤提供は PASS**
- [x] **IV. 確率整合性**: `race_predictions` に `0 <= win <= top2 <= top3 <= 1` の行 CHECK を入れる。
  レース内合計 (≈1/2/3) は行制約にできないため検証クエリ / 下流責務とする (data-model に明記)。
  取消・除外は `entry_status` で表現し再正規化対象を識別可能にする。**PASS**
- [x] **V. 再現性・監査**: `recommendations` に使用市場オッズ / 推定市場オッズ / 疑似オッズ / 疑似ROI /
  計算時刻 / ロジック版 / モデル版を保持。推定オッズは実オッズと別列 + フラグで区別。全テーブルに
  `created_at` / `updated_at` を持たせ、`updated_at` は DB トリガで自動更新 (書き手非依存)。**PASS**
- [x] **VI. feature 分割規律**: UI は含まない。P0 未決 (結合確率・推定オッズ変換) は最小列のみ定義し
  非破壊拡張可能にする。予測・推奨系テーブルの最小契約を本 feature で確定。**PASS**
- [x] **品質ゲート**: `codex:codex-rescue` で second opinion を取得済み。両案差分と採用根拠を下記
  「Second Opinion 記録」に記載。**PASS**

### Second Opinion 記録 (codex:codex-rescue)

| 論点 | Claude 案 | codex 案 | 採用 | 根拠 |
|---|---|---|---|---|
| ディレクトリ構成 | 単一 `db/` 共有パッケージ | 単一 `backend/db` + Alembic (3分割しない) | **単一 `db/` (top-level)** | 両者一致 (分割回避)。共有データ契約は api/training 双方が依存するため backend 配下より top-level が中立。憲法「初期分割を強制しない」 |
| 状態表現 | `entry_status`/`result_status` (text+CHECK) | 同左 (bool フラグより優位) | **text+CHECK 状態列** | 両者一致。取消≠除外、中止≠失格の意味差を保持。ラベル導出で完走前提集計から除外対象が明確 |
| ENUM vs text+CHECK | text+CHECK | text+CHECK (ENUM は Alembic で重い) | **text+CHECK** | 両者一致。値追加は CHECK 差替え migration で運用 |
| id_mappings | source/source_id/entity_type/canonical_id | + `mapping_status`(unmapped/mapped/conflict/rejected)、canonical_id nullable、conflict_group_id | **codex 案を採用** | 未対応・衝突・保留を unique 制約だけでは表現できない。憲法 I の「推測結合禁止」を状態で担保 |
| 予測・推奨契約 | 3 確率列 + 監査列 | + recommendations 最小キー (prediction_run_id, race_id, bet_type, selection_json, 監査列) を固定 | **codex 案を採用** | 後付け破壊変更を避けつつ券種詳細だけ deferred、の線引きが明確 |
| updated_at 自動更新 | (未定) | DB トリガ vs app を plan で固定すべき | **DB トリガ** | 監査列の信頼性を書き手非依存にする (憲法 V) |
| 確率 CHECK 配置 | (未定) | 行 CHECK で win<=top2<=top3 を強制すべき | **行 CHECK 採用** | 憲法 IV を DB で強制。レース内合計は検証クエリ |
| docs 参照欠落 | (未認識) | repo に `docs/database.md`/`docs/open-decisions.md` が無い (Vault のみ) | **repo に `docs/` 同期を follow-up 化** | spec が source-of-truth として参照する以上、repo 内に置くべき |

不採用・保留: なし (codex 指摘はすべて採用)。

## Project Structure

### Documentation (this feature)

```text
specs/001-core-db-schema/
├── plan.md              # This file
├── research.md          # Phase 0 output
├── data-model.md        # Phase 1 output (全テーブル定義・制約・不変条件)
├── quickstart.md        # Phase 1 output (migration + テスト実行手順)
├── contracts/           # Phase 1 output
│   ├── tables.md        # 下流が依存する凍結テーブル契約
│   └── validation.md    # 再利用バリデータ (race_id 形式, 2007 境界) のシグネチャ契約
└── tasks.md             # Phase 2 output (/speckit-tasks で生成)
```

### Source Code (repository root)

```text
db/                                  # 共有データパッケージ horseracing-db
├── pyproject.toml
├── alembic.ini
├── src/horseracing_db/
│   ├── __init__.py
│   ├── base.py                      # DeclarativeBase + naming convention
│   ├── enums.py                     # 状態コード定数 (entry/result/mapping/job status, source, bet_type)
│   ├── constraints.py               # CHECK 式・制約名の一元定義
│   ├── session.py                   # engine / session factory
│   ├── validation.py                # 再利用バリデータ (race_id 形式, 2007 境界判定)
│   ├── labels.py                    # status-aware ラベル導出参照クエリ/ヘルパ
│   ├── sql/
│   │   └── triggers.py              # set_updated_at() + BEFORE UPDATE トリガ DDL ヘルパ
│   └── models/
│       ├── __init__.py
│       ├── core.py                  # races, horses, jockeys, trainers, race_horses, race_results
│       ├── ingestion.py             # id_mappings, ingestion_jobs
│       └── prediction.py            # model_versions, prediction_runs, race_predictions,
│                                    #   feature_snapshots, recommendations
├── migrations/
│   ├── env.py
│   └── versions/
│       ├── 0001_core_schema.py          # US1/US2: コア6テーブル + CHECK + 索引 + トリガ
│       ├── 0002_ingestion_id_schema.py  # US3: id_mappings, ingestion_jobs
│       └── 0003_prediction_contract.py  # US4: 予測・推奨の最小契約
└── tests/
    ├── conftest.py                  # testcontainers postgres fixture
    ├── integration/                 # 制約・トリガ・マイグレーション (実 Postgres)
    └── unit/                        # validation.py 単体

docs/                                # follow-up: Vault からプロジェクト docs を同期 (spec の参照先)
```

**Structure Decision**: 単一の top-level 共有パッケージ `db/` (`horseracing-db`) を採用。aiuma の
`backend/{api,training,workflow}-server` 分割は初期スコープでは過剰であり、憲法に反するため見送る。
DB は将来の全サービスが依存する共有契約なので、特定サーバー配下ではなく top-level に置く。将来
API が必要になった時点で `backend/api-server` を追加し、本パッケージへ依存させる。

## Complexity Tracking

> Constitution Check に違反なし。記入不要。
