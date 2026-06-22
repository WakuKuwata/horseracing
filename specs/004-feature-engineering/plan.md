# Implementation Plan: 特徴量生成 (Feature Engineering)

**Branch**: `004-feature-engineering` | **Date**: 2026-06-22 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/004-feature-engineering/spec.md`

## Summary

リーク安全な特徴量生成を新パッケージ `features/` (`horseracing-features`、`horseracing-db` 依存) に実装
する。各 race-horse について、発走前静的特徴量 + 過去成績累積特徴量 (as-of `race_date < R`) + 履歴件数
特徴量 + 欠損/フラグを、固定スキーマの feature matrix として出力する。全特徴量は FeatureRegistry に
source/availability_timing/missing_policy を宣言し、未宣言・結果後混入・結果確定オッズ混入を fail-fast
で検出する。pandas/numpy で集計するが as-of cutoff は機構で固定し、リーク検査テストで担保する。学習・
価値検証 (baseline 超え) は Feature 005 へ委譲。MVP はスキーマ変更なし。

## Technical Context

**Language/Version**: Python 3.12

**Primary Dependencies**: `horseracing-db` (パス依存)、pandas、numpy、SQLAlchemy 2.0 (DB 読取)

**Storage**: PostgreSQL 16。読取のみ (races/race_horses/race_results/horses/jockeys/trainers)。MVP は
スキーマ変更なし (on-the-fly 計算)。

**Testing**: pytest。合成データで特徴量の数値正しさ・**リーク検査**・欠損 (Unknown≠0)・メタデータ強制を
検証。testcontainers で実 DB を使い as-of 計算を統合検証。

**Target Platform**: Linux / macOS のオペレーター/学習パイプライン実行

**Project Type**: 単一の特徴量パッケージ (`horseracing-features`)

**Performance Goals**: 全 race_results を一括ロードして pandas で集計。~100 万 race-horse 行を実用時間で
処理。as-of は per-race クエリではなくベクトル化集計で行う。

**Constraints**: 過去成績は `race_date < R` のみ (同日除外)。結果確定 odds/popularity をモデル特徴量に
使わない。欠損は null (Unknown)、0 と区別。決定論的。2007 境界 (`is_in_ingest_scope`)。

**Scale/Scope**: race-horse ~100 万行、固定スキーマの特徴列 ~30 程度 (MVP)。

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- [x] **I. データ契約**: `race_id`/`race_date` で既存データを読み、2007 境界は `is_in_ingest_scope`。
  ラベルは `labels.derive_labels` を正本。横断 ID 推測結合なし。**PASS**
- [x] **II. リーク防止 (NON-NEGOTIABLE)**: 本 feature の中核。as-of `race_date < R` (同日除外) を機構で
  固定しリーク検査テスト (SC-001)。全特徴量に source/timing/missing を必須宣言 (FeatureRegistry)、
  結果後・結果確定オッズの混入を fail-fast。target encoding は train 境界前のみ (P2)。**PASS**
- [x] **III. 評価先行 (NON-NEGOTIABLE)**: 出力は Feature 003 Predictor が消費。特徴量の価値検証
  (baseline 超え) は学習 (005) の walk-forward 評価へ委譲。本 feature は正しさ・リーク安全まで。**PASS**
- [x] **IV. 確率整合性 / Unknown≠0**: 確率は作らないが、欠損を null (Unknown) として 0 と厳密に区別し、
  新馬は is_debut で別扱い。完走前提特徴量は非完走・非出走を除外。**PASS**
- [x] **V. 再現性・監査**: 特徴量計算は決定論的 (同一入力・同一 as-of で完全一致, SC-005)。
  feature_snapshots は予測時点監査用で feature store の代替ではないことを明記。**PASS**
- [x] **VI. feature 分割規律**: MVP はスキーマ変更なし (on-the-fly)。materialize テーブル (US4)・
  encoding (US3) は P2 で非破壊拡張。**PASS**
- [x] **品質ゲート**: `codex:codex-rescue` の second opinion を spec 段階で取得・記録 (下記)。本 plan は
  その設計を実行するもので新たな非自明分岐なし。**PASS**

### Second Opinion 記録 (codex:codex-rescue — spec 段階)

| 論点 | codex 助言 | 本 plan の対応 |
|---|---|---|
| MVP スコープ | P1=リーク安全特徴量+メタデータ、価値検証は 005、P2=encoding/materialize | 採用 (US1/US2 P1) |
| リーク機構 | `race_date < R` は過去成績に概ね安全 (同日除外)。target encoding は OOF/train-only。purge/embargo は通常不要 | 採用 (R1/R8) |
| 特徴量セット | MVP 少数: career_starts, days_since_last, prev_finish, win_rate, avg_finish, prev_last3f。完走系と非完走系を分離 | 採用 (R2/R3) |
| 新馬・低履歴 | null + flags (has_past_race/is_debut/is_low_history)。Unknown と 0 の混同は重大 | 採用 (R4) |
| メタデータ | FeatureRegistry を必須化、未登録列は fail-fast、結果後は機械的除外 | 採用 (R5) |
| 保存 | P1 on-the-fly、pandas は自然だが sort/groupby/shift のリーク検査必須。cutoff は SQL/機構で固定 | 採用 (R6/R7) |
| 校正・035/036 リスク | 結果確定 odds/popularity を特徴量禁止。校正器も valid/test を見ない。win/top2/top3 別校正は整合性破壊に注意 | 採用 (FR-012、校正注意は 005 へ申送り) |
| 横断リスク | 取消・除外後の母集団再正規化、ID 推測結合禁止、馬体重/枠順/オッズは予測時点別特徴群 | 採用 (タイミング別 metadata) |

最重要リスク TOP3 (codex): ①race_date 境界/同日情報の未来混入、②target encoding/校正器の fold 漏れ、
③新馬・取消・非完走の 0 埋め。①③は MVP の機構 + テストで対応、②は US3 (P2) + 005 で対応。

## Project Structure

### Documentation (this feature)

```text
specs/004-feature-engineering/
├── plan.md
├── research.md          # as-of 機構・特徴量定義・欠損/フラグ・registry・pandas リーク検査
├── data-model.md        # FeatureRow 固定スキーマ・registry メタデータ・母集団
├── quickstart.md        # 特徴量生成 + リーク検査テスト手順
├── contracts/
│   ├── feature_matrix.md  # FeatureMatrix / FeatureRow の列契約 + metadata
│   └── builder.md         # builder/registry の関数契約と as-of 不変条件
└── tasks.md             # /speckit-tasks
```

### Source Code (repository root)

```text
features/                                  # 新パッケージ horseracing-features
├── pyproject.toml                         # horseracing-db (path) + pandas + numpy + sqlalchemy
├── src/horseracing_features/
│   ├── __init__.py
│   ├── registry.py                        # FeatureRegistry: name -> (source, availability_timing, missing_policy)
│   ├── schema.py                          # 固定列定義 + フラグ (has_past_race/is_debut/is_low_history)
│   ├── loader.py                          # DB から races/race_horses/race_results を一括ロード (2007+)
│   ├── history.py                         # as-of (race_date < R) で過去成績集計 (完走/非完走分離)
│   ├── static_features.py                 # 発走前静的特徴量 (レース条件・馬属性・馬体重)
│   ├── builder.py                         # build_feature_matrix(...) -> FeatureMatrix + registry 検証
│   ├── encoding.py                        # (P2) train-only target encoding
│   └── cli.py                             # (P2) build-features --from --to
└── tests/
    ├── unit/                              # history as-of, 欠損/フラグ, registry 強制, 決定論
    └── integration/                       # 実 DB で as-of リーク検査 (testcontainers)
```

**Structure Decision**: 特徴量は application ロジックなので `db/` と分離し、新パッケージ `features/`
(`horseracing-features`、`horseracing-db` 依存) を作る。`eval/`/`ingest/` と同じ層。MVP スキーマ変更なし。

## Complexity Tracking

> Constitution Check に違反なし。記入不要。
