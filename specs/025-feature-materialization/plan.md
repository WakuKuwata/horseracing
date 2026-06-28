# Implementation Plan: 特徴量 materialization 基盤 (Feature Materialization)

**Branch**: `025-feature-materialization` | **Date**: 2026-06-28 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/025-feature-materialization/spec.md`

## Summary

重い as-of/過去由来特徴（history・020 recent_form/aptitude/class_transition・020 human_form・023 pace）を生成 CLI で 1 回 materialize → `artifacts/features.parquet` + manifest（source fingerprint 込み）。`build_feature_matrix` は **opt-in** で parquet を read+merge し、static/current-race 特徴は従来計算。**生成・fallback・パリティ比較は既存ブロック関数（単一実装）を共有**。staleness は **fail-closed**（fingerprint 不一致/未カバーでエラー、未来レースのみ fallback 計算）。出力は現行 in-memory と **bit 一致**（FEATURE_VERSION 据え置き＝採用済みモデル不変）。スキーマ変更なし。

## Technical Context

**Language/Version**: Python 3.12（features/training/eval/serving）

**Primary Dependencies**: pandas / pyarrow（parquet, 既存 `to_parquet` 実績あり）、hashlib（fingerprint）。新規重依存なし。

**Storage**: PostgreSQL 16（read-only）。**新規テーブルなし**。materialize 先 = `artifacts/features.parquet` + `artifacts/features.manifest.json`（.gitignore 済み、非コミット）。

**Testing**: pytest + testcontainers（features）。パリティ（materialize==in-memory, check_exact）・決定論・fingerprint staleness fail-closed・generator==fallback 契約・materialize 後 leak 不変・カバレッジ・no-schema。

**Target Platform**: バッチ CLI（生成）+ 透過 read（training/eval/serving）。

**Project Type**: ML ライブラリ infra（features）。UI/DB スキーマ変更なし。

**Performance Goals**: 生成 1 回（現行 build 1 回相当の重さ）。eval/ablation 反復は parquet read（全ロード+as-of 再計算を回避）。性能予算は quickstart で実測。

**Constraints**: パリティ非交渉（bit 一致）・リーク境界保持・fail-closed staleness・単一実装・スキーマ変更なし。

**Scale/Scope**: 2007–2024（62k races/883k entries）。materialize 列 = 全 as-of group（約 30 列）。

## Constitution Check

- [x] **I. データ契約**: raceId/2007+/ID は既存契約。materialize は計算結果のキャッシュで契約不変。**PASS**
- [x] **II. リーク防止 (NON-NEGOTIABLE)**: 生成は既存ブロック関数の strict-before/同日除外/跨馬 TE 機構をそのまま使用（新実装しない）。per-row as-of は pool-end 非依存。materialize 後に target/同日/未来の結果を変えても特徴不変（leak test）。既存行変更/backfill は fingerprint で無効化。odds/結果は特徴にしない。**PASS**
- [x] **III. 評価先行 (NON-NEGOTIABLE)**: 本 feature は出力不変（パリティ）＝新規採用判定は不要。むしろ「採用済みモデルの予測が変わらない」ことをパリティ/予測一致テストで保証。**PASS**
- [x] **IV. 確率整合性**: 計算経路のみ変更、win→joint(009) 不変、出力同一。**PASS**
- [x] **V. 再現性・監査**: manifest（データ範囲・行数・FEATURE_VERSION・決定論ハッシュ・生成時刻・**source fingerprint**）で再現性/staleness 検知。parquet は非コミットだが DB から決定論再生成可能。**PASS**
- [x] **VI. feature 分割規律**: DB スキーマ変更なし（parquet は artifacts）。read 経路は opt-in で段階有効化。infra と signal(026 血統) を分離。**PASS**
- [x] **品質ゲート**: infra spec として codex second opinion 実施済（spec「codex レビュー所見」、research R1–R5）。**PASS**

スキーマ変更なし・違反なし → Complexity Tracking 不要。

## 主要設計判断（codex second opinion 反映）

1. **単一実装（P0/FR-017）**: materialize 生成も serving fallback も builder の read 経路も、**既存ブロック関数 `build_history_features`/`build_extra_features`/`build_human_form_features`/`build_pace_features` を唯一の as-of 計算源**として呼ぶ。materialize 対象列は `registry.FEATURE_GROUPS`＋history 由来列から**機械的に導出**（static/current-race を除外）。「generator 出力 == fallback 出力」契約テスト。
2. **パリティ bit 一致（P0/FR-007）**: `assert_frame_equal(check_exact=True, check_dtype=True)` を ALL_COLUMNS 列順込みで。parquet スキーマ契約（列順・明示 dtype・null≠0・(race_id,horse_id) ソート）。round-trip に null/同日/pace/static を含む。FEATURE_VERSION 据え置き。
3. **staleness fail-closed + source fingerprint（P0/FR-003/009）**: manifest に入力 races/race_horses/race_results の射影カラムのハッシュ（fingerprint）を保存。build 時に「要求キー網羅 + fingerprint 一致 + version 一致」を検査し、不一致は **fail-closed**。fallback は parquet カバー外の**未来レースのみ**＋ audit warning。
4. **末尾非依存 + backfill 無効化（P0/FR-018）**: materialize 対象は cutoff 不変（pool-end 非依存）をテストで確認した特徴のみ。既存行の変更/backfill は fingerprint 変化で parquet を無効化。
5. **段階有効化（P1/FR-019）**: read 経路は既定 off（opt-in フラグ/列 group リスト）。parity/leak 全合格まで本番デフォルトにしない。026 血統も本基盤の形で materialize。

## Project Structure

### Documentation (this feature)

```text
specs/025-feature-materialization/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── materialization.md   # parquet スキーマ契約 + manifest + CLI + read API
└── tasks.md                 # /speckit-tasks で生成
```

### Source Code (repository root)

```text
features/src/horseracing_features/
├── materialize.py   # NEW: build_asof_features(frames)=既存ブロック関数を呼ぶ単一実装 /
│                    #      source_fingerprint(frames) / write(parquet+manifest) /
│                    #      read+coverage(fail-closed)
├── builder.py       # assemble_feature_matrix に opt-in read 経路（materialized 優先・static は計算・
│                    #      未カバー新規レースは block 関数で fallback）
├── registry.py      # materialize 対象列 = group メタから機械導出（static 除外）するヘルパ
└── cli.py           # `materialize`(生成: parquet+manifest) サブコマンド追加

eval/training/serving: build_feature_matrix 経由で透過。呼び出し側変更は最小（opt-in フラグ）。

tests:
features/tests/ : パリティ(bit)・決定論・fingerprint staleness fail-closed・
                  generator==fallback 契約・materialize 後 leak 不変・カバレッジ・no-schema
```

**Structure Decision**: features 内に materialize モジュールを新設し、**既存ブロック関数を単一の as-of 計算源**として再利用（二重実装を作らない）。builder は read 経路を opt-in で追加。DB スキーマ・他パッケージの呼び出しは不変（透過）。

## Complexity Tracking

> 憲法違反なし・スキーマ変更なしのため記載不要。
