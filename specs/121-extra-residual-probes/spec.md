# 121 追加残差の一次プローブ

現在保持されている125列＋出走間隔補正に、前走自身の出走間隔や確率の温度補正を追加する余地を調べる。新規booster学習は0。119の季節補正、120の確率アンサンブルとは独立した研究で、旧110〜120のsource/config/evidenceを変更しない。

raw125の凍結済み3seed（42/43/44）予測を使う。2019年は係数fitの初期データのみ、2020〜2026-08-23を評価する。保持済み補正には2019用gammaが無いため、保持済み確率へ追加係数をfitするのではなく、raw125にgapと新項をjoint fitする。比較基準は既存115/116で保存したgap-only係数による確率で固定する。

3候補と4比較を結果前に固定する。

- A：gap＋prior_gap_log。保持済みgap-onlyと比較する。prior_gap_logは凍結111 matrixのstarted行、同一horse_idの厳密過去の異なる2日D2<D1<Dからlog1p(D1−D2)を作る。現行のD−D1とは異なる。開催当日・取消除外・対象結果を参照せず、ID断絶や初期履歴不足は欠損中立。現行gapの履歴母集団とは異なる可能性があり、旧081の完全再現とは呼ばない。
- B：gap＋全体温度x。x=log(p)−レース内mean(log(p))。保持済みgap-onlyと比較する。
- C：gap＋global x＋文脈別x。旧screen_architecture.pyの基底を保持し、頭数≤9/10–13/14+の3帯、正規化entropyの3帯、上位2確率比log(p1/p2)の3帯のslopeを使う。Bと保持済みgap-onlyの両方と比較する。entropy・比の境界は各年のstrict先行fitレースだけで作る。単走はentropy0、x0。

A/Bは既存fit_gammaのridge1e-6、50iter、tol1e-9。Cは旧文脈基底を使うが正則化は新仕様：gap/globalは1e-6、追加context9項のみlambda1000/n_fit_races。1000は過去診断の選択値を事前固定し、今回grid探索はしない。旧の全係数同一ridge・inner選択手順とは区別する。B/Cのレース別実効温度指数は1＋global_delta＋当該context_delta合計とし、fit/held全レースで正値を要求する。旧診断の最適化には明示的な正値制約が無かったが、本121では非正値をBLOCKED_TEMPERATURE_DOMAINとして止め、結果後のclipや再最適化をしない。年別fit/heldの指数min/maxを記録する。L-BFGS-Bの1500iter/ftol1e-13/gtol1e-8、有限性、初期zeroより悪化しない正則化目的関数、gradient∞≤1e-5を要求する。失敗を改善なしと読み替えずBLOCKED_NUMERICAL、再調整しない。

3seedのレース集合・順序・同日を完全一致させ、レースごとの3seed平均損失差を集計する。確率平均ではない。B4000/seed20260907/alpha.0125のrace-day sample CIを全seedと平均差に表示する。係数再推定分散・training noise込みtotal CIではない。

A/Bは平均差が負ならADVANCE_TO_FULL_QUALITY、CはB比と保持基準比の双方が負なら同状態とする。CI跨ぎだけでは進行を止めない。全3seed完了が必要で、完了した一部だけで平均しない。top2/top3/ECE/recent/subgroupはこの段階では未測定。正式RETAIN/ADOPTではなく、次の全v4品質評価へ進める候補を選ぶ一次probeに限る。全成果物はcan_adopt:false、eligible_for_verdict:false。

prepareは完成済み118の独立監査PASS・method/freeze/summary/全report/evidence SHAを結び、118/116/115/113の完全証跡と111snapshotを検証し、raw125から全started馬の確率・canonical eligible行を3seed分保存してsource/runtime/config/input SHAを凍結する。保存したgap-only係数から再構成したレース別NLLを元115/116証拠と順序込み1e-12以内で一致確認し、各年の全レース数・eligible数を凍結母集団と照合する。source hashには過去計算・旧文脈式・固定正則化を含める。runは凍結済み小入力だけを読み、先行年係数fitと評価を順次実行する。単一プロセス、seed並列workerなし。既存成果物はhash検証と保存済みseed別損失からのsummary再集計だけで確認し、係数fitを再実行しない。prepare/runは親レビュー後に親が実行し、実装担当は実行しない。
