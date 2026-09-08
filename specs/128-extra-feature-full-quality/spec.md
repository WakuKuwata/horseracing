# 128 追加特徴候補の全期間品質比較

127の2018一次screenを通った追加特徴を、2020-01-01〜2026-08-23の全7foldでraw125列基準と比較する。固定候補族はf04（既存市場期待残差6列）、finish_decomp（既存着順分解10列束）、weight_deviation（前回有効体重の個体内偏差1列）だけ。今回の最良補正stack125や確率平均への上積みを測るものではない。旧110〜127のsource/config/packages/cache/成果物は変更せず、新128で完結する。

## 条件付き選択

候補集合と順序はf04→finish_decomp→weight_deviationを結果前固定。127全3report/evidence/receipt/summaryが完成し、独立予測監査と独立scalar特徴監査がPASSして全SHAが一致してから、`ADVANCE_TO_FULL_RESEARCH`の全候補だけをprepareで凍結する。最良1件・多数決・新しい効果量閾値は使わない。0件ならjobs=[]、smokeを含め追加fit0、最終記録はNO_ADVANCING_CANDIDATESとする。これは候補族全体に効果がないとの証明や本番採否ではない。

元127 source/runtime/config/freeze/158列matrix/feature-audit、全3結果・evidence・receipt、基準と候補の4cache、独立予測method/json・122 helper method・特徴method/jsonを全て結合する。予測監査の完成receipt SHA、feature method/result SHAも照合する。特徴監査は958011行×17=16286187セル、NaN一致、strict prior dayを確認したものが必要。通常列abs1e-10、sd列abs1e-7かつ分散差1e-14を超えず、全列最大差が有限であることを128も検査する。

## 比較条件

主期間は2020-01-01〜2026-08-23、全23030レース、winner NLL22990レース・715開催日。毎年の訓練は凍結入力の2007年以降の全厳密過去年。全7fold、seed42、PL top3、900trees、strict-past OOF isotonic8、weight mask0.5/seed20260810、TE smoothing10、colsample1.0。127の特徴定義・NaN・実効列順・dtype・全paramsを変更しない。raw125基準、F04なら131列、finish_decompなら135列、weight_deviationなら126列。

新boosterは候補ごと7 outer。1 outer=7 inner OOF＋finalの8 fitsなので56 fits/候補、最大21 outer/168 fits。基準は0 fits。各候補の年順は2026→2025→2024→2020→2021→2022→2023。直近から計算するのはスケジュールだけで、点差・CI跨ぎ・NOT_PROVEN・途中年の品質値で止めない。候補の7fold＋結果を揃えてから次候補に移る。入力/recipe/cache/OOF/数値の不整合だけ停止して保全する。全品質を計算できたBLOCKEDも記録し、他の登録候補を取り消さない。

基準7年は元113 ReadOnlyFactoryを元113 config/freeze/identityと141列projectionで読み、元110 native recipeでpayload検証する。128の未使用追加列dropを含むrecipeを古いcacheのmetadata照合に使わない。両者の実効125列とdropを除くrecipe同値を別検査する。prepareで127matrixを141列へ戻した全値・カテゴリ・build audit・EvalRace集合が111原snapshotと完全一致することを確認する。全レースの全started馬3headとactual train/key/receipt/SHA/OOFを検査し、元115/116 raw125基準の全eligibleレースID/日付/NLLと全体NLL/ECEが一致することを証明する。

## 品質・保持の判定

126と同じ small_gain_research_v1。delta0、B4000、seed20260907、alpha0.0125、sd_fold0.001816/k_seeds1を維持する。負のwinner NLL点差と既存品質/害検出条件を満たす候補を保持する。CIが0を跨ぐ場合はRETAIN_UNCERTAIN、跨がない場合はRETAIN_SUPPORTED。点差>=0はDEFER。top2/top3差<=.0005、candidate ECE<.05/ECE差<=.001、recent3/5の害検出、canonical/nk/recent_year_onlyを既存方針で扱う。NOT_PROVENだけで自動拒否しないが残余リスクを表示する。数値が揃う品質失敗はBLOCKEDとして報告、未計算/非有限/来歴不一致は停止する。

各resultで全eligible損失と差・全期間平均・primary変更flag・全race hash・原raw125 baseline NLLを再照合する。recent3/5の件数/開催日/平均・CI三値・残余リスク、critical3群の数値/CI/状態/target2026を検証する。seed noiseは単一seed・7foldとして記録し、seed増加や確率ensembleと表現しない。全成果物can_adopt=false / eligible_for_verdict=false。歴史開発期間の研究であり、年間確認枠や本番切替はしない。

## 実行・再開・独立性

prepare/smoke/train/evaluateは親だけがレビュー後に実行する。max2workers各1thread。128fulltrainと126/127fulltrainは重ねず、CLI親/worker双方で旧running.lockを検査する。旧ソースへ相互lockを追加する変更はしないため、実行担当も同時起動しない。準備・監査・smokeは別扱い。

128 FreshFactoryは127のrecipe/scope/check_payloadを使うがfit本体を128独立WORKに明示実装し、旧module.WORKの書換えや一時redirectをしない。凍結outcomesへの局所patchは単一worker内contextで復元され、DBを読まない。smokeは選択案＋基準の5trees/2OOFのみ。0選択はsmoke0fit、1〜3選択では各arm2 booster（計4〜8）を本学習とは別計上する。

source hashに128driver/tests/spec/plan、127/116 source、研究判定source、元126driver SHAを含む。config/runtime/全upstream SHA・actual jobsをfreezeする。既完了cacheはexact job/cache/freeze receiptで検証し、無receipt cacheは上書きせず停止。fit後receipt前の例外cacheはunverifiedへ保全する。完成結果再開には当該候補全7cache receiptとreport/evidence/receiptの一致が必要で、途中レース・一部seedの平均を作らない。
