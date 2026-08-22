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

- **変更パッケージ**: features(新モジュール+registry+materialize 結線)/ training(評価
  driver)/ eval(不変 — 既存 paired_eval・v4 ゲートをそのまま使う)/ serving(compat pin 1 行)
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
- **Codex ゲート**: spec 段階で取得成功・全採否記録済み(codex-review.md)→ PASS

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
    features/tests/unit/test_early_mid_pace_features.py   # 手計算 fixture + リーク 3 方向
    training/… (scripts/097_simulated_supply_gate.py として driver。CLI 本体は不変)
    serving/src/horseracing_serving/model_loader.py       # COMPAT pin 1 エントリ追加

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
   - primary: pooled paired winner NLL・v4 標準式(δ=0.002・sd_fold=0.001816・n_folds=3)
   - guard 1: full-info(マスク無し・同窓)evidence-of-harm(margin +0.003)
   - guard 2: 実 2025-10-11+ 方向一致 evidence-of-harm(margin +0.005)
   - verdict 正本: `primary(pooled) AND guard1 AND guard2`。個別カットオフの事後選別禁止
6. driver `scripts/097_simulated_supply_gate.py`: assert_confirmatory → 3 カットオフ順次
   (マスク→ビルド→両アーム fit→採点→rollback)→ pooled CI → guard 2 本 → verdict JSON
   (artifact_kind="full_walk_forward"・両アーム recipe hash・マスク定義・採点窓を記録)
7. **対称性の契約テスト**(codex (i)): マスク適用前後で採点対象レース集合・started 集合が
   不変であることを driver 内で assert(破れたら即 fail)

**中断点**: guard 1 が FAIL(full-info で自信を持って害)なら primary の値に関わらず REJECT。

### Phase C: 後始末(US3)

8. REJECT → bump/registry/結線/compat pin を revert・モジュール+テスト非結線保全・
   active 予測バイト一致検証(SC-005)・結果を spec に転記
9. ADOPT → 列採用を確定(features-022 が正)・実データで候補モデルを学習・登録
   (`register-arm-e` 系・verdict 添付)。**昇格はしない** — 標準窓非劣化 + prospective の
   3 点セットで別途(research D9)

## 検証ゲート(要約)

| ゲート | 条件 | 破れたら |
|---|---|---|
| 共有列パリティ | 138 列全量バイト一致 | 即中断 |
| リーク 3 方向 | 今走/同日/未来 不変 | 即中断 |
| 対称性 | マスクが母集団を変えない | 即 fail |
| primary | pooled: point<−0.002 ∧ 総 CI 上限<0 | REJECT |
| guard 1 | full-info で害の証拠なし(+0.003) | REJECT |
| guard 2 | 実窓で害の証拠なし(+0.005) | REJECT |

## Complexity Tracking

逸脱なし。新しいゲート実装・新しい as-of 機構・スキーマ変更・新規取得はいずれもゼロ。
最大の複雑さはシミュレーション driver(Phase B)だが、部品は全て実証済み
(セッション内マスク=kill-test / drop-group=069 / paired_eval+v4=既存 / arm E factory=既存)。
