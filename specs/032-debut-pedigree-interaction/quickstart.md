# Quickstart: 低履歴×血統適性 交互作用 (032)

実 DB は分離 Postgres(`postgresql+psycopg://aiuma:aiuma@localhost:15432/horseracing`)。レガシー `aiuma` DB は触らない。

## 1. ユニットテスト(network-free)

```bash
cd features
uv run ruff check src tests
uv run pytest -q
uv run pytest tests/unit/test_debut_pedigree_features.py tests/unit/test_debut_pedigree_leak.py -q
```

期待: デビュー戦集約(自馬除外・strictly-before)・ゲーティング積・NaN 伝播・float64・leak-guard が緑。

## 2. 実 DB materialize パリティ(非交渉)

```bash
export DATABASE_URL="postgresql+psycopg://aiuma:aiuma@localhost:15432/horseracing"
cd features
uv run python -m horseracing_features materialize --artifacts-dir ../artifacts   # features-010
uv run pytest tests/unit/test_materialize_core.py -q
```

期待: manifest が features-010、debut_pedigree 5 列が materialized_columns に収録、materialize==in-memory が bit 一致(float64)。

## 3. カバレッジ確認(実 DB)

sire_debut_win_rate の非 null 率(デビュー戦母数が min_starts を満たす種牡馬)、ゲーティング列の非 null 率(デビュー馬/低履歴馬で非ゼロ)を確認。

## 4. 採用判定(事前登録 walk-forward OOS)

```bash
cd training
uv run python -m horseracing_training feature-eval --drop-groups debut_pedigree
# 診断(採否に使わない): ablation 群寄与 / market edge(デビュー馬セグメント)
uv run python -m horseracing_training feature-ablation --groups debut_pedigree
uv run python -m horseracing_training feature-diagnostic
```

期待: AdoptionReport が primary(win LogLoss 改善 かつ ECE 非悪化)+ fold ガードで adopted を機械判定。全体ゲインはデビュー馬の出走比(~10.5%)に希釈されうるので、SECONDARY でデビュー馬セグメントの効果を記録。

## 5. 採用なら serving 再学習・昇格

```bash
cd training
uv run python -m horseracing_training train-evaluate \
  --model-version lgbm-032 --baseline baseline-uniform-v1 --artifacts-dir ../artifacts
```

期待: adopted=active なら lgbm-032 を active 昇格・lgbm-031 retired(feature_hash=features-010 整合)。不採用ならブランチ保全(main は features-009/lgbm-031 のまま)。

## 完了条件

- [ ] features lint/test 緑、eval/training/serving 既存テスト透過で緑
- [ ] 実 DB materialize parity bit 一致(features-010)
- [ ] leak-guard 全通過
- [ ] 事前登録 OOS で採否が機械判定され research に記録(+ デビュー馬セグメント診断)
- [ ] 採用時は lgbm-032 active 昇格(または不採用でブランチ保全)
