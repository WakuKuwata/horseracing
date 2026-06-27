# Quickstart: モデル確率校正と edge haircut (017)

end-to-end 検証ガイド。詳細は [contracts/](contracts/) と [data-model.md](data-model.md) 参照。

## 前提

- ローカル DB（[[local-db-setup]]）: `DATABASE_URL=postgresql+psycopg://aiuma:aiuma@localhost:15432/horseracing`
- 016 までの Kelly 推奨/backtest が動作（recommendations.stake_fraction = migration 0006 適用済み）。
- **スキーマ変更なし**（本 feature は migration を追加しない）。

## 検証シナリオ

### 1. 校正器の学習・評価（US1 / SC-001,002,003,005）

```
cd probability && DATABASE_URL=... uv run probability calibrate-eval \
  --from 2008-01-01 --to 2008-12-31 --method power --min-races 50
```

期待:
- 生 p / 校正 p' の NLL・Brier・ECE・reliability（overall + 人気帯別）が walk-forward out-of-sample で出力。
- overconfidence 指標（reliability slope / 上位帯 over-under / cal-in-large）と同着除外件数。
- 009 後の券種別 reliability（exacta/trifecta）before/after。採用判定 = NLL/Brier 改善 + joint 非悪化。
- 2 回実行で完全一致（決定論）。

### 2. 校正 + haircut Kelly 推奨（US2 / SC-010）

```
cd betting && DATABASE_URL=... uv run python -m horseracing_betting kelly-recommend <race_id> \
  --p-calibrator fit --haircut-type relative --haircut 0.05 --bankroll 100
```

期待:
- logic_version に `pcal=power(p^gamma);gamma=...;haircut=rel:0.05;base_mv=...` が記録（再現可能）。
- 生 p 経路は後方互換（--p-calibrator 無指定で従来どおり）。

### 3. Kelly 比較（raw / cal / cal+haircut）+ 2×2（US2/US3 / SC-006,007,008,009）

```
uv run python -m horseracing_betting kelly-calibration-compare \
  --from 2008-06-01 --to 2008-06-30 --modes raw,cal,cal+haircut --haircut 0.05
```

期待:
- mode 別の 6 指標（終端 bankroll・対数成長率・最大DD・破産確率・分散・最大連敗）。
- 校正+haircut が生 Kelly 比で最大DD・破産確率を下げ（過大賭け低減）、成長低下が過剰でない。
- success = 校正改善 かつ Kelly リスク非悪化（逆転ケースは明示）。2×2 で二重補正の edge 過縮小を検出。

## リーク境界チェック（SC-002）

```
cd betting && uv run pytest -k "leak or calib"   # p'/haircut/edge_adj/fraction が features/training に出ない
cd probability && uv run pytest -k "leak or walk"  # 校正器が対象レース結果を読まない・選択も窓内
```

## 受け入れ

- pytest（合成データ）: 校正 fit/apply・walk-forward・joint reliability・haircut・2×2・決定論・リーク・
  小データ fallback。
- 実 DB スモーク: calibrate-eval（校正品質）+ kelly-calibration-compare（生 vs 校正+haircut）を目視。
