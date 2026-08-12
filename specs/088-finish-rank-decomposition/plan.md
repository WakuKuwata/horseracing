# Implementation Plan: 着順の頭数正規化+ラグ分解 bundle(前走着順軸の測定的クローズ)

**Branch**: `088-finish-rank-decomposition` | **Date**: 2026-08-08 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `/specs/088-finish-rank-decomposition/spec.md`

## Summary

gain importance を支配する前走着順(生値)の分解 — 頭数正規化(finish_pct)・個別ラグ(前々走/3走前)・5走平均・トレンド — に残余の予測情報があるかを、事前登録した 10 列 1 bundle で測定して確定する。**null-is-success 型**(期待値は低いと spec 冒頭で明示済み。REJECT でも「この軸は搾り切った」が前例として成立することが成果)。

技術的アプローチ: 新モジュール `finish_decomposition_features.py`(per-horse as-of・完走系列・072 投影対応)を 025 単一 as-of 源に純加算で結線し、`FEATURE_VERSION` を features-020 に bump(019 は 070 焼却済み)。評価は既存機構のみ — binary `feature-eval` は**診断専用(非ゲート)**とし、**verdict は本番 pl_topk 構成の `paired-eval --subgroups` を必ず実行して決める**(068 契約+069 subgroup+059 型非悪化の三値決定表=FR-013a)。REJECT 時は bump+結線を revert しモジュール+テストを負の結果として保全(062/070 同型)。**スキーマ変更ゼロ・migration なし・API/OpenAPI/front 不変・新規スクレイピングなし・新規ソース列ゼロ(source_fingerprint 不変=materialize-safe)**。

codex 2 回目レビュー(列定義+評価設計の絞り込み)で当初案から 3 点を設計改訂: ①分母を完走頭数→**出走頭数**(推定対象=フィールド規模の正規化に整合) ②finish_trend3→**finish_trend5**(3 点 OLS は採録済みラグの線形結合で独立情報ゼロ) ③binary 打ち切り廃止→**判定段の常時実行**(binary が過小評価する逆方向を排除できないため)。全採否は research D10。

## Technical Context

**Language/Version**: Python 3.12(features / training / eval のみ・他パッケージ不変)

**Primary Dependencies**: pandas / numpy(既存)・LightGBM(既存・学習は判定段と ADOPT 分岐のみ)。新規依存ゼロ

**Storage**: PostgreSQL 16(読むだけ・スキーマ変更なし)。特徴 parquet(artifacts/features.parquet・再 materialize 1 回)

**Testing**: pytest(features 単体+リーク不変+parity、training は既存 harness 流用)

**Target Platform**: ローカル macOS(オペレータ CLI 実行)

**Project Type**: 既存 monorepo の features/training パッケージ拡張(1 bundle 特徴+既存評価機構)

**Performance Goals**: build への追加コストは軽量 as-of 集約 1 block(既存 rolling/merge_asof と同オーダー)。serving 1 レース予測の現行レイテンシ(~24s)を悪化させない — 072 投影(target_race_ids)を per-horse 型で実装(research D5)

**Constraints**: 既存共有列バイト不変(check_exact+check_dtype)・全列 float64・NaN 0 埋め禁止・OOS 後の定義/閾値変更禁止・features-019 識別子の再利用禁止

**Scale/Scope**: 学習行 ~957k・10 列追加。判定段の pl_topk paired 評価(fold ごと再学習)は十数時間級の長時間ジョブ(**常時実行** — codex 論点B 採用。nohup+監視の 056 運用ノートに従う)

## Constitution Check

- [x] **I. データ契約**: PASS — race_id/horse_id は既存 frame の canonical ID をそのまま使用。ID 解決・結合の新規箇所なし。2007+ 既存プールのみ
- [x] **II. リーク防止 (NON-NEGOTIABLE)**: PASS — 全 10 列は strictly-before+同日除外の完走走のみ(既存 history 機構と同一規約・[contracts/feature-columns.md](contracts/feature-columns.md) INV-C4/C5)。過去走の完走頭数は結果確定済み情報(リークでない)。対象レースの市場/結果は非入力。リーク不変テストで機械固定。走査対象は結果履歴のみ=市場データ非使用(p⊥q と無関係、default 系譜に組込可)
- [x] **III. 評価先行 (NON-NEGOTIABLE)**: PASS — 既存 068/069 ハーネスをそのまま使用(新指標なし)。ゲート・seed・手順を [contracts/adoption-gate.md](contracts/adoption-gate.md) で OOS 前凍結。三値判定・null-is-success。判定段は recipe drop アーム(candidate vs 同構成−bundle)の paired 比較+ECE 非劣化(gate.adopted 組込み)
- [x] **IV. 確率整合性**: PASS — 確率導出経路は不変(特徴の追加のみ)。Unknown=NaN で 0 と区別(INV-C6)
- [x] **V. 再現性・監査**: PASS — verdict・効果量・CI・subgroup・カバレッジを artifact+spec 転記(FR-014/017/018)。FEATURE_VERSION/feature_hash で特徴定義版を監査。オッズ非関与
- [x] **VI. feature 分割規律**: PASS — UI なし・スキーマ変更なし・margin-aware 教師信号は別 spec に分離(1 bundle 1 spec)
- [x] **品質ゲート(codex second opinion)**: PASS — 1 回目(広域)はタイムアウトだが途中所見 3 点を回収・採否記録済み。2 回目(列定義+評価設計の絞り込み)は**完了・採用 6 件/不採用 2 件(理由つき)/確認 1 件**で、うち 3 件は設計改訂(分母 STARTED 化・trend5 化・判定段常時実行)に反映済み。全採否の記録は research D10(憲法の「両案差分と採用根拠の記録」要件を充足)

## Project Structure

### Documentation (this feature)

```text
specs/088-finish-rank-decomposition/
├── spec.md
├── plan.md              # This file
├── research.md          # D1-D10
├── data-model.md        # 10 列定義表・入力射影
├── quickstart.md        # 検証手順
├── contracts/
│   ├── feature-columns.md   # 列定義の凍結不変条件 INV-C1..C11(+C2a)
│   └── adoption-gate.md     # 診断段/判定段ゲートの事前登録(gate-config・verdict 正本)
├── checklists/requirements.md
├── gate-config.json         # T016 で作成・凍結(canonical hash を adoption-gate.md に記録)
└── tasks.md             # 生成済(T001-T024)
```

### Source Code (repository root)

```text
features/src/horseracing_features/
├── finish_decomposition_features.py   # 新規: 10 列 as-of block(per-horse・072 投影対応)
├── registry.py                        # FEATURE_GROUPS に finish_decomp 10 列 / FEATURE_VERSION → features-020 /
│                                      # COMPATIBLE_PRIOR_FEATURE_VERSIONS["features-020"] に 018 pin 追加
└── materialize.py                     # build_asof_features に 1 箇所結線(025 単一 as-of 源)

features/tests/
└── test_finish_decomposition_features.py  # 手計算 fixture・リーク不変・純加算・dtype・072 投影 parity

training/                              # 変更なし(既存 feature-eval / paired-eval / train-evaluate を実行するだけ)
eval/                                  # 変更なし
serving/ betting/ api/ front/ admin/   # 変更なし(compat pin により既存モデル serving 不変)
```

**Structure Decision**: 既存の 1-bundle 特徴 feature の確立パターン(031/059/061 同型)。コード変更は features パッケージ内 3 ファイル+テスト 1 ファイルに限定し、評価は既存 CLI の実行のみ。REJECT 時の revert 単位は「registry の bump/pin/GROUPS 登録 + materialize の結線」だけ(research D9)。

## Complexity Tracking

違反なし(記載不要)。

## 着手時確定事項(T001・2026-08-10 実 DB 実測)

- **active モデル**: `lgbm-064-f02acc`(feature_version=features-018・objective=pl_topk・weights_uri は絶対パス)。candidate は lgbm-085-armE / lgbm-065 / lgbm-060-mkt / lgbm-058-acc
- **features-018 canonical feature_hash**(T002・compat pin に使う値): `263ef6b7ac5eccf45faf90005a5904de91adfed639b8d3f14a04c4d20f141a3f`(model_input_features 137 列)。active モデル artifact の `metadata.feature_hash` と**一致を確認済み**
- **features-019 / features-020 の使用痕跡なし**(model_versions は 004〜018 のみ・artifacts 配下の grep もヒット無し)→ FR-008 の前提(019 焼却済み・020 未使用)成立
- **現行 registry**: `FEATURE_VERSION = "features-018"`([registry.py:368](../../features/src/horseracing_features/registry.py))
- DB は `scripts/stack.sh` の postgres(port 15432・`postgresql+psycopg://aiuma:aiuma@localhost:15432/horseracing`)
