# Implementation Plan: recency weighting — 学習の時間重み

**Branch**: `main`(直接コミット運用) | **Date**: 2026-08-26 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `specs/101-recency-weighting/spec.md`

---

## Summary

**2007-2026 の 958,011 学習行を全期間一律の重みで学習しており、直近 3 年は質量の 13.2% しかない。**
そこへ時間減衰を入れて、効くかどうかを 1 回の事前登録判定で決着させる。

feature 100 が「評価契約は主たるボトルネックではなかった」を確定させたので、モデリング側で
一度も測っていない大玉に移る、という位置づけである。

| US | 内容 | 実装コスト |
|---|---|---|
| **US1** | 時間重みの導入と採否判定 | 小(配管は 079 の遺産で存在) |
| **US2** | 重みの適用範囲の一貫性(TE / 校正器) | **中〜大**(TE と校正器に重みを通す必要がある) |
| **US3** | 交絡の切り分け(時間変化 vs 供給元切替) | 小(US1 の窓を変えて再実行) |

技術的アプローチ:

- **US1** は `LightGBMPredictor` の fit 経路に `model_weights` を渡すだけで済む。079 の
  `assert_race_constant` がレース内定数を fail-closed で担保し、PL 損失が valid な weighted
  likelihood である条件を満たす。`RACE_DATE` は既に学習行列にある。
- **US2** は `fit_target_encoder` / `oof_target_encode` / `fit_calibrator` の**いずれも重みを
  受け取らない**ので、そこに重みを通す作業が本体になる。
- **US3** は US1 のアームを疑似 cutoff(2025 年より前)で回し直すだけ。

**進め方の骨格**: A=重み関数と凍結 → B=US1 の配線と判定 → **★判定★** → C=US2(US1 が正なら)
→ D=US3(US1 が正なら)。**US1 が REJECT なら C/D は消える。**

---

## Technical Context

**Language/Version**: Python 3.12

**Primary Dependencies**: LightGBM(custom PL top-3 objective)、numpy / pandas /
scikit-learn(isotonic)、SQLAlchemy 2.0、pytest

**Storage**: PostgreSQL 16(**スキーマ変更なし**)+ ディスク artifact

**Testing**: pytest。重み無効時のビット一致・レース内定数の fail-closed・日付衛生・
ESS 報告・変異テスト

**Target Platform**: ローカル CLI(`uv run --project training`)

**Project Type**: ML 学習パッケージ + 判定のための評価

**Performance Goals**: 学習コストは重み付けで変わらない(行数は同じ)。判定 1 回の所要は現行と
同程度(arm E の 1 fit = 900 rounds / 62k レースで **102〜136 秒**、outer fold あたり base 1 +
OOF 8 = 9 fit ≈ 20 分)。

**Constraints**:
- 重み無効時は現行と**ビット一致**
- 特徴量・FEATURE_VERSION・確率導出(009)・DB スキーマ・API・買い目は**不変**
- 新規スクレイピングゼロ・migration なし

**Scale/Scope**: 958,011 学習行 / 62,171 レース。触るのは `training/` が主で、判定のために
`eval/` を読む。serving は ADOPT まで進んだ場合のみ。

---

## Constitution Check

*GATE: Phase 0 前に PASS。Phase 1 後に再チェック。*

- [x] **I. データ契約**: **N/A**。`raceId`・ID 結合・ラベルを変更しない。
- [x] **II. リーク防止 (NON-NEGOTIABLE)**: **PASS**。重みは `(race_date, cutoff)` の**純関数**で、
  結果・オッズ・未来のレースを一切参照しない(FR-002)。leak-guard テストで固定する。
  **重みは「どれだけ数えるか」であって「何を見てよいか」ではない** — target encoding の as-of
  時間境界は動かさない(FR-016)。
- [x] **III. 評価先行 (NON-NEGOTIABLE)**: **PASS**。事前登録ゲートで walk-forward OOS 判定。
  **半減期はラベルも winner NLL も使わず日付だけで事前登録**するので選択リークが構造的に
  起こりえない(FR-007)。**評価は無重みで行う**(FR-011a)。
- [x] **IV. 確率整合性**: **PASS**。重みは損失の係数であって確率の導出を変えない。Σ=1 も
  取消除外も不変。
- [x] **V. 再現性・監査**: **PASS**。cutoff・半減期・ε・ESS(粒度別)・重み分布・消失カテゴリを
  artifact に記録する(FR-008/009/010)。**再学習日が動けば全重みが動く**ので cutoff の記録は必須。
- [x] **VI. feature 分割規律**: **PASS**。UI 非関与。スキーマ変更なし。P0 未決なし
  (FR-007 は spec 段階で日付基準に確定済み)。
- [x] **品質ゲート**: **PASS**。codex 設計レビュー取得済み・**採用 10 / 部分採用 1 / 不採用 1** を
  [codex-review.md](./codex-review.md) に記録。

**違反ゼロ** → Complexity Tracking 不要。

---

## Project Structure

### Documentation (this feature)

```text
specs/101-recency-weighting/
├── spec.md            # 完了
├── plan.md            # 本ファイル
├── research.md        # Phase 0
├── data-model.md      # Phase 1
├── contracts/
│   ├── recency-weight.md    # 重みの定義・正規化・日付衛生
│   └── weight-scope.md      # 重みをどこに適用するか(US2)
├── quickstart.md      # Phase 1
├── codex-review.md    # 完了(採用 10 / 部分採用 1 / 不採用 1)
├── gate-config.json   # Phase B で凍結
└── tasks.md           # Phase 2
```

### Source Code (repository root)

```text
training/src/horseracing_training/
├── recency.py          # 新規: 重みの計算(純関数)・正規化・日付衛生・ESS
├── predictor.py        # US1: fit 経路に重みを渡す(079 の seam の隣)
├── recipe.py           # US1: ModelRecipe にフィールド追加
├── calib_split.py      # US1: arm E builder の _RECIPE_FIELD_DISPOSITION に "forward" 登録
├── target_encoding.py  # US2: fit_target_encoder / oof_target_encode に重みを通す
└── calibration.py      # US2: fit_calibrator に重みを通す

scripts/
└── recency_gate.py     # 判定 driver(凍結 gate-config を読んで両アームを回す)

tests/                  # training/tests/unit・integration
```

**Structure Decision**: `training/` に閉じる。判定のために `eval/` を読むが**変更しない**
(feature 100 で入れた per-race 証拠がそのまま使える)。US1 が REJECT なら `serving/` は触らない。

---

## Phase 構成(判定を中断点にする)

```
Phase A: 重みの計算と凍結
   recency.py(純関数)・日付衛生・正規化・ESS。半減期を日付基準で決めて凍結。
   ★ ラベルを一度も見ずにここまで終わらせる ★
   ↓
Phase B: US1 の配線と判定  ★★★ 中断点 ★★★
   predictor/recipe/calib_split に通し、両アームを事前登録ゲートで判定。
   ↓ (ADOPT / 有望 の場合のみ)
Phase C: US2 重みの適用範囲(TE / 校正器)
   booster 限定と一貫適用の**両方を測る**。どちらを採るかは測定で決める。
   ↓
Phase D: US3 交絡の見分け(疑似 cutoff)
   ↓
Phase E: 後始末(REJECT なら非結線保全 / ADOPT なら serving 結線)
```

**Phase A でラベルを見ない**のが要点である。半減期を日付だけで決めるので、Phase A が終わった
時点で「選択リークが無いこと」が構造的に確定する。Phase B で初めてラベルに触れる。

**Phase B を中断点にする理由**: US2 は TE と校正器に重みを通す**中〜大の実装**である。
US1 が REJECT なら丸ごと不要になる。feature 100 で「理屈のまま spec に載せた US2 が測定で
死んだ」前例があるので、同じ形を繰り返さない。

**REJECT 時の後始末**: 062/070/090/100-US3 と同じ非結線保全。`recency.py` とテストは残し、
結線・レシピフィールドは revert する。

---

## 主要な設計判断(research.md に詳細)

1. **正規化は非交渉**(FR-006a)。重み総量が LightGBM の正則化(`lambda`・leaf 条件・Hessian
   閾値・early stopping)を動かすので、レース平均 1 に正規化しないと「時間重み」と「容量変更」が
   区別できない。このリポジトリは容量だけで −0.0067 を出しているので実際に起こりうる。
2. **半減期は日付だけで事前登録**(FR-007)。nested 選択は採らない — estimand が変わり、かつ
   nested の結果から固定すると同じ outer 結果を根拠に使えなくなる。
3. **評価は無重み**(FR-011a)。目的は「将来レース当たり平均 winner NLL」。
4. **US2 は両方測る**(FR-015)。booster 限定と一貫適用のどちらが良いかを理屈で決めない。
5. **交絡は完全には識別できない**(FR-017b)。並行取得が無いので、US3 が言えるのは
   「どちらがより説明するか」まで。

---

## Complexity Tracking

Constitution 違反なし。記入不要。
