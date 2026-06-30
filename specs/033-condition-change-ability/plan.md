# Implementation Plan: 条件替わり×能力/時計 交互作用 (033)

**Branch**: `033-condition-change-ability` | **Date**: 2026-06-30 | **Spec**: [spec.md](spec.md)

## Summary

§3 中コスト第3弾。027 の未マージ条件替わり base(dist_change/surface_switch/going_change=現 main に無い新情報)を新モジュール `condition_change_features.py` に再導入し、距離替わりの符号付き hinge(dist_extension/dist_shortening)× 023 の末脚/時計能力(rel_last3f_best/rel_time_avg)との交互作用 2 列を加える(計 7 列)。codex/032 の学び「積でなく新情報が主役・既存列積は GBM 冗長」を反映: class/斤量×time は除外、本群の積は新 base hinge × 能力のみ。025 単一源結線・bit パリティ・going は既存ロード列で source_fingerprint 無改修。FEATURE_VERSION 010→011。

## Technical Context

- **Language**: Python 3.12 (features, uv)。pandas/numpy。新規依存なし。
- **Storage**: PostgreSQL 16(read-only、going は races 既存列)。parquet(025)。
- **Testing**: pytest。correctness(base 差/hinge/能力交互作用/NaN/float64)・leak-guard・materialize parity。
- **Constraints**: bit パリティ非交渉。リーク境界新設なし(027 merge_asof + 023 as-of)。
- **Scope**: 新規列 7(condition_change group 1 つ)。

## Constitution Check (v1.0.0)

- [x] **I. データ契約**: raceId/2007+/ID 既存経路。**PASS**
- [x] **II. リーク防止 (NON-NEGOTIABLE)**: base=直前 started レース(merge_asof allow_exact_matches=False)、能力=023 as-of 出力、今走 result/odds 非参照。利用可能タイミング=PRE_ENTRY(条件は出馬表既知、過去能力は as-of)。欠損=NULL(0埋め禁止)。leak-guard test。**PASS**
- [x] **III. 評価先行 (NON-NEGOTIABLE)**: bundle 事前登録 OOS(baseline=features-010 vs candidate=011)。primary + fold ガード(strict majority・worst ECE 2e-3・worst dLL 5e-3)。**PASS**
- [x] **IV. 確率整合性**: 特徴追加のみ、009 不変。Unknown=NaN。**PASS**
- [x] **V. 再現性・監査**: parquet 決定論再生成。FEATURE_VERSION 011。going は既存ロードで fingerprint 無改修。**PASS**
- [x] **VI. feature 分割規律**: スキーマ変更なし。features 内拡張。**PASS**
- [x] **品質ゲート**: codex second opinion は 032 直前に取得済(027 base 再導入が主眼・hinge エンコード・class×time 等の冗長積は除外)。新規相談は重複のため省略(理由を会話で宣言)。**PASS**

**Gate 結果**: 全 PASS。

## 実装アプローチ

1. `condition_change_features.py`(新): 027 の `_surface`/`_GOING_ORD`/`_runs`/`_prev_started` を移植。dist_change/surface_switch/going_change を算出(027 ロジック)。
2. hinge: dist_extension=where(notna, max(dc,0), NaN)・dist_shortening=where(notna, max(−dc,0), NaN)。
3. 能力交互作用: build_pace_features(frames, pace=渡し) の rel_last3f_best/rel_time_avg を merge し、dist_ext_x_closing=dist_extension×(−rel_last3f_best)・dist_short_x_speed=dist_shortening×(−rel_time_avg)。
4. NaN 伝播・float64 cast。
5. registry に group + FEATURE_VERSION 011。materialize 結線(pace 渡し)。feature-eval 既定 drop=condition_change。

詳細は [contracts/condition-change-features.md](contracts/condition-change-features.md)。

## Project Structure

```text
features/src/horseracing_features/
├── condition_change_features.py  # 033（新規）
├── registry.py                   # 改修: condition_change group + FEATURE_VERSION 011
├── materialize.py                # 改修: build_asof_features に結線（pace 渡し）
features/tests/unit/
├── test_condition_change_features.py  # 新規: correctness
├── test_condition_change_leak.py      # 新規: leak-guard + grep
├── test_materialize_core.py           # 改修: 010→011, condition_change in materialized_columns
└── test_feature023_leak_guard.py      # 改修: FEATURE_VERSION 010→011
training/src/horseracing_training/cli.py  # 改修: feature-eval 既定 drop を condition_change
```

## 実データ結果（T013, 18 fold walk-forward OOS, baseline=features-010）

**bundle（condition_change 全7列）= ADOPTED=True（弱いが通過）**: win LogLoss 0.23193→**0.23187**(−0.00006)・AUC 0.75153→**0.75197**(+0.00044, 032 の 4 倍)・Brier 不変・ECE 0.00858→0.00878(微悪化, 平均 tol 1e-3 内)・**11/18 fold**(strict majority)・worst_dLogLoss +0.00037(<5e-3)・worst_dECE +0.00148(<2e-3)・primary_pass=True。
**決定: 採用**(features-011=010+condition_change, lgbm-033 再学習・active 昇格, lgbm-032 retired)。**仮説が当たった**: 027 の条件替わり base は単独 group では 8/18 で不発だったが、距離替わりを hinge(延長/短縮)に分け末脚/時計能力と交互作用させた結果 **AUC が 032 の 4 倍持ち上がった**(027 base が「効く形」に変換された)。032(積中心=AUC +0.0001)と異なり、本 feature は「新 base 情報を効く形に変換」が効いた=032 の学び(積でなく新情報)の延長線。ECE 微悪化は 020/023 同様の discrimination↔calibration トレードオフ(tol 内、017 p校正で相殺)。実 DB parity bit 一致(916k×93)・カバレッジ 89.6%(前走あり馬)。市場 q 超過は SECONDARY。

## Complexity Tracking

違反なし(全ゲート PASS)。
