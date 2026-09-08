# 116 結果レビュー

2026-09-08実施。研究判定は **SEED_MEAN_IMPROVEMENT_RETAINED**。相対能力13列削除＋出走間隔log確率補正を研究候補として保持する。本番採用・切替を行わない。

## 結果と解釈

- 全期間の3seed等重み平均winner NLL差は、補正増分−0.0015803617394198888、現行構成比−0.0024085526220341733。
- 全6比較で最近期間・top2/top3・校正の主要品質ガード通過。平均品質も固定した許容幅を通過。品質BLOCKEDなし。
- 補正増分の3seed記述的標準偏差は0.0000242465、総差は0.0010509631。補正増分は近い値で再現したが、seed42で見た総改善幅をそのまま代表値にはしない。
- 補正増分のノイズ込み98.75%CIは全seedでゼロを跨ぐ。総差はseed42のみCI上限が負。全seed負・多数決・各seedの有意差を追加条件とせず、事前に固定した平均損失と品質による保持判定を適用した。
- 最近3年・5年の平均差は両比較で改善方向。ただし2026年単独の総差は全seedで悪化方向（+0.003326〜+0.004672）、非劣性が未確定。nk群はseed42・44でNO_DECISION。これらを平均改善で解消したとは扱わない。
- 提供用seed42は変更しない。今回の平均はseed別損失の平均であり、平均確率のアンサンブル性能ではない。平均CIや、3seed標準偏差を用いた提供用モデルの新しいノイズ推定は作っていない。

## 独立照合

[独立計算コード](evidence/independent-review.py)と[独立レビュー結果](evidence/independent-review.json)を別に保存した。独立agentによる全30cache/receiptのseed・recipe・列順・trainhash・race/horse集合・確率の照合は通過した。

保存gammaから全23,030レースをseedごとに再生成し、22,990適格レースのwinner NLLを元の両比較evidenceと照合した。全seedでレース単位の最大誤差は0。top2/top3は登録済みHarville組立と指標関数で再評価し、ECEとともにreportと一致した。

21個の年別gammaについて、先行年だけの対象数・最終日を確認し、保存値で目的関数と勾配を再計算して一致した。係数の再fitやLightGBMの追加学習は行っていない。

日単位の差分合計と件数から4,000回bootstrapを独立再計算し、登録済みの7fold・移植ノイズ仮定を使った各seedのノイズ込みCIと一致した。6reportの対象集合・順序、平均損失、集計判定も一致した。部分集団の残余リスクは元reportを保持・検討しており、新しい平均CIや保証には置き換えていない。

## 実行と完全性

新規30fitは2worker・各1threadで完了。開始から最終receipt保存まで208.79分、新規fit累計411.78分。旧seed42の過去学習費用は含まない。新規4比較の記録評価時間合計52.86秒は係数推定・監査時間等を含まない。詳細は[実費用](evidence/actual-cost.json)。

実行前56テスト、4構成smoke、独立実装・freezeレビューを通過済み。実行後は凍結source/config/runtime/upstream、全30receipt、6report、集計script SHAを再照合した。最終照合は[final-integrity.json](evidence/final-integrity.json)。`git diff --check`も通過した。

- source hash: `5f847f8667380afb285887ec9dc1350c09b621a847567d7ca1b0b4e53f7bca77`
- config hash: `06fa5bd77ad18340e3b1a4f6d8f35d690de53443615b7686aba6d65799f1daaf`
- run-freeze SHA: `487d4f62963639e0dcad7d9edcf22df05fe47ec2362dad5dcea96c04cb032d4d`
- verdict SHA: `2d95beaa2cd5fdf5bb46db62f7e5e0920c1c842d8bf76ba825a4e04683438956`

DBのread-only照会ではactiveは `lgbm-094-cap900` のみ。package source、特徴量registry、旧研究証跡を変更せず、年間最終確認枠も予約していない。

## 次に確認すること

2026年の総差から補正増分を引いた13列削除単独の差は、全seedで悪化方向。次の優先研究はその悪化要因の切り分けとする。これは過去の同じ母集団での損失分解であり、因果関係や削除単独の新しいCIを主張するものではない。

構成の再選択は別研究として記録する。最終候補の採用確認には、選定に使っていない固定した将来期間と予測時入力条件の検証が残る。今回の過去データ研究を未使用期間の確認に置き換えない。
