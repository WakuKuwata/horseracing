# Quickstart: モデル改善 — 特徴量拡張 (020)

end-to-end 検証ガイド。詳細は [contracts/feature_eval.md](contracts/feature_eval.md) と
[data-model.md](data-model.md) 参照。

## 前提

- ローカル DB（[[local-db-setup]]）に JRA-VAN 2007+ 取込済み、現行 active model（baseline）あり。
- **スキーマ変更なし**（migration 追加しない）。feature_version=features-005 を bump。

## 検証シナリオ

### 1. リーク安全性（US1 / SC-001/002/003）

```
cd features && uv run pytest -k "cutoff or leak or target_row"
```

期待: 各新特徴に cutoff テスト（当日以降のデータ変更で特徴量不変）、跨馬（jockey/trainer）に target-row 除外
テスト（対象行・同日結果変更で統計不変）、新馬で 0 代入が起きない（Unknown）。

### 2. fold 内選択の walk-forward 採用評価（US2 / SC-004/005/006）

```
cd eval && DATABASE_URL=... uv run eval feature-eval --from 2008-01-01 --to 2008-12-31 --seed 42
```

期待: fold 別 + 平均の LogLoss/Brier/AUC/ECE（new vs baseline）、勝ち fold 数・最悪 fold・ECE 差分、PRIMARY
判定（LogLoss 改善 かつ ECE 非悪化）。選択・ハイパラが fold 学習窓内で完結（選択リーク無し）。同一 seed で再現。

### 3. group ablation（US2 / SC-007）

```
uv run eval feature-ablation --from 2008-01-01 --to 2008-12-31 \
  --groups recent_form,aptitude,race_condition,human_form
```

期待: group 単位の寄与（LogLoss 差）が分離報告され、horse form と human form の寄与が判別できる。

### 4. SECONDARY diagnostic（US3 / SC-008）

```
uv run eval feature-diagnostic --from 2008-01-01 --to 2008-12-31
```

期待: pseudo-ROI/Kelly（高分散・参考）+ 市場 q edge（p−q calibration / edge bucket 実現勝率 / q 条件付き
LogLoss）。主採用ゲートにしない旨が出力に明示。

## 受け入れ

- pytest（合成＋実 DB）: cutoff/target-row 除外・fold 内選択・採用ゲート（LogLoss+ECE+fold 別差分）・group
  ablation・決定論・スキーマ無変更。
- 採用は OOS 改善が gate（baseline 未超過なら不採用）。market odds/結果が特徴に出ない（leak-guard）。
