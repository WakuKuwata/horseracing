# 143 実装計画

Python3.12 training/.venv、既存pandas/numpy/LightGBM、研究CLI。最大2worker・各1thread、24GiB。既存142成果物や共有の未コミット変更を維持し、新規143ファイルだけを編集。

## 実装

1. features.py: 111 source-frames.pklを最初に日付制限、mutable horses不使用。023+061の10列、023脚質+031+041の14列。正確なrace/horseキーで完全結合。欠損は欠損のまま。
2. 追加特徴の履歴から馬番重複の202504040406/407をレースごと除外。406は市場の対象外、407は142共通除外。基準母集団は変えない。
3. run.py: 142の120基準本体receipt/予測を検証して15seed平均を再構成、既存集計と完全一致を確認。過去年次OOF2019〜2025を③用に保存。入力・依存コード・24特徴・3候補・予算を全て開始前にSHA固定。
4. models.py/experiment.py: binary300roundで時計速度と展開を各120fit（計240）。カテゴリ辞書は学習集合限定。各本体・特徴順・予測・receiptを保存し再読込予測差を確認。
5. report.py: 現行参照、同条件基準、時計速度、展開を同一母集団で比較・保存。開催日4000回paired bootstrap、3事前固定候補にBonferroni補正。全日・未購入日を保持。
6. ①②比較のSHA付きmarker完成後だけ③を120fit。固定幅0.2、中心EV1.2、raw重み1+exp(-0.5*((EV-1.2)/0.2)^2)。当落非依存・全頭OOFが揃ったレースのみ使用、informative subset平均1、他は正確に1。OOFのfit-through<行の年を検証。
7. ③を同条件基準と比較。2019は全重み1なので基準確率と完全一致が必要。全結果を独立監査する。

## 独立設計確認

feature143_reviewは固定raw利用、原特徴2束、source異常2レースの除外、target結果改変/prefix不変テスト、③の母体事前固定とOOF来歴を提案。すべて反映。sigmaは探索せず0.2へ事前固定。079のPLレース重みとは別のbinary行重みであり、過去の類似研究がnullだった点を維持する。

## Constitution check

- I: 2007以降・12桁raceID・同一source正確なID、推測alias結合なし。
- II: 同日/未来結果は追加特徴へ入らず、年次学習も前年まで。オッズは既存142の市場研究枠内。
- III: 基準・現行参照・ECE・同予算損益を先に実装、採用判定なし。
- IV: started完全field、取消除外、新馬欠損維持、raw確率EVと正規化品質評価を区別。
- V: input/source/recipe/runtime/本体SHA、roundtrip、実費を保存。
- VI: 新規サービス・API・UI・schemaなし、研究契約を開始前に固定。

## 結果

2026-10-19完了。①②はwinner NLLを改善したが、回収率・利益の改善は確認できず。③は回収率116.84%・利益663,730円、同条件基準比+104,930円だが補正区間は0を含む。全360新規fitと74対象テスト、全480receiptの独立監査を完了。モデル・購入条件は現行を維持。条件・年別・実費・研究の限界はresult-review.mdを参照。
