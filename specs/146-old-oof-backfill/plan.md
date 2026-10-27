# 146 実装計画

2026-10-25完了。教師90本・候補120本・対照240本の比較と独立監査が終了。OOF補完案110.24%・利益409,240円で両対照を下回った。詳細は[result-review.md](result-review.md)。本番変更なし。

Python3.12 training/.venv、既存numpy/pandas/LightGBM/scipy。macOS24GiB、最大2worker各1threadの研究CLI。142〜145の凍結物と共有の既存変更は保護し、146新規ファイルだけを追加する。

1. models.pyは教師を142のbaseline binary関数、候補を143のweighted_baseline関数へ委譲する。77特徴・300round・BASE_PARAMSを両方で固定。年次の前年末cutoff、完全field、train-onlyカテゴリ、保存再生を検証する。
2. experiment.pyはmentor90本とcandidate120本を別stageで実行。再開は実入力から年次レシピと重みを再計算し、identity・receipt・本体・予測の一致を必須とする。完了markerは各々270/360のidentity/evaluation/receipt hashを束縛する。
3. run.pyで従来の基準と③を240本のreceiptから再構築。全候補レシピ・2比較・感度診断を教師実行前のmentor_protocolへ凍結する。
4. mentorの90本を確認後にraw確率のmean15を作る。厳密に過去の学習来歴を確認し、旧2019〜2025年OOFを値・列・行順ごと保持して連結する。weights_manifestで来歴と費用を保存する。
5. 候補protocolは最初のmentor_protocolから派生し、共通の設定を変えず、新OOF・manifest・教師receipt/予測・完了markerのSHAを追加する。独立OOF監査後に候補を学習する。
6. report.pyで候補の15seed確率平均を先に作り、143の同じ評価器で回収率・利益・不確実性・感度・リスクを集計する。評価コードを実験途中で変更しない。
7. 独立監査は旧240+新教師90+新候補120の450fit/1350artifactSHA、教師平均・過去来歴・重みの全年度再計算、全4条件の購入配分・2比較区間と感度診断、教師9+候補9本×64頭の保存本体再生を確認する。

## 独立設計の指摘

- 2019は今回は非中立で、以前の完全一致対照を要求しない。
- 重みの適用範囲拡大に加え、情報あり部分の平均重みの分母も変わる。両効果を一意に因果分解したとは表現しない。
- 固定レシピ自体は後年の研究時点で決めたもの。モデルの学習cutoffは過去限定でも、当時事前登録した戦略や完全未参照検証とは呼ばない。142の選択モデルを新たに探索・適用しない。
- raw確率を平均してからEVを作り、p_raceへ置換しない。旧教師の値を再調整せず、過去の来歴不明OOFを継ぎ足さない。

## Constitution check

- I: 2007以降、12桁race ID、exact race/horse結合。
- II: 教師も候補も対象前年末まで学習、OOF train-through<行の年。元データ読取上限も対象年末。
- III: 全レシピ・評価・2比較は教師開始前に固定、採用不可。
- IV: 完全field・取消・欠損を維持し、raw EVと正規化品質指標を区別。
- V: 入力/source/runtime/teacher/weights/model/prediction SHA・実時間・保存再生を記録。
- VI: 本番/API/UI/DB変更なし、追加サービスなし。
