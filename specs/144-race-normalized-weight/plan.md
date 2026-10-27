# 144 実装計画

2026-10-24完了。240新規fitと独立監査が終了。新案111.47%・利益450,240円で基準/③への改善は確認できず。詳細は[result-review.md](result-review.md)。提案2/3は保留。

Python3.12 training/.venv、既存numpy/pandas/LightGBM。研究CLI、最大2worker各1thread、24GiB。142/143凍結成果物・既存の共有変更を保護し、新規144ファイルのみ作成する。

## 設計

1. models.py: immutable143.training_weightsを各年の実学習集合へ適用し、完全なレースに対してa/b分解。元ベクトル・新ベクトルSHA、最大mean/product誤差・中立行数をreceiptへ保存。
2. 学習本体は143.fit_modelを再利用。内側kernel armはweighted_baseline、identity.research_armにwithin_race/race_onlyを明記する。カテゴリはtrain-only、77列順とparamsは142/143と一致。保存本体再読込を検証。
3. experiment.py: 8年×15seed×2arm=240fit。再開時は8年分の小さな期待recipeだけを保持し、実入力から重みを再計算してidentity/receipt/本体hashを照合。完了markerは120job/240fit/480identity・evaluation・receipt hashを束縛する。
4. run.py: 142基準と143③を各120本のreceipt/予測から再集約し、保存ensembleと完全一致を確認。現行保存ens15は別参照。全入力・実依存・変換式・4比較・指標を実行前にSHA凍結。
5. report.py: 凍結143.scoreで同予算損益を算出し、新規4比較と感度診断を集計。15seed確率を平均してから評価する。新モデルへ市場混合係数を移送しない。
6. 独立監査は実験実装を使わない計算で、全480receipt/1440artifact SHA、独立重み・購入配分・年別損益・4000回bootstrap・保存本体サンプル再生を検証。

## 独立設計指摘

143の重みは各外側年の学習集合ごとに正確に再計算する必要がある。完全レースを保証し、欠損教師のレースは全頭exact1にする。a*b=w・各race平均b=1を確認するが、大きなfieldは行数が多く、レースが一律同じ損失になるとは表現しない。LightGBMのweighted初期値や有限容量の相互作用も変わるので、a/b対照は機序解釈を助ける比較であり利益の一意な因果分解ではない。

## Constitution check

- I: 2007以降・12桁race ID、既存の正確なrace/horse結合のみ。
- II: 教師OOFのfit-through<行の年、学習は前年まで。対象窓を読取時に制限。
- III: 評価器・基準・較正・4比較を先に固定し、採用判定なし。
- IV: started完全field・取消/欠損維持、raw確率EVと正規化品質評価を区別。
- V: source/input/protocol/runtime/model/weight SHA、実費と再生誤差を保存。
- VI: API/DB/UI変更なし。提案2/3は保留。
