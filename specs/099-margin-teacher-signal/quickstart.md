# Quickstart: margin-aware 教師信号(099)の検証手順

前提: DATABASE_URL(ローカル postgres)・materialized parquet が最新
(`features materialize` 済み・コード hash 一致)。

**凍結 gate-config hash(完全値)**:
`d8c479dea834a22e4b27030d4558e9b1cc2e120639fbb29c32a8955331d098b7`
(`_` 始まりのキーは hash 対象外。照合はこの記録値との**完全比較** — 改変されたファイル
からの再計算は凍結の証跡にならない)

## 1. 単体(実 DB 不要)— SC-001 / INV-MT1/MT2/MT3/MT4/MT5

```bash
cd training && uv run pytest \
  tests/unit/test_margin_teacher_objective.py \
  tests/unit/test_margin_teacher_recipe.py \
  tests/unit/test_margin_teacher_alignment.py \
  tests/unit/test_margin_teacher_leak.py -q
```

期待: OFF ビット一致を offsets×weights 4 象限で(objective)/ dead-heat 中立化が変調下
でも不変 / 両系 hash スナップショット継続緑・"v1" distinct・不正 variant ValueError
(recipe)/ mask+sort 後の group スケール整列・不均一 ValueError(alignment)/ aux 列が
feature_cols/hash/snapshots に不在(leak)。

## 2. 統合(実 DB)— SC-002 / SC-004 / INV-MT3/MT7/MT8/MT9

```bash
cd training && uv run pytest tests/integration/test_margin_teacher_db.py -q
```

期待: 実データ形状で s2・s3 の fireable 平均がともに実質 1.0 未満(run 1 バグ形なら赤)/
ミニレース fixture の s3 手計算一致・3 完走で s3=1.0・時計 NULL→中立 / ON の fit_info 統計
存在・OFF のバイト不変 / 既存 active モデルの予測バイト一致。

## 3. smoke(配線のみ・事前登録外の窓・効果数値 redact)— SC-003 前段

```bash
cd training && uv run python -m horseracing_training paired-eval \
  --candidate "pl_topk:oof_isotonic:mteach=v1" --active "pl_topk:oof_isotonic" \
  --from 2016-01-01 --to 2017-12-31 --subgroups \
  --gate-config specs/099-margin-teacher-signal/gate-config.json \
  --use-materialized --materialized-path ../artifacts/features.parquet --pin-snapshot \
  --json out/099_smoke.json
```

(出力は `--json`(標準経路)。`--out` は regime 経路専用で標準経路では未消費。
`--materialized-path` を省くと fail-closed で即終了する)

(非 confirmatory + `--gate-config` = T016a の注入機構が `smoke` ブロックの低容量
n_estimators=50 を適用する)

期待: 完走・非ゼロ差レース ≥1(T017a の実行後 assert が作動)・candidate fit_info に
margin_teacher 統計・**効果数値は読まない・転記しない**(redact は運用者の規律)。
アーム spec を同一にした対照実行が実行前エラーになることも 1 回確認。

## 4. 本実行(confirmatory・十数時間・nohup)— SC-003 / SC-005

```bash
cd training && DATABASE_URL=... nohup uv run python -m horseracing_training paired-eval \
  --candidate "pl_topk:oof_isotonic:mteach=v1" --active "pl_topk:oof_isotonic" \
  --from 2019-01-01 --to 2026-08-23 \
  --confirmatory --gate-config specs/099-margin-teacher-signal/gate-config.json \
  --gate-config-hash d8c479dea834a22e4b27030d4558e9b1cc2e120639fbb29c32a8955331d098b7 \
  --subgroups \
  --use-materialized --materialized-path ../artifacts/features.parquet --pin-snapshot \
  --num-threads 1 \
  --json specs/099-margin-teacher-signal/verdict.json &
```

期待: 窓照合・hash 照合が通ってから採点開始(fail-closed)。verdict は harness の三値が
正本。完了後、pooled 点推定・標本 CI・総 CI・fold 別・校正前の全数値を spec 末尾に転記。

## 5. verdict 分岐 — SC-005 / SC-006

- **ADOPT**: candidate 登録(自動 active 化しないことを registry で確認)
- **REJECT**: 結線 revert 後に (1) 既存スイート緑 (2) `test_margin_teacher_objective.py`
  (objective 拡張と win_model 検証の保全テスト)が緑 (3) 実 DB E2E で active 予測バイト
  一致、を確認して数値転記(recipe/alignment/leak テストは結線と共に revert)
- どちらでも `git add` は**変更ファイルの明示列挙**(共有チェックアウト・`-A` 禁止)

## SC 対応表

| SC | 手順 |
|---|---|
| SC-001 | 1(ビット一致・hash 不変)+ 既存スイート無改修緑 |
| SC-002 | 2(実形状スケール統計) |
| SC-003 | 3(smoke)→ 4(本実行) |
| SC-004 | 2(leak-guard + E2E バイト一致) |
| SC-005 | 4 完了後の spec 転記 |
| SC-006 | 5 の REJECT 分岐確認 |
