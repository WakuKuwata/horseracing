# Contract: speed figure features(061)

API/OpenAPI/DB スキーマ不変。内部不変条件:

## INV-F1: as-of 境界(基準タイム側)

任意の行の speed_figure 列は、対象レース日より strictly-before のデータのみから決まる。
同日・今走・未来のレースのタイムを変更しても値は不変。過去走タイムの変更では変化する(正の対照)。
基準タイム統計自体も同じ境界(未来レースの追加で過去行の基準が変わらない)。

## INV-F2: additive バイト不変

features-016 build の既存共有列は features-015 build と check_exact + check_dtype で一致。

## INV-F3: materialize 安全

speed_figure 列は per-race 決定的・pool-end 非依存。materialize 経路と in-memory 経路の出力が bit 一致。

## INV-F4: 欠損規律

標本不足セル・履歴なし馬・finish_time 欠損は NaN(0 埋め禁止)。カバレッジ(非 NaN 率)をレポート。

## INV-F5: serving 互換

features-016 registry 下で lgbm-057(features-014)・lgbm-058-acc / lgbm-060-mkt(features-015)が
compat-path でロードされ、lgbm-057 の予測が persisted 値とバイト一致。ピン hash 不一致は fail-closed。

## 事前登録採用ゲート(変更禁止、020/023 同型)

フル walk-forward binary feature-eval、baseline = features-016 から speed_figure 群を drop(=features-015 相当):

1. mean win LogLoss 改善 — MUST
2. mean ECE 非悪化(tol 1e-3)+ worst-fold ECE(tol 2e-3)
3. strict majority(勝ち fold × 2 > fold 数)+ worst-fold LogLoss 上限(5e-3)

通過後: production 構成(pl_topk+TE+isotonic)で lgbm-061 を再学習し、現 active(lgbm-057)比で
win/top2/top3 LogLoss・win ECE の全指標非悪化を確認。active 昇格は最終的にユーザー判断。

## Spike go/no-go(事前登録、codex 反映で強化)

直近 3-4 fold で (1) binary feature-eval の win LogLoss 改善 **かつ** (2) pl_topk 少数 fold の非悪化
(ゲイン幅に関わらず pl_topk 確認は必須 — 059 の縮小前例は絶対軸にも起こり得る)。
no-go 時は std 非依存の秒/100m 正規化(登録済みフォールバック)を 1 回だけ試行 → なお不発なら中断・記録。
