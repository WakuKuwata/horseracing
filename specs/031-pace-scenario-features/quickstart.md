# Quickstart: 展開・ペース構成特徴 (031)

実 DB は分離 Postgres(`postgresql+psycopg://aiuma:aiuma@localhost:15432/horseracing`)。レガシー `aiuma` DB は触らない。

## 1. ユニットテスト(network-free)

```bash
cd features
uv run ruff check src tests
uv run pytest -q                       # 全 features テスト(新規 pace_scenario 含む)
uv run pytest tests/unit/test_pace_scenario_features.py tests/unit/test_pace_scenario_leak.py -q
```

期待: leave-one-out 集計・相互作用・coverage・NaN 伝播・float64・leak-guard(自馬今走/他馬今走/同日/未来 不変 + grep)が緑。

## 2. 実 DB materialize パリティ(非交渉)

```bash
export DATABASE_URL="postgresql+psycopg://aiuma:aiuma@localhost:15432/horseracing"
cd features
uv run python -m horseracing_features materialize --artifacts-dir ../artifacts   # features-009 生成
uv run pytest tests/unit/test_materialize_core.py -q                              # parity bit 一致
```

期待: manifest が features-009、pace_scenario 7 列が materialized_columns に収録、materialize==in-memory が bit 一致(float64)。

## 3. カバレッジ確認(実 DB)

pace_scenario 列の非 null 率を確認(脚質判明=過去走の running_style 由来なのでデビュー馬・古いレースで NaN 増)。`field_style_coverage` の分布で Unknown 量を把握。

## 4. 採用判定(事前登録 walk-forward OOS)

```bash
cd training
# baseline=features-008(pace_scenario drop) vs candidate=features-009(full)
uv run python -m horseracing_training feature-eval --drop-groups pace_scenario

# 診断(採否に使わない): ablation 群寄与 / market edge
uv run python -m horseracing_training feature-ablation --groups pace_scenario
uv run python -m horseracing_training feature-diagnostic
```

期待: AdoptionReport が primary(win LogLoss 改善 かつ ECE 非悪化)+ fold ガード(strict majority・worst-fold ECE 2e-3・worst-fold dLogLoss 5e-3)で adopted を機械判定。

## 5. 採用なら serving 再学習・昇格

```bash
cd training
uv run python -m horseracing_training train-evaluate \
  --model-version lgbm-031 --baseline baseline-uniform-v1 --artifacts-dir ../artifacts
```

期待: adopted=active なら lgbm-031 を active 昇格・lgbm-030 retired(feature_hash=features-009 整合)。serving が lgbm-031 を自動ロード。不採用ならブランチ保全(main は features-008/lgbm-030 のまま)。

## 完了条件

- [ ] features lint/test 緑、eval/training/serving 既存テスト透過で緑
- [ ] 実 DB materialize parity bit 一致(features-009)
- [ ] leak-guard 全通過
- [ ] 事前登録 OOS で採否が機械判定され research に記録
- [ ] 採用時は lgbm-031 active 昇格(または不採用でブランチ保全)
