# Quickstart: 騎手の時変切片 confirmatory (107)

## 前提

- postgres 稼働(`scripts/stack.sh status`)・materialized parquet が 2024-12-31 を被覆
  (現行: data_through 2026-08-23・features-021)
- gate-config 凍結済み(2026-09-02 再凍結=analyze C1+H2・効果数値未見)・
  **hash = `c872172a57be8a052a7ecd9e4b6492574fcd852e1f8c2845e4736e3cddac8eaa`**
  (実行時にこの値を `--gate-config-hash` へ渡す。config を触ると照合で落ちる=意図どおり)

## 実行(US1)

```bash
cd training && nohup uv run python ../scripts/jockey_tv_confirmatory.py \
  --gate-config ../specs/107-jockey-tv-intercept/gate-config.json \
  --gate-config-hash c872172a57be8a052a7ecd9e4b6492574fcd852e1f8c2845e4736e3cddac8eaa \
  --out-dir ../specs/107-jockey-tv-intercept/evidence \
  > ../out/jtv_confirmatory.log 2>&1 &
```

推定 ~60 分(spike 実績比: 2 fold×3 アーム 53 分 → 3 fold×2 アーム)。

## 検証(SC 対応)

| SC | 確認 |
|---|---|
| SC-001 | `evidence/verdict.json` に三値 decision。`paired-evidence.json` からの再計算が点推定・CI とビット一致(公式 `evidence.recompute`) |
| SC-002 | verdict.json の窓 ≤ 2024-12-31・driver の窓 assert が 2025+ 指定で落ちることをテストで確認 |
| SC-003 | `evidence/preflight.json` に fold 別 λ(raw/clamped)・騎手数・nk: 件数・カバレッジ |
| SC-004 | 実 DB E2E: active モデルの予測バイト不変(測定は何も persist しない=構造的に成立・E2E で確認) |
| SC-005 | (REJECT 時)全スイート緑・spike/driver スクリプトは scripts/ に保全 |
| SC-006 | spec.md に実測転記 |

## 判定後

- **ADOPT** → tasks の Phase 4(条件付き): training モジュール化・artifact 凍結・serving
  外部加算・候補登録・昇格ゲート([contracts/adoption.md](contracts/adoption.md))
- **REJECT / NO_DECISION** → tasks の Phase 5: spec 転記・閉鎖範囲の明記・memory 更新。
  結線差分ゼロなので revert 作業なし
