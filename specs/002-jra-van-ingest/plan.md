# Implementation Plan: JRA-VAN 過去データ取込 (2007+)

**Branch**: `002-jra-van-ingest` | **Date**: 2026-06-21 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/002-jra-van-ingest/spec.md`

## Summary

ローカルの JRA-VAN 年別 CSV (Shift_JIS, 73列固定) をストリーミング解析し、Feature 001 のコア
テーブルへ冪等 upsert する取込パイプラインと CLI を、新パッケージ `ingest/`
(`horseracing-ingest`、`horseracing-db` に依存) として実装する。列レイアウトは Phase 0 の調査で
ほぼ確定済み (research.md に 73列マップ)。状態正規化 (取消/除外/中止/失格/同着) は学習ラベルを
汚染しないことが最重要で、finished / DNF / DNS の 3 区分を golden fixture でロックする。コアテーブルの
スキーマは変更しないが、監査 (憲法 V) のため `ingestion_jobs` に件数列を非破壊追加する (migration 0004)。

## Technical Context

**Language/Version**: Python 3.12 (db パッケージと同一)

**Primary Dependencies**: `horseracing-db` (パス依存)、SQLAlchemy 2.0 (upsert)、psycopg 3。パースは
標準ライブラリ `csv` (Shift_JIS ストリーミング、pandas 不使用)。CLI は標準ライブラリ `argparse`。

**Storage**: PostgreSQL 16 (Feature 001 のコアテーブル + `ingestion_jobs`)

**Testing**: pytest + testcontainers[postgres] (取込→DB の統合)、parser/mapping/raceId/status は
golden fixture によるユニット

**Target Platform**: Linux / macOS のオペレーター実行 (手動 CLI)

**Project Type**: 単一の取込パッケージ (`horseracing-ingest`)

**Performance Goals**: 1年 ~49k 行、全19年 ~93万行。ストリーミング + バッチ upsert で 1 年を実用
時間 (数分以内) で取込む。メモリは年ファイル全読みしない (行ストリーム)。

**Constraints**: Shift_JIS デコード必須、73列固定、冪等、2007 境界は `validation.is_in_ingest_scope`
が唯一の正本 (独自日付比較なし)、スキーマ変更なし。

**Scale/Scope**: race_horses / race_results 各 ~93万行規模。horses は累計 (重複 upsert で 1 行)。

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- [x] **I. データ契約**: `race_id` を year+venue_code+kai+nichime+race_no から導出し `^[0-9]{12}$` を
  保証 (CHECK で拒否される行はエラー記録)。`is_in_ingest_scope` で 2007 境界。JRA-VAN を canonical
  source とし血統登録番号/騎手コード/調教師コードを各 id に採用。`id_mappings` は本 feature で作らない
  (netkeiba 合流まで deferred)。**PASS**
- [x] **II. リーク防止 (NON-NEGOTIABLE)**: 発走前情報 (race_horses) と結果 (race_results) を行内で
  分離格納。JRA-VAN のオッズ/人気は「結果確定時」値であり provenance を data-model/research に明記
  (発走前特徴量に使用不可、強制は特徴量 feature)。本 feature は特徴量を作らない。**PASS**
- [x] **III. 評価先行 (NON-NEGOTIABLE)**: 取込は評価ハーネスより前段のデータ整備。`labels.derive_labels`
  が finished のみを返す前提を、状態正規化の正しさで担保する。学習は含まない。**N/A / 基盤提供 PASS**
- [x] **IV. 確率整合性**: 本 feature は確率を作らない。ただし取消・除外を非出走として正しく除外し、
  同着を finish_order 共有で表現することで、下流の確率/ラベル整合の前提を守る。**PASS (前提担保)**
- [x] **V. 再現性・監査**: 取込ジョブを `ingestion_jobs` に source/年/状態/処理行数/checkpoint/エラーで
  記録。冪等再取込。不正行は黙って捨てず記録 (FR-017)。**PASS**
- [x] **VI. feature 分割規律**: コアテーブルのスキーマ変更なし。監査のため `ingestion_jobs` に件数列を
  **非破壊追加** (migration 0004、憲法 VI の非破壊拡張)。P0 未決 (列の一部・状態の 4分類) は研究課題と
  して golden fixture でロックし、未知値はエラー化。**PASS**
- [x] **品質ゲート**: `codex:codex-rescue` の second opinion を spec 段階で取得済み (下記記録)。本 plan は
  その設計を実行するもので、新たな非自明分岐なし。**PASS**

### Second Opinion 記録 (codex:codex-rescue — spec 段階で取得)

| 論点 | codex 助言 | 本 plan の対応 |
|---|---|---|
| スコープ境界 | パーサ+upsert+監査が範囲、73列確定は research の必須完了条件 | 採用。research.md に 73列マップを置き、確定不能列はスキーマ外で読み飛ばし |
| パース戦略 | `csv` で SJIS ストリーミング、pandas 回避、行番号付きエラー | 採用 (Technical Context) |
| 状態正規化 (最大リスク) | JRA-VAN 値→enums の明示マップ。取消/除外=結果行なし、中止/失格=結果行ありラベル除外。誤分類はラベル汚染 | 採用。Phase 0 で finish_order=0 を確認、DNS/DNF をデータ存在で分離。4分類は golden fixture でロック、未知はエラー (FR-012) |
| ID 導出/FK | 18→12 桁規則をテスト固定、venue 完全表、upsert 順序 | 採用。raceId は列由来で導出 (18桁は cross-check)、venue は標準10コース表 |
| 冪等・監査 | PK upsert、年単位 ingestion_jobs + checkpoint 行番号 | 採用 |
| リーク防止 | JRA-VAN オッズ=結果確定時、特徴量利用禁止を明記 | 採用 (FR-018 ドキュメント記載のみ) |
| MVP | 2007 1ファイルを end-to-end | 採用 (US1) |
| 横断リスク | golden fixture、SJIS破損、列数不正、同着/取消/中止、docs/database.md は古く data-model/ORM を正に | 採用。テスト化、確定仕様は 001 の data-model/ORM |

最大リスク TOP3 (codex): ①列レイアウト誤マップ ②状態誤分類によるラベル汚染 ③結果確定時オッズの
リーク。①②は Phase 0 で大幅に解消 (research.md)、残りは golden fixture でロック。③は FR-018 で対応。

### Second Opinion 記録 (codex:codex-rescue — tasks/analyze 後の I1)

analyze が `ingestion_jobs` に件数列が無いのに spec/SC-006 が件数記録を要求する矛盾 (I1) を検出。
codex に解決方針を相談:

| 選択肢 | 内容 | 判定 |
|---|---|---|
| A スキーマ変更なし rescope | 件数は core テーブルを年集計で確認 | **不採用** — 再取込後にジョブ時点の件数を復元できず、取消/除外/skip/不正行は core に残らない。憲法 V (監査) を満たせない |
| B 非破壊拡張 | `ingestion_jobs` に件数列を追加 | **採用** — 憲法 VI の非破壊拡張。ジョブ時点の件数を後から再現でき、netkeiba 取込でも共通利用 |

codex 推奨の列構成を採用: `processed_rows`/`skipped_rows`/`error_count` (固定カウンタ、検索・テスト・
共通監査に強い) + `summary jsonb` (テーブル別件数でソース差分を吸収)。migration 0004 を db/ に追加。

## Project Structure

### Documentation (this feature)

```text
specs/002-jra-van-ingest/
├── plan.md
├── research.md          # 73列マップ・venue表・状態判定・raceId導出規則
├── data-model.md        # 列→コアテーブル列のマッピング、状態正規化規則
├── quickstart.md        # CLI 実行 + テスト手順
├── contracts/
│   ├── cli.md           # 取込 CLI の契約 (コマンド・引数・終了コード)
│   └── parsing.md       # パーサ/マッパの関数契約と不変条件
└── tasks.md             # /speckit-tasks
```

### Source Code (repository root)

```text
ingest/                                  # 新パッケージ horseracing-ingest
├── pyproject.toml                       # horseracing-db (path) + sqlalchemy + psycopg
├── src/horseracing_ingest/
│   ├── __init__.py
│   ├── layout.py                        # 73列の index 定数、venue_code 表、状態元値マップ
│   ├── parser.py                        # SJIS ストリーミング、73列レコード -> 行 dataclass
│   ├── mapping.py                       # raceId 導出、venue 変換、状態正規化、欠損処理
│   ├── upsert.py                        # FK 順 upsert (races/horses/jockeys/trainers→race_horses→race_results)
│   ├── pipeline.py                      # file -> parse -> map -> upsert -> ingestion_jobs 監査/checkpoint
│   └── cli.py                           # argparse: ingest-year / ingest-all
└── tests/
    ├── fixtures/                        # 小さな golden CSV (SJIS, 2006/2007、取消/除外/中止/失格/同着)
    ├── unit/                            # parser, mapping, raceId, venue, status
    └── integration/                     # 取込→testcontainers PG: 件数, 冪等, skip<2007, resume
```

**Structure Decision**: 取込は application ロジックなので、純粋なデータ契約パッケージ `db/` とは分離し、
新パッケージ `ingest/` (`horseracing-ingest`) を作り `horseracing-db` にパス依存させる。憲法の「初期は
スクリプト/CLI でよい」に沿い、サービス化はしない。

加えて、監査件数のため `db/` に **migration 0004 (`0004_ingestion_job_counts.py`)** を追加し、
`ingestion_jobs` に nullable な `processed_rows`/`skipped_rows`/`error_count`/`summary(jsonb)` を足し、
`JobStatus` に `skipped` (<2007 ファイル記録用) を非破壊追加する (CHECK 差し替え + enum、IngestionJob
モデルも更新)。コアテーブルのスキーマは不変。

## Complexity Tracking

> Constitution Check に違反なし。記入不要。
