# Quickstart: Kelly 賭け金最適化 (016)

end-to-end 検証ガイド。詳細は [contracts/](contracts/) と [data-model.md](data-model.md) 参照。

## 前提

- ローカル DB（[[local-db-setup]]）: `DATABASE_URL=postgresql+psycopg://aiuma:aiuma@localhost:15432/horseracing`
- 011/012 までの推奨パスが動作（P_model=009、実/推定オッズ）。
- migration **0006**（recommendations.stake_fraction）適用済み。

## セットアップ

```
cd db && DATABASE_URL=... uv run alembic upgrade head   # 0006 まで
```

## 検証シナリオ

### 1. 実オッズ Kelly 推奨（US1 / SC-001,002,003）

```
cd betting && DATABASE_URL=... uv run betting kelly-recommend <race_id> --bankroll 100 --allocation exact
```

期待:
- 各採用買い目に `stake_fraction`（λ·cap·配分後）と stake = fraction×bankroll。
- 負 edge は不保存。Σ stake_fraction(券種) ≤ cap_total。同一入力 2 回で完全一致（決定論）。

### 2. 推定オッズ二重疑似（US3 / SC-004,005）

```
uv run betting kelly-recommend <race_id_without_real_exotic> --bankroll 100
```

期待:
- 実 exotic 無しの買い目は `is_estimated_odds=true`・double_pseudo（API 導出）で標識。
- 推定経路は λ_est(0.10) 適用で実(0.25)より保守的（stake_fraction が同等以下）。

### 3. bankroll backtest（US2 / SC-006,007,008）

```
uv run betting kelly-backtest --from <race_id> --to <race_id> --bankroll 100 --compare flat
```

期待:
- Kelly / flat の 6 指標（終端 bankroll・対数成長率・最大DD・破産確率・分散・最大連敗）が両戦略で算出。
- 実区間 / 二重疑似区間が分離集計。success はリスク調整後成長で判定（ROI>1 単独ではない）。
- 破産確率は walk-forward 実経路 + block bootstrap。

## リーク境界チェック（SC-010）

```
cd betting && uv run pytest -k "leak or feature"   # stake_fraction/odds/q が features/training に出現しない
```

## 受け入れ

- pytest（合成データ）: Kelly 式・相互排他配分・推定抑制・破産確率・分離集計・決定論・リーク。
- 実 DB スモーク: 実オッズあるレースで Kelly 推奨生成 + 短期間 backtest が flat と比較表示。
