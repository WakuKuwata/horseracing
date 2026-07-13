# Quickstart: 評価契約 + 校正分割

**Feature**: 068 | **Date**: 2026-07-12

本feature が end-to-end で動くことを示す検証手順。詳細な列・数式は [data-model.md](data-model.md)・[contracts/cli.md](contracts/cli.md) を参照。

## 前提

- ローカル Postgres（[local-db-setup]: `docker-postgres-1`、DB `horseracing`）に 2007–2026 の学習データが投入済み。
- artifact: 現 DB-active は `lgbm-063`（features-017, pl_topk+isotonic、lgbm-062 と recipe 共有＝絶対パス再学習版、[weights-uri-relative-path-ops-bug]）。SC-001 の demo は歴史比較 `lgbm-062 vs lgbm-061`（A–D の運用 baseline=db-active とは別）。いずれも `artifacts/model_versions/` に存在。
- eval は predictor-agnostic を維持（CLI が `LightGBMPredictor` を注入）。

## SC-001: 既存2モデルの paired 再評価

物差しの是正が動くことを、既存モデル同士の paired 評価で示す。

```
uv run --project training training paired-eval \
  --candidate lgbm-062 --active lgbm-061 \
  --seed 20260712 --json /tmp/paired_062_vs_061.json
```

**期待**:
- 両モデルが同一 race 集合・同一 fold で再評価され、`race_id_set_hash` が一致（不一致なら fail-closed）。
- レポートに winner NLL（candidate/active/diff）・started-all LogLoss/Brier・finished-only（互換）・equal-mass ECE・block bootstrap 95% CI・期間別（全/直近3年/5年）が揃う。
- `metrics_summary` の保存値を baseline に使っていない（両者を現DB再評価）。

## SC-002: 決定論

```
# 同一 seed・単一スレッドで2回実行（決定論は num_threads=1 が前提）
uv run --project training training paired-eval --candidate lgbm-062 --active lgbm-061 \
  --seed 20260712 --num-threads 1 --json /tmp/run1.json
uv run --project training training paired-eval --candidate lgbm-062 --active lgbm-061 \
  --seed 20260712 --num-threads 1 --json /tmp/run2.json
```

**期待**: winner NLL・paired 差・bootstrap CI の絶対差が `< 1e-9`（bit一致は要求しない、SC-002）。

## SC-003: 校正分割実験 A〜D

```
uv run --project training training calib-split-eval \
  --experiments A,B,C,D --derisk-recent-folds 3 \
  --seed 20260712 --json /tmp/calib_split.json
```

**期待**:
- 4条件で `feature_version`・`objective`・`seed` が同一（レポートの `fixed` で確認）。
- 各 experiment の直近fold winner NLL と go/no-go 判定が出る。
- C/D は `score_transfer_check` を持ち、悪化構成は B フォールバック理由が記録される。
- 少なくとも B/C/D のいずれかが A（70/30）に対し直近窓 winner NLL で非劣化か、CI 付きで判定できる。

## SC-005: provenance

```
# 次回学習後に metadata を確認
uv run --project training training train-evaluate --objective pl_topk --calibration isotonic ...
python -c "import json; m=json.load(open('artifacts/model_versions/<new>/metadata.json')); \
  print(m['model_fit_through'], m['train_through'], m['calib_from'], m['calib_through'])"
```

**期待**: 校正分割ありなら `model_fit_through < train_through`。`calib_from`/`calib_through` が populate。

## SC-006: 契約・境界

```
# leak-guard + 契約不変
uv run --project eval pytest eval/tests -k "leak_guard or winner_nll or bootstrap or paired"
uv run --project training pytest training/tests -k "provenance or calib_split"
# スキーマ・OpenAPI・feature_schema_hash（列名 hash）不変
git diff --stat -- '*.sql' 'api/**/openapi*' && echo "no schema/api change"
```

**期待**: 全緑。DBスキーマ・API・OpenAPI・FEATURE_VERSION・feature_schema_hash（列名 hash）に diff なし。

## テストの要点（合成データ）

- winner NLL: 同着・勝者不在・未確定レースの除外と件数 surface。
- started-all: cancel 馬が母集団に入らない（started 定義）。
- block bootstrap: 開催日ブロック・seed 記録・i.i.d. 禁止・同一 seed 決定論。
- power 校正: race-normalized p に作用し Σ=1 保存（IV）。
- paired: race_id 集合 hash 不一致で fail-closed。
- provenance: 校正分割で `model_fit_through != train_through`。
