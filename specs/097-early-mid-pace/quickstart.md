# Quickstart: 097 early-mid pace の検証手順

前提: ローカルスタック稼働(`scripts/stack.sh`・DB は localhost:15432)。

## 1. 構築の検証(Phase A 完了後)

```bash
cd features && uv run pytest tests/unit/test_early_mid_pace_features.py -q
```

共有列パリティ(SC-001)と充足監査(SC-002):

```bash
cd training && uv run python ../scripts/parity_097.py && uv run python ../scripts/coverage_097.py
```

期待: `shared 138 columns: byte-identical (mismatch 0)`(→ evidence-parity.md)/
`2026 coverage (has_past_race): >= 95%`(→ evidence-coverage.md)。

## 2. 判定(Phase B)

gate-config の凍結確認(hash が specs の記録と一致すること):

```bash
cd training && uv run python -c "
import json
from horseracing_eval.decision import gate_config_hash
cfg = json.load(open('../specs/097-early-mid-pace/gate-config.json'))
print(gate_config_hash(cfg))"
```

シミュレーションゲート実行(~4.5h 見込み: マスク 3 本 ≈2h + full-info guard ≈2.4h):

```bash
cd training && PYTHONUNBUFFERED=1 nohup uv run python \
  ../scripts/097_simulated_supply_gate.py \
  --gate-config ../specs/097-early-mid-pace/gate-config.json \
  --gate-config-hash $(cat ../specs/097-early-mid-pace/gate-config.hash.txt) \
  --json ../specs/097-early-mid-pace/verdict.json > ../out/097-gate.log 2>&1 &
```

期待する出力の形(**出力規律**: 全成分が揃うまで効果数値は出ない):

```
cutoff 2019-01-01: mask OK / single-load OK / symmetry OK / provenance OK / fit OK
cutoff 2021-01-01: …
cutoff 2023-01-01: …
guard1 full-info: 3 windows fit OK
guard2 real-window: fit OK
--- all components computed ---
pooled: point=… sample CI[…, …] total CI[…, …]
guard1: … / guard2: …
VERDICT = ADOPT|REJECT|NO_DECISION(実行不能のみ)
```

## 3. 後始末(Phase C)

- REJECT: revert 後に `uv run python -m horseracing_serving predict --race-id <id>`(**cwd=serving/**)の出力が
  revert 前とバイト一致(SC-005)、保全テストは
  `uv run pytest tests/unit/test_early_mid_pace_features.py -q`(build 直呼び)で緑。
- ADOPT: 実データ学習 → `register-arm-e --model-version lgbm-097-emp --n-estimators 900
  --weight-mask-rate 0.5 --weight-mask-seed 20260810 --n-oof-blocks 8 --seed 42
  --artifacts-dir <絶対パス>`(gate-config `arms.recipe` と一致・`--verdict` 引数は無い)→
  `promote-model --model-version lgbm-097-emp --verdict ../specs/097-early-mid-pace/verdict.json`
  (**--apply 無し=dry-run**)。期待結果は `error: … verdict_artifact_not_eligible` + **exit 1**
  (失敗ではなく構造的拒否の証明)。昇格はしない。

## SC 対応表

| SC | 検証コマンド |
|---|---|
| SC-001/002 | `parity_097.py` / `coverage_097.py` |
| SC-003 | 単体テスト(リーク 3 方向) |
| SC-004 | verdict.json の hash 3 点 + 判定式 |
| SC-005 | REJECT 分岐の予測バイト一致 |
| SC-006 | `specs/097-early-mid-pace/verdict.json`(追跡パス) |
