# 127 追加特徴3候補の2018年一次比較

過去の小幅改善候補を現在のraw125列基準で再確認する。F04市場期待残差6列、088着順分解10列、過去体重偏差1列を個別に追加し、2018年seed42の同じレースで比較する。全期間・gap補正・確率平均への増分を測る研究ではなく、通過した候補を後続の全期間品質研究へ残す一次screenである。本番変更・モデル登録なし、全成果物can_adopt=false / eligible_for_verdict=false。旧110〜126のsource/config/package/cache/結果は変更しない。

## 固定された特徴定義

1. `f04`：既存070 `pm_expectation_residual.py` の6列を順序も含めてそのまま追加。finish残差は人気順位がcompleteな過去レースのFINISHED母集団で観測数3以上、win残差はoddsがcompleteな過去レースのSTARTED母集団で観測数3以上。win残差sd5はddof=1・2件以上、結果観測countは後者で0が有意。2母集団・窓・NaNを混ぜない。F03/F05列は追加せず、計算上の既存primitive依存だけを使う。F02の9列を維持する。
2. `finish_decomp`：既存088 `finish_decomposition_features.py` の10列束をそのまま追加。FINISHED系列、分母はその過去レースの全STARTED頭数−1。1頭・範囲外着順はpct NaN、窓内NaNはrollingに伝播、bestは既存通りNaNをskip。既存実装はFINISHED resultを採用しentry_statusによる再選別を行わないため、当該不整合件数を監査に出し、今回式を変更しない。
3. `weight_deviation`：新列 `asof_weight_deviation`。STARTEDかつ数値体重200〜800kg（両端含む）の観測を使う。対象日Dより厳密に前の最新source日D1の体重から、D1より厳密に前の本人の無曖昧有効日が3件以上ある場合の全平均を引く。D1自身は平均に入れない。対象日の全レースを除外する。

同一馬・同一日で有効体重が複数ある日を曖昧NaN markerとする。最新source日が曖昧なら出力はNaNで、古い有効体重に戻らない。本人平均からも曖昧日の全測定を除外する。後日ふたたび無曖昧有効測定があれば、それ以前の無曖昧有効日だけを平均に使える。最新の無効値・非STARTED行はsource日を作らない。履歴不足・馬ID断絶はNaN。0は実測差0であり、欠測を0にしない。

研究行列は111凍結141列に上記17列を順序付き追加した158列。実効列はbaseline125 / f04 131 / finish_decomp135 / weight_deviation126。3候補間で新列を混ぜず、旧125列を削らない。全旧値・カテゴリ・キー・順序・auditは完全一致、追加列はfloat64・inf禁止。列の実効順序・dtype・actual LightGBM paramsをfreezeとreceiptに保存する。

## 期間・学習・再利用

2018-01-01〜12-31、全3454レース、winner NLL3448レース、109開催日。訓練は凍結snapshotの2007年以降かつ2018年より前の全レース。PL top3、900trees、seed42、strict-past OOF isotonic8分割、weight mask0.5/seed20260810、騎手/調教師TE smoothing10。colsampleは1.0。変更は追加列だけ。

基準は旧110の2018 raw125 native cacheを必須再利用。122のfreeze/certificate/source/runtimeを結合し、127 prepareでも110/111共通行列全値・カテゴリ・全EvalRace/fold・actual train集合・effective recipe/列を再確認する。元125と元138anchor双方のcache key/meta/hash/OOF・全STARTED馬3head、元報告の全eligible損失・full品質を検証し、127の新certificateと元122certificateが完全一致することを要求する。旧screenにはprefill receiptがなかったことを明記し、今回新規学習同値を証明したとは呼ばない。cache欠落・recipe/runtime/数値不一致は停止し、予定外のbaseline fitへ移らない。

新規学習は3 outer jobs固定。1 outer=7 inner OOF＋finalの8 booster fits、計24。4armのsmokeは2008年1月・5trees/2OOFで別8 booster fits、効力判定に使わない。親のみprepare/smoke/train/evaluateを実行する。max2worker、各1thread。126と127の本学習workerは重ねず、127 CLIは126 running.lockを確認する。準備・監査・smokeは別扱い。旧global WORKを変更せず、127Factoryは明示的な独立127 WORKを使う。凍結outcomesを渡す局所patchは単一workerプロセス内contextでfinally復元され、DBを読まない。

## 一次進級規則

3候補を結果前固定し、全3結果を完成させる。winner NLL差<0、top2/top3差<=0.0005、candidate ECE<0.05、ECE差<=0.001なら `ADVANCE_TO_FULL_RESEARCH`。品質を満たし点差>=0ならDEFER、数値が揃う品質失敗はQUALITY_REVIEW_REQUIRED。NaN/不正数値/来歴不一致は停止し、失敗をDEFERへ置換しない。通過候補全てが後続full研究の候補であり、2018で一番よかった1案だけを選ばない。

既存v4 delta0、B4000、bootstrap seed20260907、alpha0.0125、sd_fold0.001816/k_seeds1は維持。screenのCI/recent/subgroupは記述で進級ゲートではない。3探索CIはfamily確認や新しい独立検証ではなく、2018は過去に研究した開発用履歴。最新年・nk・複数seed・全期間・補正/ensemble増分は未測定。

## 凍結・監査・再開

127 source hashはdriver/tests/spec/planおよび元113/122 sourceを含む。config hash、111 snapshot/元Frames、122freeze、元125/138cache、追加研究行列、feature audit、runtimeを結合する。full Framesで作る特徴と2018末までのprefix Framesで作る全共通特徴が完全一致することをprepareで確認。syntheticでは対象結果/market/weight変更・同日・未来除外を確認する。

独立scalar監査は既存builder/rolling/merge_asofを使わず全行×17列を再構成する。通常16列absolute tolerance1e-10、F04 sd5だけabsolute1e-7かつ分散差1e-14の両方を事前固定し、NaN一致・列別検査数・最大差を保存する。これは独立数値実装間の丸め許容であり、元共通行列・カテゴリ・同builder prefixの完全一致を緩めない。

cache/receipt/report/evidenceは追記のみ。既完了jobはfreeze SHA・exact job・cache SHAを検証して再利用、既存無receipt cacheは停止して保全。fit後の失敗cacheは別unverified名へ保全し、旧成果物に触らない。complete result resumeは全3jobの有効receipt、report/evidence/freeze SHA、列/recipe、full/eligible母集団、各race loss差・全平均・primary変更flag、元110 raw125 baseline損失一致を再確認する。
