# 127 実行前レビュー

2026-09-08、実データのprepare・学習・評価を開始する前に記録した。実装担当と独立担当に加え、親が既存F04/088・体重定義、native基準再利用、実効列・レシピ、評価・進級条件を確認した。担当・独立担当・親の95テストがPASS。旧110〜126のsource/config/artifactsを変更していない。

## 固定した判断

- F04のfinish残差は人気の完全な過去レースでFINISHED、win残差はオッズの完全な過去レースでSTARTED。別の観測数で3件gate、SD5だけ2件以上・ddof1を維持。
- 088は既存のFINISHED系列・完全な出走頭数の分母・窓内NaN伝播をそのまま用いる。entry/result不整合は件数を記録し、旧式の母集団を変更しない。無限大の元着順は入口で拒否。
- 体重は前回の有効測定と、その日より前の無曖昧な有効3日以上の本人平均との差。200〜800kg、STARTEDのみ。同一馬同日複数有効測定はNaN markerで、最新なら古い値へ戻らず、本人平均からもその日を除外する。当日は全て除外。
- 111の凍結Frames/141列から17列を追加。共通全値・カテゴリ・train/race/fold・prefix完全一致と、旧native125/138の予測/証明の一致を要求。基準が欠けた場合も再学習fallbackはしない。
- 3案それぞれ2018年・seed42・900trees・8 OOF。NLL差が負でtop2/top3/校正品質を満たせば全期間研究へ進級。最低改善幅やCIを新たな足切りにしない。2018探索の結果を全期間保持や現構成への増分と呼ばない。
- 126と127の本学習は重複させない。最大2 worker・各1 thread。追加3 outer=24 booster fits。煙試験4案8 booster fitsを分けて記録する。

## 独立監査

特徴量監査は既存builder・rolling・expanding・merge_asofを呼ばず17列をscalarで再構成する。合成10レース×5頭の850セルで母集団欠損、同日重複、範囲外着順、NaN窓、体重曖昧日を検証済み。親も別fixtureでF04の別gate/SD、088のラグ・5点傾き、最新曖昧日のNaNと先行平均を確認した。

通常16列はabs≤1e-10、SD5はabs≤1e-7かつ分散差≤1e-14を結果前に固定し、count/NaN/母集団/時点は厳密に比較する。列別の最大実測差とSD分散差を保存する。

予測監査は元native factoryのrecipe同値を確認し、全3head・ラベル・損失・校正・開催日bootstrap/1fold noise・3進級判定を独立再計算する。method/helper、特徴監査、入力、全cache/receipt/report/evidence/summaryのSHAを読込前と終了時に照合する。監査自身のfitは0。

## 変更停止時のSHA

- config: `7f907a97d577a76e220f82f8678384092e5b0c49c6d1bb552e5e46ac35f7a12b`
- source（driver/test/spec/plan・旧sourceを含む）: `7c5b548fda6eeb69579b7c92aee8137970a5c648d948ae73478dfdf4d0f732ee`
- driver: `cafa030424ddf0e31b6a1cd4a112028a03a7f668d601b9a714e430a44e1c948a`
- tests: `c97a79a8b30d0307b634ff9ef3640fffd764edce17efd16119a6a54a427dc2e9`
- 独立特徴監査method: `de537cded6d731ea31af7d6b0cf498ba9b4baf00c4120b3f4f0c523c2d16d77b`
- 独立予測監査method: `2fa2009c3bd9de17a31e076dba09d8b425d04c4f37cb3f748d4620bec51b1dae`

本番切替・DB書込・年間確認枠予約を伴わない研究として、親がprepareから実行する。
