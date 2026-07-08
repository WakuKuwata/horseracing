# Contract: rating features(062)

API/OpenAPI/DB スキーマ不変。内部不変条件:

## INV-R1: as-of 境界(逐次状態)

任意の行の rating 列は、対象レース日より strictly-before のレース結果のみで更新された状態から決まる。
今走・同日他レースの結果を変更しても値は不変。過去レースの結果を変更すると変化する(正の対照)。

## INV-R2: pool-end 非依存(materialize 安全の要)

未来レースを追加しても過去行の rating 列は不変(1 パス累積状態が未来を見ない)。
逐次状態のため 061 までの per-row 独立特徴より検証が厳格 — 専用テストで機械固定。

## INV-R3: 決定論 + materialize parity

同一データで 2 回 build して content_hash 一致。in-memory build と materialized parquet の rating 列が bit 一致。

## INV-R4: additive バイト不変

features-017 build の既存共有列は features-016 build と check_exact + check_dtype で一致。

## INV-R5: 欠損規律

初出走は固定初期レーティング(0 埋めでない事実としての初期値)。starts=0.0 で信頼度を明示。
履歴不足の delta は NaN。

## INV-R6: レーティング正しさ

既知の小規模対戦データで、一貫して勝つ馬のレーティングが上がり、負ける馬が下がる(SC-006)。

## INV-R7: serving 互換

features-017 registry 下で lgbm-061(features-016)・lgbm-058-acc/lgbm-060-mkt(features-015)が
compat-path でロードされ、lgbm-061 の予測が persisted 値とバイト一致。ピン hash 不一致は fail-closed。

## 事前登録採用ゲート(変更禁止、020/023/061 同型)

フル walk-forward binary feature-eval、baseline = features-017 から rating 群を drop(=features-016 相当):

1. mean win LogLoss 改善 — MUST
2. mean ECE 非悪化(tol 1e-3)+ worst-fold ECE(tol 2e-3)
3. strict majority + worst-fold LogLoss 上限(5e-3)

通過後: production 構成(pl_topk+TE+isotonic)で lgbm-062 再学習、lgbm-061 比で全指標非悪化。active 昇格はユーザー判断。

## Spike go/no-go(事前登録)

(1) 小規模データで INV-R6 レーティング正しさ + INV-R2/R3 materialize 決定性・pool-end 非依存。
(2) 実 DB 直近 fold で binary feature-eval 改善 かつ pl_topk group-marginal 非悪化(Elo は既存能力と
重複しうるため pl_topk 確認必須 — 061 教訓)。no-go は中断・記録。
