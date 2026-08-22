# Implementation Plan: Early-Mid Pace Features (rel_early_mid)

**Branch**: `097-early-mid-pace` | **Date**: 2026-08-22 | **Spec**: [spec.md](spec.md)
**Input**: spec.md + codex-review.md(採否確定済み) + research.md(D1-D10)

## Summary

供給停止で死んだテン3F 軸(実測価値 -0.0163)を、死なない入力(走破時計・上がり3F)から作る
一貫量 `rel_early_mid` の独立 2 列で恒久回収する。効果は供給死亡レジームでしか現れないため、
採否は**擬似供給停止シミュレーション**(3 カットオフ・washout 1 年・fold 内マスク再学習・
pooled paired winner NLL・v4 標準ゲート)で決める。full-info 非劣化 guard と実 2025-2026
方向一致 guard を併設。新規スクレイピングゼロ・新規ソース列ゼロ・スキーマ/API 不変。

## Technical Context

- **変更パッケージ**: features(新モジュール+registry(FEATURE_VERSION/compat pin)+materialize 結線)/
  training(評価 driver)/ eval(`PairedReport.diffs_by_day` の純増露出 + `provenance.py` 小ヘルパ。
  ゲート実装は不変)/ serving(**無改修** — compat pin は features/registry.py にある)/
  front+admin(表示ラベル 2 行のみ=088 の教訓)
- **新列**: `asof_rel_early_mid_avg` / `asof_rel_early_mid_best`(FEATURE_GROUP
  `early_mid_pace`・float64・NaN=情報なし)
- **FEATURE_VERSION**: features-021 → features-022(019/020 焼却済み再利用禁止)。
  compat pin = lgbm-094-cap900 の実測 hash `663fe86c7564…`(metadata.json 照合済み)
- **評価**: research D3-D5。既存 `paired_eval` + `drop=early_mid_pace`(069)+
  セッション内マスク(kill-test 実証済み機構)。新しいゲート実装は書かない
- **性能**: シミュレーション 3 本 ≈ 2h + full-info guard ≈ 2.4h(096 実績)。
  実行時間の予算は部品積み上げでなく実績比で見る(095 の教訓)

## Constitution Check

- **I. データ契約**: 新 ID 結合なし。race_results の既存列のみ読む → PASS
- **II. リーク防止(非交渉)**: 既存 as-of 機構(strictly-before+同日除外+finished-only)に
  載るだけ。新規 loader 列ゼロ。挙動テスト 3 方向で固定(FR-007)→ PASS
- **III. 評価先行(非交渉)**: gate-config を実行前凍結・hash fail-closed・判定式単一・
  OOS 後の列選別禁止(D9)→ PASS
- **IV. 確率整合性**: 確率導出層に触れない → PASS
- **V. 再現性と監査**: evidence 追跡パス・hash 3 点(gate-config/レース集合/recipe)・マスクは
  未 commit + 必ず rollback → PASS
- **VI. feature 分割規律**: 単一束・単一 spec・REJECT 時の revert 手順を事前定義 → PASS
- **Codex ゲート**: spec 段階(2 問)+ tasks 段階(2 問)の計 4 問を取得成功・全採否記録済み
  (codex-review.md)。tasks 段階で昇格ゲートの構造的な穴とマスク provenance を検出し再事前登録 → PASS

## Project Structure

### Documentation (this feature)

    specs/097-early-mid-pace/
    ├── spec.md / plan.md / research.md / codex-review.md
    ├── data-model.md
    ├── contracts/
    │   ├── feature-columns.md      (INV-EM1..EM8)
    │   └── adoption-gate.md        (シミュレーション設計の凍結 + 判定式)
    ├── gate-config.json            (実行前に凍結・hash 記録)
    └── quickstart.md

### Source Code

    features/src/horseracing_features/
    ├── early_mid_pace_features.py      # 新規: rel_early_mid の走単位値 + as-of 集約
    ├── pace_features.py                # 不変(既存 first3f 列は触らない)
    ├── registry.py                     # FEATURE_GROUPS + FEATURE_VERSION bump
    └── materialize.py                  # build_asof_features に 1 箇所結線
    features/tests/unit/test_early_mid_pace_features.py   # 手計算 fixture・欠損・1200m 恒等・投影 parity
    features/tests/unit/test_early_mid_pace_leak.py       # リーク 3 方向
    features/tests/unit/test_registry_features022.py      # 登録・group・compat pin
    training/tests/unit/test_097_mask.py                  # mask SQL / provenance hash
    eval/tests/unit/test_paired_report_diffs_by_day.py    # diffs_by_day 純増
    scripts/parity_097.py / scripts/coverage_097.py       # SC-001 / SC-002 の証跡
    eval/src/horseracing_eval/paired.py                   # PairedReport.diffs_by_day 純増
    eval/src/horseracing_eval/provenance.py               # frame_projection_hash(pure pandas)
    training/src/horseracing_training/supply_mask.py       # mask SQL / symmetry / provenance(driver とテストが import)
    scripts/097_simulated_supply_gate.py                  # driver(CLI 本体は不変)
    front/src/components/featureLabels.ts + admin/src/lib/featureLabels.ts   # ラベル 2 行

## 実装フェーズ

### Phase A: 構築(US1)

1. `early_mid_pace_features.py`: 走単位 `em = finish_time_s − last_3f`(em ≤ 0 は NaN =
   入力破損・1200m 導出 backfill と同じ規律)→ レース内完走馬平均との差 `rel_em` →
   既存 `_rolling_asof` 機構(同一窓 _RECENT_N=5)で `_avg`/`_best`。
   **実装は pace_features の機構を呼ぶ**(二重実装禁止・025 単一 as-of 源)
2. registry: group `early_mid_pace` 2 列・FEATURE_VERSION features-022・
   optional-leaf 登録(072 投影の per-horse 型)
3. serving compat: features-022 → {features-021: lgbm-094 hash pin}
4. 検証: 共有 138 列全量バイト一致(SC-001)・2026 充足 ≥95%(SC-002)・リーク 3 方向(SC-003)・
   materialize parity(fingerprint 不変)

**中断点**: SC-001 が破れたら即中断(既存列を動かした = 設計違反)。

### Phase B: 判定(US2)

5. gate-config.json を凍結(下記パラメータ・hash 記録・コミット)してから driver を書く:
   - cutoffs: 2019-01-01 / 2021-01-01 / 2023-01-01(マスク = cutoff 以降 ∧ 距離≠1200m の
     first_3f を NULL・セッション内・未 commit)
   - 採点窓: 2020 / 2022 / 2024(互いに素・washout 1 年 = 実測 98.1% 定常)
   - アーム: features-022 全列 vs `drop=early_mid_pace`(同一マスク環境・同一ビルド)
   - レシピ: 現 active 同等(pl_topk・arm E OOF isotonic 8 blocks・rounds 900・091 体重マスク・
     seed 42・num_threads 1)
   - primary: pooled paired winner NLL・v4 標準式(δ=0.002・sd_fold=0.001816・n_folds=3)・
     sufficiency = pooled n_days ≥ 300(実測 323)・top2/top3/ECE は n_races 加重平均(ECE は近似)
   - guard 1: full-info(マスク無し・同窓)evidence-of-harm(margin +0.003)
   - guard 2: 実 2025-10-11+ 方向一致 evidence-of-harm(margin +0.005)
   - verdict 正本: `primary(pooled) AND guard1 AND guard2`。個別カットオフの事後選別禁止
6. driver `scripts/097_simulated_supply_gate.py`: assert_confirmatory → 3 カットオフ順次
   (マスク→対称性 assert→provenance assert(射影クエリ)→**matrix 単一構築**→両アーム fit→採点→
   rollback)→ pooled CI →
   guard 2 本 → verdict JSON(**artifact_kind="counterfactual_supply_simulation"・
   eligible_for_verdict=False・feature_adoption_eligible=True・evidence_regime**・両アーム
   recipe hash・マスク定義・採点窓を記録)。**出力規律**: 全成分が揃うまで効果数値を出さない
7. **対称性の契約テスト**(codex (i)): マスク適用前後で採点対象レース集合・started 集合・winner
   が不変であることを driver 内で assert(破れたら即 fail)
7b. **provenance の契約テスト**(codex tasks Q2): マスク後 Frames 射影で (a) cutoff 以降∧距離≠1200m
   の first_3f 非 NULL が 0 行 (b) 両アームが同一 matrix オブジェクト(`is`)(c) `use_materialized=False`
   を assert。parquet/キャッシュ/別コネクションによる非対称汚染を構造的に排除する

**verdict 分岐**: guard 1 が FAIL(full-info で自信を持って害)なら verdict=REJECT。実行は全成分を
完走させ verdict は最後に 1 回だけ評価する(中断ではない)。

### Phase C: 後始末(US3)

8. REJECT → bump/registry/結線/compat pin を revert・モジュール+テスト非結線保全・
   active 予測バイト一致検証(SC-005)・結果を spec に転記
9. ADOPT → 列採用を確定(features-022 が正)・実データで候補モデルを学習・登録
   (`register-arm-e`・引数列は T032 — `--verdict` は存在しない)→ `promote-model --verdict`
   dry-run で `verdict_artifact_not_eligible` による昇格拒否を確認。**昇格はしない** —
   標準窓非劣化 + 本 verdict + prospective の 3 点セットで別途(research D9)

## 検証ゲート(要約)

| ゲート | 条件 | 破れたら |
|---|---|---|
| 共有列パリティ | 138 列全量バイト一致 | 即中断 |
| リーク 3 方向 | 今走/同日/未来 不変 | 即中断 |
| 対称性 | マスクが母集団を変えない | 即 fail |
| provenance | 違反行 0・同一 matrix・use_materialized=False | 即 fail |
| primary | pooled: point<−0.002 ∧ 総 CI 上限<0(+recent/top/ECE は凍結既定値) | REJECT |
| guard 1 | full-info で害の証拠なし(+0.003) | REJECT |
| guard 2 | 実窓で害の証拠なし(+0.005) | REJECT |

## Complexity Tracking

逸脱なし。新しいゲート実装・新しい as-of 機構・スキーマ変更・新規取得はいずれもゼロ。
最大の複雑さはシミュレーション driver(Phase B)だが、部品は全て実証済み
(セッション内マスク=kill-test / drop-group=069 / paired_eval+v4=既存 / arm E factory=既存)。
