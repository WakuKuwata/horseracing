# Implementation Plan: 馬体重欠損時の serving 入力是正 (Serving Weight Imputation)

**Branch**: `091-serving-weight-imputation` | **Date**: 2026-08-10 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/091-serving-weight-imputation/spec.md`

## Summary

ライブ予測時に 97.4% 欠損する馬体重を、前走体重という代理値で埋められる形にモデルへ渡す。実現手段は 3 点セット:

1. **特徴列 `prev_weight` を 1 本追加**(`features-018` → `features-021`)。当初 3 列案は実測で 2 列が既存列と完全重複と判明したため 1 列に縮小した(research D1)。
2. **学習時にレース単位で体重列を mask**(率 0.5・決定論・校正 holdout にも同一適用)。mask が無ければ `prev_weight` は当日体重に食われて機能しないので、これは付随的な工夫ではなく機構の本体である。
3. **採用ゲートを serving regime(体重マスク)で測る**。標準の paired-eval は体重が存在する確定済レースで評価するため、そのまま回すとこの feature は無価値に見える。

スキーマ・migration・API・OpenAPI・買い目生成は不変。変更は features / training / eval / serving の 4 パッケージに閉じる(serving は予測経路の可用性正規化 1 箇所のみ)。

## Technical Context

**Language/Version**: Python 3.12

**Primary Dependencies**: LightGBM(pl_topk custom objective)、pandas / numpy、SQLAlchemy 2.0(read-only)、pytest

**Storage**: PostgreSQL 16(**読み取りのみ・スキーマ変更なし**)、特徴量 parquet(`artifacts/features.parquet`、再 materialize が 1 回必要)

**Testing**: pytest。features 単体(手計算 fixture)、training/eval 単体、実 DB integration(パリティ・リーク境界)

**Target Platform**: ローカル CLI(学習・評価は長時間ジョブ)

**Project Type**: 単一リポジトリ内の複数 Python パッケージ(`features` / `training` / `eval` / `serving`)

**Performance Goals**: 特徴量ビルドの実測値(フル in-memory 72 秒)を悪化させない。`prev_weight` は 1 回の `merge_asof` なので追加コストは秒未満。serving 1 レース予測の現行 ~24 秒を悪化させない。

**Constraints**:
- 現行 active `lgbm-064-f02acc` の予測が**バイト不変**(compat 経路)
- `source_fingerprint` 不変(新ソース列を読まない)
- `apply_weight_mask(spec=None)` は現行と**バイト同一**
- `features-019` は焼却番号のため使用禁止

**Scale/Scope**: 学習行 ~955k、評価母集団 19,113 レース(2021-2026)。新規モジュール 1 本 + 純関数 1 本 + 評価経路の regime 対応 + serving 予測経路の可用性正規化 1 箇所。

## Constitution Check

*GATE: Phase 0 前に PASS。Phase 1 設計後に再チェック済み(末尾)。*

- [x] **I. データ契約**: `raceId` 12 桁契約に触れない。前走体重の結合キーは `horse_id`(`id_mappings` を跨いだ推測結合はしない)。ラベル定義不変。**N/A に近いが PASS**。
  - 注記: `nk:` サロゲートによる個体分裂で履歴が繋がらない馬は「前走なし」になる。これは 067 未実装に由来する既知の制約であり、本 feature は解決しない。ただしカバレッジ監査で真のデビューと区別して報告する(FR-028)。
- [x] **II. リーク防止 (NON-NEGOTIABLE)**: `prev_weight` は厳密前 + 同日除外(既存 `_prev_started` と同一規約)。結果・当日オッズを一切読まない。mask はレース属性と seed のみで決まり結果に依存しない。leak-guard テストで「結果を変えても値が変わらない」を機械検証(INV-W3)。registry に source / availability_timing / missing_policy を必須記載。**PASS**
- [x] **III. 評価先行 (NON-NEGOTIABLE)**: walk-forward OOS の paired 評価。ゲートは gate-config として OOS 前に凍結し hash 検証。δ・mask 率・seed・subgroup をすべて事前登録。診断アーム(m=0/1)の結果で採用値を差し替えない。**PASS**
- [x] **IV. 確率整合性**: 予測後処理(race-softmax → isotonic → clip → 正規化 → Harville)を一切変更しない。`prev_weight` 欠損は NaN のまま(0 埋め禁止)。FR-034 の可用性正規化は**入力の選択**であって後処理ではない。**PASS**
- [x] **II / V(serving 経路の変更について再評価)**: FR-034 は予測経路に条件分岐を入れる変更なので、憲法チェックを serving 側にも当てた。(a)**リーク面ゼロ** — 判定に使うのは対象レースの出走馬の体重が公表済みか否かだけで、結果・オッズ・他レースを読まない。(b)**現行モデルへの影響ゼロ** — FR-034a により前走体重を持たないモデルでは発動しない(SC-004 が全入力条件で成立)。(c)**監査** — 発動回数と (計測済み/出走) 分布を記録し(FR-035)、`feature_snapshots` には従来どおり per-horse のモデル入力ベクトルが残るので、どの値でその予測が出たかは事後に確認できる。(d)**経路の一貫性** — ライブと backfill に同一条件で適用する(FR-034b)。**PASS**
- [x] **V. 再現性・監査**: `feature_snapshots` が既に per-horse のモデル入力ベクトルを保存しており体重の有無は事後確認可能。加えて `logic_version` に体重 regime marker を追加。mask の seed と率を artifact に記録。**PASS**
- [x] **VI. feature 分割規律**: UI 変更なし。API/DB 契約不変。FEATURE_VERSION bump は compat pin で正当化。**PASS**
- [x] **品質ゲート**: codex に kill-test 設計レビュー(13 指摘・採否を research D11 に全件記録)と plan レビューを `codex exec` 直叩きで実施。**PASS**

### 憲法上の判断が要る点(違反ではないが明示する)

**III との緊張**: 学習データに人工的な欠損を注入する。これは「評価派生値をモデルに戻す」類のリークではない(mask はレース属性と seed のみで決まる)が、**学習分布を意図的に本番分布へ寄せる操作**であり、full-info regime の精度を意図的に犠牲にする。この trade-off を verdict の二本立て(PRIMARY = serving / GUARD = full-info)で明示的に管理する。

**V との緊張**: 本 feature 導入後、同一レースでも backfill(体重あり)と live(体重なし)で予測が変わる。再現性は `feature_snapshots` で担保されるが、**full-info の backfill 予測を live 品質の代理に使うと 065 の closing-oracle バイアスと同型の誤りになる**。契約で明示的に禁じる(research D7)。

## Project Structure

### Documentation (this feature)

```text
specs/091-serving-weight-imputation/
├── spec.md              # 完成
├── plan.md              # This file
├── research.md          # Phase 0 (D1-D13)
├── data-model.md        # Phase 1
├── quickstart.md        # Phase 1
├── gate-config.json     # OOS 前に凍結する採用ゲート
├── contracts/
│   ├── feature-columns.md   # prev_weight の定義と不変条件
│   ├── weight-mask.md       # mask の意味論と regime 契約
│   └── adoption-gate.md     # verdict の正本と事前登録値
├── evidence/            # kill-test 再現スクリプトと JSON レポート
└── checklists/requirements.md
```

### Source Code (repository root)

```text
features/src/horseracing_features/
├── weight_history_features.py   # 新規: prev_weight の as-of 解決
├── weight_mask.py               # 新規: apply_weight_mask 純関数(regime 変換)
├── materialize.py               # 変更: build_asof_features に 1 箇所結線
└── registry.py                  # 変更: 列登録 / FEATURE_GROUPS / FEATURE_VERSION / compat pin
                                 #        + carried_weight_ratio の timing 是正(US3)

features/tests/unit/
├── test_weight_history_features.py   # 新規: 手計算 fixture・境界・同日除外
├── test_weight_mask.py               # 新規: race-atomic・決定論・None=不変
└── test_weight_leak.py               # 新規: leak-guard(結果/オッズ/同日で不変)

features/tests/integration/
└── test_features020_parity.py        # 新規: 共有列バイト一致・fingerprint 不変

training/src/horseracing_training/
├── recipe.py       # 変更: weight_mask フィールド(既定 None は recipe_hash から除外=back-compat)
└── predictor.py    # 変更: fit / raw_win_probs に mask spec を通す(既定 None で現行同一)

eval/src/horseracing_eval/
├── paired.py       # 変更: regime 別スコアと full_info_guard を PairedReport に追加
└── foldfit.py      # 変更: predict 側 mask spec の受け渡し

training/src/horseracing_training/cli.py   # 変更: paired-eval に --weight-regime 等を追加

serving/src/horseracing_serving/
├── predictor.py    # 変更: レース単位の可用性正規化(FR-034)。モデルが prev_weight を
│                   #        持つ場合のみ発動(FR-034a)。ライブと backfill の両方に効く(FR-034b)
└── pipeline.py     # 変更: 正規化の発動回数と (計測済み/出走) 分布の記録(FR-035)
                    #        + ADOPT 時に logic_version へ体重 regime marker

serving/tests/unit/test_weight_availability_normalization.py  # 新規
```

**Structure Decision**: 既存パッケージ境界をそのまま使う。`features` が唯一の as-of 源([025](../055-materialized-serving/spec.md) の単一実装規律)、`training` が学習、`eval` が predictor-agnostic な評価。

**パッケージ依存の向き(重要)**: `eval` は `horseracing-features` に**依存していない**(現行の依存は db / numpy / sklearn / sqlalchemy / psycopg)。したがって `apply_weight_mask` と `MaskSpec` を `features` に置いても、**`eval` にそれらを import させてはならない** — させると 020 で確立した「eval は predictor-agnostic」の境界を壊す。よって:

- mask spec の**構築は `training` / CLI が行う**
- `eval` は spec を**中身を見ない不透明な値として `predict_over_folds` に受け渡すだけ**にする
- `stable_hash` は現在 `eval/hashing.py` にあるので、`features` 側に mask 選択用の決定論ハッシュを**独立に持つ**(eval への依存を作らない)

serving は予測経路にレース単位の可用性正規化(FR-034)が入るため、**予測ロジックに触れる**。ただし発動条件は「そのモデルが前走体重を入力に持つか」の一点で機械的に決まり、前走体重を持たないモデルには何もしない(FR-034a)ので、現行 active の予測はバイト不変のまま保たれる。

## 実装フェーズ(概要 — 詳細は /speckit-tasks)

**Phase A(US1 前半・構築)**: `prev_weight` の as-of 解決 + registry 登録 + build 結線 + パリティ実測。この時点で FEATURE_VERSION はまだ上げない(パリティ確認まで既存列不変を担保)。

**Phase B(US1 後半・mask 機構と可用性正規化)**: `apply_weight_mask` 純関数 + recipe/predictor への配線。`spec=None` のバイト同一性テストを先に緑にしてから mask 経路を足す。**続けて serving 予測経路のレース単位可用性正規化(FR-034 / 034a / 034b)**を入れる — 学習側が race-atomic mask になった直後に本番側も二値に揃えることで、両者がずれている期間を作らない。

**Phase C(US2・評価契約)**: paired-eval の regime 対応 + verdict の合成。gate-config の凍結と hash 検証は Phase D の直前に置く。

**Phase D0(US2・bump と受入)**: FEATURE_VERSION bump + compat pin + 再 materialize + compat 受入 + **配線 E2E smoke**。bump 後にしか検出できない不具合(旧 parquet の再利用・FEATURE_GROUPS の登録漏れ/重複/列順・candidate recipe が新列を選ばない・loader が新 version を選ばない)をここでまとめて潰す。続けて **outcome-blind 受入**(直近 fold・効果を見ない項目だけ: mask 件数 / 両アーム一致 / カバレッジ / 指標が有限 / provenance 一致)を通す。

> **受入で効果を見てはならない。** 当初は「直近 fold で候補が現行を上回らなければ止める」設計だったが、**その fold は最終評価窓にも含まれる**ため、効果を見て継続判断すること自体が選択リークになる(codex 指摘)。効果で中断したいなら当該 fold を verdict から除外するか二段階継続規則を事前登録する必要があり、どちらも本 feature の射程を広げる。受入 artifact には `artifact_kind="acceptance"` / `eligible_for_verdict=false` を刻み、verdict loader が機械的に弾く。

**Phase D(US2・本評価)**: gate-config 凍結 + hash 記録 → フル walk-forward paired-eval(serving regime PRIMARY / full-info GUARD / subgroup)。診断アーム(m=0, m=1)を併走。

**Phase E(US3 + verdict 分岐)**: registry timing 是正(採否と独立に実施)。ADOPT なら昇格承認フロー、REJECT なら bump/結線 revert + モジュール保全。測定結果を spec に転記。

## Complexity Tracking

| 判断 | なぜ必要か | 却下した単純案とその理由 |
|---|---|---|
| 学習データへの人工欠損注入(mask) | mask が無いと `prev_weight` は当日体重に食われて木に採用されず、serving で元の未学習 NaN 分岐に戻る。機構の本体 | 列を足すだけ(m=0): 診断アームとして測るが、主案にはしない。理由は上記 |
| 評価に regime という新しい軸を導入 | 既存 paired-eval は体重がある条件で測るため、この feature の効果を構造的に測れない | 既存 harness をそのまま使う: 測りたい効果がゼロに見える。却下 |
| 校正 holdout にも mask | 校正器の定義域と runtime 入力のずれを避ける | 校正だけ full-info: codex 前回指摘のずれが残る。却下 |
| 診断アーム 2 本(m=0 / m=1)の併走 | m=0 は「mask が本当に必要か」の対照、m=1 は「体重を捨てる設計」の先行測定 | 主案  1 本のみ: mask 率の妥当性を後から議論できず、次の feature の事前登録の根拠も残らない |
| 予測経路にレース単位の可用性正規化を入れる(FR-034) | レース内混在は race-atomic mask で学習したモデルに OOD。目的関数がレース内 softmax なので 1 頭の入力変化が全馬の確率を動かす | 混在パターンで replay して非劣化を確認する(並走 spec の案): 本番の混在分布が未知(結果未確定レースは揮発的で母集団が取れない)なので、再現すべきパターン自体が手に入らない。**測って対処するより起きない形にする方が確実**(research D12) |

## Post-Design Constitution Re-check

Phase 1 設計([data-model.md](./data-model.md) / [contracts/](./contracts/))完了後の再評価:

- **II**: `prev_weight` は既存 as-of 機構の再利用で新しいリーク面ゼロ。mask は結果非依存。**PASS 維持**
- **III**: gate-config を OOS 前に凍結・hash 検証・診断と判定を分離。**PASS 維持**
- **IV**: 予測後処理に一切触れない。**PASS 維持**
- **V**: regime marker 追加で監査が強化される方向。**PASS 維持**
- **VI**: スキーマ・API 不変、FEATURE_VERSION bump は compat pin と純加算パリティで正当化。**PASS 維持**

新たな違反は発生していない。Complexity Tracking に挙げた 5 点はいずれも憲法違反ではなく、単純案では要件を満たせないことを理由に選んだ設計判断である。
