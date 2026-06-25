# Quickstart: 人気-不人気バイアス補正の検証

実装後に「校正器学習 → q→q' 補正 → 勝率校正/乖離評価」が動くことを確認する手順。

## 前提

- Feature 009/010/011/012 が適用済み。`probability` を拡張、`betting` が opt-in 利用。
- 実 DB（2007+、race_horses.odds + race_results）。012 で `exotic_odds` があれば乖離比較も可。

## セットアップ

```bash
cd probability && uv sync
export DATABASE_URL=postgresql+psycopg://aiuma:aiuma@localhost:15432/horseracing
```

## 校正器の学習（US1、walk-forward 厳密前）

```bash
uv run python -m horseracing_probability fl-fit --train-from 2007-01-01 --train-to 2007-12-31 --method power
```

期待: べき乗 `q'∝q^γ` の γ（勝者尤度 MLE）・学習窓・サンプル数・q 範囲が表示。学習は対象期間のレースのみ（評価期間と非重複）。

## 補正適用（US2、ライブラリ）

```python
from horseracing_probability.fl_bias import fit_fl_calibrator, apply_calibrator
from horseracing_probability.market_odds import estimate_market_odds
# cal = fit_fl_calibrator(train_samples, method="power")
cp = apply_calibrator(cal, {"A": 2.0, "B": 4.0, "C": 8.0})   # cp.q_prime: Σ=1, q→q' 単調
eo = estimate_market_odds({"A": 2.0, "B": 4.0, "C": 8.0}, calibrator=cal)  # 補正済み推定オッズ(opt-in)
```

期待: `cp.q_prime` は Σ=1・単調・エンジン整合。`eo` は q' 由来で生オッズを厳密復元しない（バイアス除去）。補正無効（calibrator=None）
は生 q の従来挙動。

## 評価（US3、評価先行）

```bash
uv run python -m horseracing_probability fl-evaluate \
  --train-from 2007-01-01 --train-to 2007-12-31 --eval-from 2008-01-01 --eval-to 2008-12-31 --method power
```

期待: **第一指標** q vs q' の NLL/Brier/ECE（人気帯別、サンプル数併記）が並び、`improved` で採否を判断。**補助** 012 乖離の補正前後
（coverage/log 比 MAE/P90）が診断表示。全出力が疑似評価・採否=勝率校正と明示。

## テスト

```bash
cd probability
uv run pytest tests/unit       # 正規化後校正・単調・Σ=1・γ MLE 決定論・ECE 固定ビン・小帯・同着除外・エンジン整合
uv run pytest -m integration   # 実 DB で walk-forward 厳密前・q vs q' 改善・乖離前後比較
cd ../betting
uv run pytest tests/unit       # リーク・ガード: q'/odds が win モデル特徴に入らない
```

検証する受け入れ基準:

- **SC-001**: 校正器が walk-forward で学習され、q' が各馬で単調・レース内 Σ=1、学習に評価対象レースの結果を使わない。
- **SC-002**: 補正は q のみ・p 非参照、オッズ/q' がモデル特徴に使われない（p≠q、リーク・ガード）。
- **SC-003**: q' が 009/010 に渡り 011/012 の EV が q' 由来（opt-in、無効時は後方互換）。補正後の推定単勝オッズは生オッズを厳密復元しない。
- **SC-004**: q vs q' の勝率校正（NLL/Brier/ECE、人気帯別）が算出され改善/悪化が定量化（採否ゲート）。
- **SC-005**: 補正前後の推定 vs 実 exotic 乖離が比較される（補助・診断）。
- **SC-006**: 方式設定可能、方式/γ/学習窓/サンプル数が再現メタに記録。決定論。
- **SC-007**: 全出力が疑似評価明示。小サンプル人気帯はサンプル数併記。

## 核心の考え方（CRITICAL / リーク境界）

校正は **per-horse の生 `g(q)` ではなく、レース正規化後 `q'=g(q)/Σg(q)`** を学習・評価対象にする（再正規化が marginal を変えるため）。
正準はべき乗 `q^γ`（γ を勝者尤度で学習）。`q'` は**市場由来**でモデル p とは別物、オッズ/q' は **win モデル特徴に一切使わない**。
学習は **walk-forward 厳密前**、方式選択も学習窓内（選択リーク防止）。採否は**実現勝率校正**で判断し、実 exotic 乖離は補助診断に留める。
