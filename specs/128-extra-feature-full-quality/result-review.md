# 128 結果レビュー

128は進級候補0件として正常終了した。全7年の新candidate予測・品質比較は存在せず、RETAIN/DEFER等の新しい全期間判定も出していない。記録されたNO_ADVANCING_CANDIDATESは事前の条件分岐の完了状態である。

## 127からの選択

| 候補 | 2018 winner NLL差 | top3 logloss差 | 127進級判定 |
|---|---:|---:|---|
| f04 | +0.004013684 | +0.000547785 | QUALITY_REVIEW_REQUIRED |
| finish_decomp | +0.002834715 | +0.000061233 | DEFER |
| weight_deviation | +0.000482886 | +0.000127608 | DEFER |

127はraw125基準・2018年・seed42・900trees/8OOFの同じ母集団で3案を全て完遂した。F04はtop3非劣性も不通過、残り2案はNLL点差非改善。旧研究の微小改善を理由に救済せず、最大値だけを選ばず、固定順の全ADVANCEを選択した結果が空集合になった。新しい必要改善幅を加えたわけではない。

127の独立特徴監査は全958,011行×17列=16,286,187セルPASS、独立予測監査は31,071照合PASS。128は両method/json、全3report/evidence/receipt、summary、全4構成cache、元Frames/matrixとsource/runtimeのSHAを照合した。

## 0件経路の実行と監査

prepareでselected_candidates=[]・jobs=[]を固定し、smokeのarm_names/recordsも空で保存、trainは学習workerを起動せず、evaluateはreports={}・new_outer_jobs=0・new_booster_fits=0のsummaryを作成した。128 cache/prefillの学習成果物も0件である。

凍結済み独立methodは候補0件でも元raw125の2020〜2026年7cache/receiptを別に読んだ。native110 metadataと実効125列/recipe、厳密過去年の訓練集合、OOF十分性、全started馬のwin/top2/top3を検証。全23,030レース・eligible22,990・715日を確認し、元116の全eligible NLL/ID/day順と全体NLL/ECE、全レースhashを独立scalar式で照合した。

監査結果は **PASS**、22,992照合、最大誤差3.04e-18。method SHA `5b59bcb02631f9615c415cfd282f8617e9856ff3668ba5df631c1084306c8fbd`、結果JSON SHA `fb2778e87e0475713444a961f4b29cd4c78bcc0eb1e49f1c1d9534949c71a8f9`。開始・終了のsource/元cache/receipt/freeze/summary SHAも一致した。候補比較が0件なので新しいcandidateのCI/recent/subgroup結果は計算していない。監査JSONの一般的なCI方式説明はmethodが対応する仕様であり、このゼロ件実行で候補CIが出たことを意味しない。

## 費用と解釈

本学習0 outer/0 booster、基準の新規fit0、smoke0 outer/0 booster、監査追加fit0。既存7native cacheを再利用した。127で既に使った学習費用は127の記録に属し、128へ二重計上しない。

準備・hash/来歴検証・baseline replayには計算時間が発生したが、全工程wall timeとRSSは未計測。0fitを「処理時間も費用もゼロ」と表現しない。

この2018screenから、各特徴の全期間・別seed・最新年・補正stack/ensembleへの効力を判定しない。現行の研究保持構成も本番構成も変更しない。
