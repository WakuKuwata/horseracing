# Quickstart: 097 early-mid pace の検証手順

前提: ローカルスタック稼働(`scripts/stack.sh`・DB は localhost:15432)。

## 1. 構築の検証(Phase A 完了後)

```bash
cd features && uv run pytest tests/unit/test_early_mid_pace_features.py -q
```

共有列パリティ(SC-001)と充足監査(SC-002):

```bash
cd training && uv run python ../scripts/097_parity_and_coverage.py
```

期待: `shared 138 columns: byte-identical (mismatch 0)` / `2026 coverage (has_past_race):
>= 95%`。

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

期待する出力の形:

```
cutoff 2019-01-01: masked build OK / symmetry OK / diff=…
cutoff 2021-01-01: …
cutoff 2023-01-01: …
pooled: point=… total CI[…, …]
guard1 full-info: … / guard2 real-window: …
VERDICT = ADOPT|REJECT
```

## 3. 後始末(Phase C)

- REJECT: revert 後に `uv run python -m horseracing_serving predict --race-id <id>` の出力が
  revert 前とバイト一致(SC-005)、保全テストは
  `uv run pytest tests/unit/test_early_mid_pace_features.py -q`(build 直呼び)で緑。
- ADOPT: 実データ学習 → `register-arm-e --verdict ../specs/097-early-mid-pace/verdict.json …`
  で候補登録。昇格はしない(contracts/adoption-gate.md)。

## SC 対応表

| SC | 検証コマンド |
|---|---|
| SC-001/002 | `097_parity_and_coverage.py` |
| SC-003 | 単体テスト(リーク 3 方向) |
| SC-004 | verdict.json の hash 3 点 + 判定式 |
| SC-005 | REJECT 分岐の予測バイト一致 |
| SC-006 | `specs/097-early-mid-pace/verdict.json`(追跡パス) |
