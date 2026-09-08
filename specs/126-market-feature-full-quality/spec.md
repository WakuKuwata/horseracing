# 126: 市場特徴screen後の全期間・品質確認

現段階は実行前の条件付き計画。122の結果はまだ未確定で、追加fitは行っていない。2026-09-08の独立設計レビューは、2018通過候補を2020〜2026年の7foldで完遂する方針を推奨した。126作成前にspecs/artifactsに既存126がないことを確認済み。

## 対象と固定する比較

122の完成済みreport/evidence/receipt・freeze・summaryと独立レビューを検証し、progressionがADVANCE_TO_FULL_RESEARCHの構成だけを対象にする。対象になり得るものはf03（126列）、f05（131列）、colsample_07（125列）の3構成に限定する。通過数0なら新fit0で終了する。勝者1本に絞ることや、非通過候補を救済して追加することはしない。

各候補を同seedのraw125 pruning基準と比較する。F03/F05の元builder・欠測・列順・型・F02維持・F05 residual除外、colsample_bytree=0.7の変更範囲は122と完全同一。追加特徴の入力は111 frozen snapshot/source-framesから122が作成・監査した研究行列をそのまま読み取る。行列の共有元列と元113 native cache用125列の値・カテゴリ・レース・学習集合が厳密に同じことを再証明する。DB読取、registry/package変更、旧110〜125のsource/cache/artifacts変更を行わない。

この研究はrawモデルの変更を検査する。保持中のpruning+gapやensembleへの増分、本番配置にそのまま使えることを意味しない。それらとの累積比較が必要になった場合は別研究で条件を固定する。

## 学習・評価

- 評価期間: 2020-01-01〜2026-08-23。元116と同じ全23,030レース、eligible22,990、715日。
- 学習: 各評価年より前の2007以降全履歴、seed42、900trees、8 OOF blocks、mask rate0.5、mask seed20260810、元TE/校正/全情報入力の条件を固定。
- 候補あたり2020〜2026の7 outer jobs。1 outer jobは7 inner OOF fitsとfinal fitを含む8 booster fits。2019評価の追加fitは不要で、gap係数warmupも行わない。
- baselineは113/116の来歴で証明済みの元125 native cacheを7foldともread-only reuseする。実際のtrain集合、列順、recipe、OOF十分性、全馬3head予測・元receipt/SHAと元116 raw baseline証跡を確認し、基準新fit0を前提にする。基準cache不足や不一致は停止して独立診断し、自動再学習しない。
- 新candidate cache・logs・receiptは独立126領域に保存する。最大2 workers、学習器は各1 thread、workerは行列1個だけをロードする。未署名cache/resultは保全し自動再利用しない。

全体winner NLL差<0を小差保持の入口とし、従来の必要改善幅を戻さない。元small_gain_research.assess_researchを使用し、top2/top3差<=0.0005、ECE差<=0.001、candidate ECE<0.05、recent3/5とcanonical・nk・recent_year_onlyの既存品質判定を維持する。全期間CIが0を跨いでもRETAIN_UNCERTAINを許し、NOT_PROVENだけでは自動拒否しない。明示FAIL・MISSING・不正/未計算は採用可能な品質証拠にしない。正しい完全同損失はDEFER。

δ0、B4000、bootstrap seed20260907、alpha0.0125、seed noise sd0.001816/k1を固定。CI、recent3/5、全critical subgroupの数値と残余リスクを省略しない。2026全体、nk、最新年とnkの交差等の既存出力も記述するが、新しい群別足切りを追加しない。seed42のみの歴史的開発研究であり、過去の選択履歴を消した確認的CIとはしない。

## 実行順と途中停止

候補は122の固定順f03→f05→colsample_07のうち通過したものだけを扱う。候補ごとに全7foldを揃えて評価証跡を完成させ、次の候補へ進む。各候補内では親指定の2026→2025→2024→2020→2021→2022→2023の順で2 workersに投入する。これはスケジュールだけの優先順であり、直近の成績で後続年を選ばない。

直近年の計算を先に行っても、その点差非改善、CI跨ぎ、NOT_PROVENで後続年のfitを停止しない。途中停止は入力、recipe、cache、OOF、数値、資源等の実行上の不整合に限り、再試行前に独立診断する。各候補の全7foldが揃う前にfull研究保持を判断しない。候補間でも成績順位により後続の通過候補を取り消さない。

全report、summary、receiptはcan_adopt=false/eligible_for_verdict=false。全期間で保持された候補も、本番採用や最新年改善が確立したという意味ではない。seed再現性・後段gap/ensembleとの比較が必要な場合は、その研究を別途固定する。

122の独立予測監査はPASS、method/freeze/summary、全3report/evidence、4構成cacheのSHAと各progressionを結ぶ。独立特徴監査も、元builder不使用で958011行×11列＝10538121セルを再構成したPASS、strict prior/same-day exclusion/target market unused、method/run-freeze/matrix/source-framesのSHAを126入力へ固定する。両監査のresearch-only/追加fit0宣言を検証する。

126の生成・完成resumeは全7cache receipt、全レース集合/eligible順、元raw125の全winner NLL、各行diff/平均を検査する。さらにrecent3/5の平均を行から再計算し、CI数値・件数・判定・残余リスクを照合する。全critical subgroupについて数値CI、固定margin、判定、集約status、残余リスクを確認し、項目欠落をNOT_PROVENとして扱わない。

## 費用比較と選択理由

元113で証明済みの125列学習の実測は2020〜2026順で661.51、713.77、766.19、805.15、847.00、902.00、939.21秒。すべてOOF込み。以下はこれを同幅候補へ移した粗い目安で、特徴量検証・煙試験・bootstrap評価・並行負荷・候補幅やcolsampleによる時間差は含まない。

| 通過候補数 | 新outer jobs | 新booster fits | 累積fit時間 | 推奨の候補別完遂・2 workers壁時間 |
|---:|---:|---:|---:|---:|
| 0 | 0 | 0 | 0分 | 0分 |
| 1 | 7 | 56 | 約93.9分 | 約52.0分 |
| 2 | 14 | 112 | 約187.8分 | 約104.0分 |
| 3 | 21 | 168 | 約281.7分 | 約156.0分 |

全候補のjobを混在させれば2候補約93.9分、3候補約146.0分まで詰められる計算だが、推奨手順では候補ごとの完成証跡を優先する。候補順・job順・資源上限は実行configで固定し、時間短縮のために統計上の足切りを追加しない。

122の完了済み2018 outer実測はF03が611.304秒、F05が629.259秒（比較元125は543.352秒）。この倍率をそのまま移すと候補あたり累積約105.7/108.8分、2 workers壁時間約58.5/60.2分になる。時期・並行負荷も異なるため確定費用ではない。旧116の大きい年はraw125でpeak RSS約7.8GB、過去最大約8.4GBの実績があり、24GiB hostでは2 workersを上限とする。新しいwide matrixやOSの余裕を確認し、資源不足は構造上の停止として診断する。

代替案の2024〜2026 recent screenは候補あたり3 outer jobs、累積44.8分・2 workers約29.1分。非通過で終えるなら2020〜2023の累積49.1分を節約するが、recent3点差<0を追加必須条件にすると、小差を積み上げる新基準に新しい足切りを加え、全7年では有益な候補まで落とす。通過して残る4foldを追加する場合は同一cacheを使えても壁時間は約53.8分となり、直接7fold約52.1分より若干増える。独立レビューと実装担当はこの代替案を採用しない。

recentの品質だけで途中停止する案も、何年・どの閾値を使うか追加の選択規則が必要である。今回は採用せず、節約は122非通過を学習しないこと、基準を全再利用すること、候補間の学習重複を作らないことで行う。

## 実装前の確定事項

122の結果前に候補集合・選択規則・年順・品質条件をgate-configとして固定した。122の全3結果と独立レビューの完成後、prepareでその固定規則から候補ID・正確なjob数・元行列/receipt SHAを確定しrun-freezeを作る。126実装の独立レビュー後にsource/config編集を停止し、親がprepare/smoke/train/evaluateを実行する。trainは各候補7foldの完了後にその全期間reportも作成し、evaluateは全結果を再検証してsummaryを作る。0候補の場合はNO_ADVANCING_CANDIDATESと0 fitsを記録する。
