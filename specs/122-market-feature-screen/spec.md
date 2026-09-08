# 122: 市場特徴と列サンプリングの初期比較

目的は、現在の raw125（138列から相対能力13列を除去）の代替候補を、2018年の固定入力で低費用に再検討すること。F03、F05、colsample を独立比較し、gap補正やensembleの増分とは呼ばない。過去に探索済みの歴史的開発データであり、本番採用・新しいholdout・確認用年枠を作らない。

独立設計レビューは2026-09-08に支持。実行は親担当、実装担当はprepare/smoke/train/evaluateを起動しない。

| 構成 | raw125との差 | 実効列数 |
|---|---|---:|
| baseline | 変更なし | 125 |
| f03 | 058の4列を削除、既存pm_rank_robustの5列を追加 | 126 |
| f05 | 既存pm_conditionedのsupport6列だけを追加 | 131 |
| colsample_07 | colsample_bytreeを1.0から0.7へ | 125 |

全構成でF02の9列を維持する。F03は人気順位のpercentile、直近/直近5走平均/1番人気率/3番人気以内率と観測数。元058の asof_mkt_rank_avg、asof_mkt_rank_norm_avg、asof_mkt_rank_best、asof_beat_mkt_avg の4列だけを置換する。F05はsurface/distband/venue別の市場支持と実観測数であり、residual2列は行列から除外する。係数や閾値を結果に合わせて変更しない。

入力は111のsnapshotとsource-framesをそのまま読み、既存builderでscripts内の研究行列に11列を追加する。registry、feature_version、package、旧110〜121のsource/artifactsは不変。builderのstrict prior/same-day exclusion、F03のmin_obs3（countは事実値）、F05のλ5・親平均fallbackと実cell countを維持。共通列の全値・カテゴリ・build audit・全EvalRace/foldを厳密比較し、2018末で入力Framesを打ち切った場合にも全過去新特徴量が完全一致することをprepareで確認する。新列はfloat64、inf不可、rank/rateは[0,1]、countは非欠測・非負整数。

評価は2018-01-01〜2018-12-31、全3454レース・eligible3448・109日。学習は2007以降の全過去年、seed42、900trees、8 OOF blocks（7 inner fits＋finalの8 booster fitsで1 outer job）、mask rate0.5、mask seed20260810、TE・校正等は元110/111/113と同一。最大2 worker、各1 thread。

旧110 screen-relative_abilityのnative125予測を再利用候補とする。旧freeze・111との全行列投影/レース/fold/有効recipe・歴史的fresh138予測等価certificateのmethod/freeze/runtimeを検証する。raw125cacheはnative key、actual train hash、列順、tuple metadata、OOF十分性、全馬のwin/top2/top3の集合と確率整合を検証し、元138cacheとともに旧screenの全eligible両側NLLと全体top2/top3/ECEを厳密再現する。元screenにはprefill receiptが無いため、新122 certificateが元report/evidence/cacheを結ぶ。これは125列を新規再学習した再現実験ではない。

基準cacheが存在すれば追加3 outer jobs（24 booster fits）。期待native cacheが存在しない場合のみ、事前に許容した追加4 outer jobs（32 booster fits）へ進み、理由をfreezeへ記録する。recipe/source/runtime/数値の不整合はfresh fallbackにせず停止し独立診断する。破損した旧成果物を上書きしない。元125のOOF込み543.35秒から、3 jobsは累積約27.2分、4 jobsは約36.2分、2並列の2波は約18.1分が粗い目安。特徴構築・検証・煙試験・評価と列変更による実費差は別で、OOF時間をさらに8倍しない。

独立3比較の予備進行は winner NLL差<0、top2/top3差<=0.0005、candidate ECE<0.05、ECE差<=0.001をすべて満たすとADVANCE_TO_FULL_RESEARCH。同損失または非改善はDEFER、品質失敗はQUALITY_REVIEW_REQUIRED、欠測/不正数値/来歴不一致は停止する。完全同損失は正常な無効果結果。CI/recent/subgroupは記述し、この単年予備進行の必須条件に加えない。δ0/B4000/alpha0.0125/bootstrap seed20260907/sd0.001816/k1を固定するが、3候補CIは探索的であり新たに家族全体の確認的検定になったとは主張しない。

通過はその候補を次のfull-quality研究に残すだけで、全期間RETAIN・2026改善・勝者選択・自動本番採用を意味しない。各結果とsummaryはcan_adopt=false/eligible_for_verdict=false。結果を見た追加候補・組合せ・年窓変更は別研究として再登録する。

freezeは全入力/行列/監査/builder/source/runtime/configと旧予測SHAを固定する。証明に利用した125列cacheと138列cacheの両方を再検証する。新cacheは独立122領域、実効列順・型・recipe・実際のLightGBM paramsを検査しreceiptに結ぶ。完成receiptのあるjobのみ再開し、unreceipted cache/resultは保全して停止する。完全な結果の再実行はreport/evidence/receiptと元freezeを再検証する。損失の有限非負性・行差・発行順・改善有無を再検査し、全体の両平均と差を行から再計算する（元集計np.logと行証跡math.logの丸めだけ1e-12を許容）。smokeは2008年1月の4構成5trees/2 OOFを配線確認だけに使い、効力を判定しない。
