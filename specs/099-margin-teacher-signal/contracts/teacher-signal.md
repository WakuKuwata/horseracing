# Contract: margin-aware 教師信号(INV-MT1..MT9)

対象: `training/src/horseracing_training/`(cond_logit / win_model / predictor / dataset /
recipe / cli)。features・serving・betting・api・db は本契約の対象外(無変更)。

## Objective 契約

- **INV-MT1(OFF の完全不変)**: `pl_topk_objective(gsizes, ranks, offsets=None,
  stage_scales=None)` は現行実装と勾配・ヘシアンが**ビット一致**する(`np.array_equal`)。
  表明は **offsets 有無 × sample weight 有無の 4 象限**で行う(spike 版は offsets を
  持たないため、素朴な移植は market-offset 対応を失う)。spike selftest の
  3 表明(all-ones 一致 / 一様 0.5 = 厳密半分 / ステージ限定変調)を production 単体
  テストとして移植する。loop oracle(`_pl_topk_objective_loop`)にも同引数を追加し、
  既存の vectorized↔loop 等価性テストの網に stage_scales 有りケースを加える
- **INV-MT4(規則不変)**: 変調はステージ重みへの乗算のみ。ステージ発火条件
  (一意 target・remaining≥2)・group 中立化(sum(y)≠1)・break 規則は変調の有無で
  一切変わらない(バグ再導入テスト: 変調下で dead-heat group の勾配が 0 のまま)

## データ供給契約

- **INV-MT2(ラベル側のみ)**: `margin_scale_s2` / `margin_scale_s3` は
  `model_input_features()` に現れず、`feature_hash` を変えず、`feature_snapshots` に
  書かれない(leak-guard テスト。`MKT_ODDS` の INV-M1/M3 と同型)
- **INV-MT3(レース内定数と値の健全性)**: aux 列はレース内で単一値・値域 [0.25, 1.0]・
  有限・s1==1。fit 時に **`ValueError`** で検証してから group 先頭行を取り出す(`assert`
  は `-O` で消える・検証なしの先頭行は 1 行の破損を隠す)
- **INV-MT7(run 1 バグ形の回帰)**: margin 算出は「LEAD を全完走馬で計算 → 着順 1..3 に
  制限」の順。単体テスト: 完走 4 頭のミニレースで s3 が手計算値(<1.0)に一致し、完走
  3 頭では s3=1.0。統合テスト(実 DB): s2/s3 のスケール平均がともに実質 1.0 未満
  (≈0.66-0.69 帯)— run 1 の形(s3≈1.0)なら赤
- **INV-MT8(中立の意味論)**: margin 未定義(次馬なし・時計欠損・表に不在)はスケール
  1.0。行の除外・NaN 伝播はしない(教師信号の重みであって特徴量ではない)

## レシピ / 同一性契約

- **INV-MT5(hash back-compat・両 Factory)**: hash 用 canonical payload は `ModelRecipe`
  に一本化し、`RecipeFactory` と `CalibSplitFactory` の**両方**が同じ payload を hash する。
  `margin_teacher=None` は payload から省略され、**holdout 系・arm E 系の既存 hash が
  1 つも変わらない**(両系の既存 hash 値スナップショットテスト。現行の Factory 直 `meta()`
  hash のままではフィールド追加だけで arm E 系が全滅する)。`"v1"` は distinct。受理値は
  None/"v1" のみ(fail-closed)
- `CalibSplitFactory._RECIPE_FIELD_DISPOSITION` に `margin_teacher: "forward"` を追加し、
  **`_make_base()` で `margin_teacher=recipe.margin_teacher` を明示的に渡す**(disposition
  は会計検査であって配線ではない。渡し忘れは booster が教師信号だけを無視して正常学習する
  黙殺 — shared-matrix 経路含む統合テストで固定)。disposition 漏れは既存の
  `_check_recipe_fields_accounted_for` が ArmNotServable(この fail-closed もテストで確認)
- CLI spec 文字列: `mteach=v1` セグメント。未知セグメントの黙殺は既存の綴り検査に従う

## 予測 / serving 契約

- **INV-MT6(予測経路不変)**: margin / スケールは fit でのみ読まれる。`WinModel.predict`
  / serving `predict_race` は無変更。実 DB E2E: 既存 active モデルの予測がマージ前後で
  バイト一致(SC-004)

## 監査契約

- **INV-MT9(統計の存在と不変)**: ON のとき fit_info / metadata に `margin_teacher`
  ブロック — variant / m0 / gmin に加え、**実際の booster fit 行に対する**ステージ別
  source_available(margin 計算可)/ scale_lt1(実際に減衰)/ fire_and_lt1(発火かつ減衰)
  件数と fireable 平均(scale=1.0 の「大差 cap」と「時計欠損中立」を分計)。OFF のとき
  key 不在で metadata はバイト不変
