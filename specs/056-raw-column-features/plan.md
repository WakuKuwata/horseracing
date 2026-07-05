# Implementation Plan: JRA-VAN 生データ未使用カラムの活用 — テン3F・馬主/生産者・賞金レベル・系統

**Branch**: `056-raw-column-features` | **Date**: 2026-07-03 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/056-raw-column-features/spec.md`

## Summary

生 CSV 73 列中 35 列しか読んでいない ingest に 6 列(テン3F・1着賞金・馬主・生産者・父系統・母父系統)を追加取込(migration 0010、既存バイト不変・冪等な upsert 再実行)し、features-013 の 4 群 11 列(pace_first3f / owner_breeder / race_level / sire_line)をシリーズ規律(strictly-before as-of・025 単一源・fingerprint fail-closed)で構築、シリーズ標準の 18-fold feature-eval bundle ゲートで採否を機械判定、通過時のみ lgbm-055 を再学習・昇格する。spike 実証済み(テン3F 意味 100% 検証・OOS 増分 −0.006、owner/breeder as-of 勝率に正シグナル)。netkeiba 不要=no-netkeiba 方針と両立。詳細は [research.md](research.md)(D1〜D7)・[data-model.md](data-model.md)。

## Technical Context

**Language/Version**: Python 3.12(既存 uv workspace)

**Primary Dependencies**: pandas/numpy(特徴)、SQLAlchemy 2.0/Alembic/psycopg3(migration・ingest)、LightGBM(再学習)

**Storage**: PostgreSQL 16 — **migration 0010**(nullable 列追加のみ: race_results.first_3f / races.prize_money / horses×4、data-model.md 表が正)。head 0009→0010

**Testing**: pytest + testcontainers(migration/ingest 統合)、実 fixture(生 CSV 断片)での parser 単体、features の leak-guard/パリティ/冪等

**Target Platform**: ローカル CLI(既存 ingest-year / features materialize / training feature-eval・train-evaluate)

**Project Type**: 既存マルチパッケージ monorepo の層内拡張(db / ingest / features / training)

**Performance Goals**: 全期間再 ingest ≈ 既存 ingest-year × 19 年(数十分)。特徴構築は 023/026 同規模の追加ブロック(materialize 経路で吸収)

**Constraints**: 既存列・既存行バイト不変(冪等 upsert)/ FEATURE_VERSION bump は採用時のみマージ(035 教訓)/ Unknown=NaN(0 埋め禁止)/ 全 as-of strictly-before

**Scale/Scope**: 再 ingest 対象 ~92 万行×19 年。新特徴 11 列(features-013 で計 ~108 列)

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.* — **Phase 1 後 再評価済み: 全 PASS(品質ゲートのみ正当化付き deviation)**

- [x] **I. データ契約**: PASS — raceId 12 桁・2007+ 不変。新エンティティキーは名前(NFKC 正規化、026 前例)で id_mappings 対象外(JRA-VAN 単一源内の列追加であり横断結合なし)。ラベル契約不変。
- [x] **II. リーク防止 (NON-NEGOTIABLE)**: PASS — first_3f は結果由来のため**今走値を特徴にしない**(過去走 as-of のみ、merge_asof allow_exact_matches=False+同日除外)。owner/breeder 跨エンティティ統計は daily cumsum−当日(020 human_form 規律)。prize は事前公開のレース条件(レース内定数を機械検証済み=結果でない、research D4)。オッズ非流入。全新群に leak-guard テスト(今走・同日・未来の改変に不変)。
- [x] **III. 評価先行 (NON-NEGOTIABLE)**: PASS — 採否はシリーズ標準の feature-eval bundle 判定(閾値は既定値を据え置き=spec で事前登録)。baseline=features-012(新 4 群 drop)。ablation は diagnostic。spike は事前確認であり採否には使わない。
- [x] **IV. 確率整合性**: PASS — 予測経路の出力・正規化は不変(特徴追加のみ)。Unknown=NaN と 0 の区別(min_starts 未満 NaN、欠損 NULL→NaN 伝播)。
- [x] **V. 再現性・監査**: PASS — 再 ingest は ingestion_jobs 監査に載る既存経路。採用時 lgbm-055 の metadata に feature_version/feature_hash。materialize は fingerprint fail-closed(黙って古い値を出さない)。
- [x] **VI. feature 分割規律**: PASS — UI なし。スキーマ変更は nullable 追加のみの 1 migration で正当化(新データ取込に必須)。API/openapi 契約不変(FR-007)。
- [x] **品質ゲート(codex second opinion)**: **DEVIATION(正当化)** — codex CLI セッション 5 回起動不可。代償: (a) spike で意味(100% 恒等式)とシグナル(OOS −0.006)を実測済み=仮説先行でない、(b) 機構は全てシリーズ実績(023 in-race relative / 020 human_form / 026 名前キー / 025 fingerprint)の流用で新規発明なし、(c) research D1〜D7 に却下代替案を明記、(d) 事前登録ゲートで誤採用をデータで遮断。CLI 復旧時は implement 前に再試行。

## Project Structure

### Documentation (this feature)

```text
specs/056-raw-column-features/
├── spec.md / plan.md / research.md (D1-D7) / data-model.md / quickstart.md
├── checklists/requirements.md
└── tasks.md (Phase 2 — /speckit-tasks)
```

### Source Code (repository root)

```text
db/migrations/versions/0010_raw_column_features.py   # 新規: nullable 6 列(data-model 表)
db/src/horseracing_db/models.py                      # RaceResult.first_3f / Race.prize_money / Horse×4

ingest/src/horseracing_ingest/
├── layout.py        # FIRST_3F=54 / PRIZE_MONEY=23 / OWNER_NAME=64 / BREEDER_NAME=65 /
│                    # SIRE_LINE=69 / DAMSIRE_LINE=70
├── parser.py        # 新列の欠損規律(空→None)・数値変換
└── (pipeline/upsert は不変 — PK upsert が新列を自然に運ぶ)

features/src/horseracing_features/
├── pace_features.py            # loader に first_3f 追加 + rel_first3f/pace_balance ブロック
│                               # (023 機構流用。拡張 leak-guard の対象)
├── owner_breeder_features.py   # 新規: 跨エンティティ as-of 勝率(human_form 同型・NFKC キー)
├── race_level_features.py      # 新規: asof_prize_avg(as-of)+ prize_money_log/prize_rel(builder 合成)
├── static_features.py          # prize_money_log / sire_line / damsire_line(STATIC_COLUMNS)
├── registry.py                 # FEATURE_GROUPS+11 列・FEATURE_VERSION features-013
├── materialize.py              # build_asof_features に新ブロック・source_fingerprint 拡張(D6)
└── tests/                      # leak-guard(新群)・冪等・パリティ・fingerprint fail-closed

training/src/horseracing_training/cli.py   # feature-eval 既定 drop_groups → _DEF_055(新 4 群)
```

**Structure Decision**: 新パッケージなし。ingest は layout/parser の列追加のみで pipeline/upsert 不変(再実行=backfill、research D5)。特徴は 025 `build_asof_features` 単一源に 3 ブロック追加+static 3 列。owner/breeder は as-of 集約で TE 拡張はしない(research D2 — ゲート経路との整合)。FEATURE_VERSION bump を含むため**採用決定までブランチに留め、main へは採用時のみマージ**(035 教訓、FR-006)。

## Complexity Tracking

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| codex second opinion 未取得(品質ゲート deviation) | CLI がセッション 5 回起動不可(環境要因) | 待機はロードマップ(ユーザー承認済み順序)と不整合。spike 実測+全機構のシリーズ前例+事前登録ゲートで単一視点リスクを緩和。復旧時 implement 前に再試行 |
| migration 0010(スキーマ変更) | 新データ列は DB に存在せず取込にはスキーマが必要 | 特徴側での CSV 直読み — 025 の単一源・fingerprint 監査・serving fallback の外に出るため却下(全経路が DB を正とするシリーズ設計) |
