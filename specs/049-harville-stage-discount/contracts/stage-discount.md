# Contract: stage 割引付き Plackett-Luce/Harville 導出 (049)

外部 API/DB 契約の変更はない(openapi 不変・migration なし)。本契約は導出関数の数学的仕様と不変量を固定する(実装・テストの正)。

## 定義

正規化済み win ベクトル p(Σp=1、clip 済み、canonical field)に対し、ステージ重み
`w2_i = p_i^λ2`, `w3_i = p_i^λ3`(λ_1=1 固定)。S2=Σw2、S3=Σw3。

- **1 着**: P(i 1着) = p_i(**不変**)
- **2 着**(i が 2 着、j が 1 着): P(i 2着 | j 1着) = w2_i / (S2 − w2_j)
- **3 着**(i が 3 着、j,k が 1・2 着): P(i 3着 | j,k) = w3_i / (S3 − w3_j − w3_k)

導出量:
- top2_i = p_i + Σ_{j≠i} p_j · w2_i/(S2−w2_j)
- top3_i = top2_i + Σ_{j≠i} Σ_{k≠i,j} p_j · [w2_k/(S2−w2_j)] · [w3_i/(S3−w3_j−w3_k)]
- exacta(i→j) = p_i · w2_j/(S2−w2_i)
- trifecta(i→j→k) = p_i · w2_j/(S2−w2_i) · w3_k/(S3−w3_i−w3_j)
- quinella/trio/wide/place = 既存どおり順序和・包含和(式変更なし、入力の exacta/trifecta が割引済み)

分母ガード: 残存質量 ≤ eps は既存 `_EPS`/floor 規律と同一(λ 導入で変更しない)。

## λ フィット(条件付き NLL、決定論)

- NLL(λ2) = −Σ_races log[ w2(2着馬) / (S2 − w2(1着馬)) ](1・2 着一意のレースのみ)
- NLL(λ3) = −Σ_races log[ w3(3着馬) / (S3 − w3(1着馬) − w3(2着馬)) ](1〜3 着一意のみ)
- λ2・λ3 は独立に golden-section(範囲 [0.1, 5.0]、tol=1e-6)。min_races=300 未満または境界張り付き → identity(λ=1)。
- フィット入力は対象より**厳密前**の (p ベクトル, 確定着順) のみ。オッズ・市場 q は不使用。

## 不変量(テスト必須)

| ID | 不変量 |
|---|---|
| INV-S1 | λ2=λ3=1.0 ⇒ 既存 `harville_topk`・`joint_probabilities` と**バイト一致**(明示分岐) |
| INV-S2 | win マージナルは λ に依らず不変(P(i 1着)=p_i) |
| INV-S3 | 任意の λ∈[0.1,5.0] で 0 ≤ win ≤ top2 ≤ top3 ≤ 1(加法構成で保証、テストで確認) |
| INV-S4 | Σtop2 ≈ 2、Σtop3 ≈ 3、Σexacta ≈ 1、Σtrifecta ≈ 1(既存許容誤差) |
| INV-S5 | joint marginal == 同一 λ の harville_topk(consistency チェッカを λ 対応に拡張) |
| INV-S6 | 決定論: 同一入力 ⇒ 同一 λ̂・同一導出値 |
| INV-S7 | λ<1 で最大 p 馬の top2/top3 が単調非増加、最小 p 馬が単調非減少(方向の健全性) |
| INV-S8 | λ・割引後値はモデル特徴に還流しない(leak-guard) |
| INV-S9 | 未指定(None/デフォルト)経路の全既存出力・logic_version はバイト不変(後方互換) |

## 公開シグネチャ(パッケージ内契約)

- `horseracing_eval.stage_discount`: `fit_stage_discount(samples, *, min_races=300) -> StageDiscount`、`discounted_topk(win, sd) -> (top2, top3)`
- `horseracing_eval.baselines.harville_topk(win, *, lambda2=1.0, lambda3=1.0)`(既定=従来)
- `horseracing_probability.engine.joint_probabilities(..., stage_discount=None)`(None=従来)
- 依存方向: probability→eval(既存)。eval は training/probability に依存しない(predictor-agnostic 維持)。
