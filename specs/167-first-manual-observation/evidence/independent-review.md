# 167 初回手動観測手順・独立レビュー

2026-10-27。165のCLI/control/session/features/settlement/adaptersと166の実DB検証結果を読んで、初回観測の選定・実行・中止・公式精算手順を独立に検討した。DB/HTTP/新規取得/モデル推論は実行していない。既存sourceは変更していない。

## 事前提言

- 初回は技術的な一式保存のpilotとして1レースに限定する。候補は結果やEVを見る前に、固定されたmetadata条件・順序で決める。起動後の失敗を捨て、同日に都合のよいレースへ乗り換えて初回成功だけを残さない。全候補の適格/見送り理由と一度の試行を台帳へ残す。
- 実日付は2026年、JRAの12桁race ID、芝/ダの平地、timezone付きpost、2頭以上の一意なID/馬番、既存DBのcard/履歴/ID mapが利用できるものを条件とする。既知のモデル欠損nullと、列/履歴/sourceが未取得であることは区別し、未取得を0/nullで補って適格にしない。
- 準備はT−30以前に終え、planの登録/耐久receiptをT−15より十分前に保存する。observeはT−15に一度だけ手動起動、処理が早ければCLIがT−10まで待つ。判断/保存確認は[T−10,T−9)。166の約60秒は余裕を示す1workloadでありHTTP/アクセス待ちや最大遅延を保証しない。
- 取得30分以内は元received_at（provider時計があるときはそれも）から判断/封印確認時に検査する。両予測readyが判断以前で、final query開始が両ready以後であることが必要。原quoteを取り直す、古い時刻を今へ付け替える、締切を動かすことはしない。
- guardの5秒はDB snapshot query開始からanchorまでの鮮度であり、公式発表からDBへの反映遅延を保証しない。既存card更新経路の来歴/反映確認ができない場合はNO-GO。観測guardのために今回新しいrefreshを開始しない。
- LiveBackendのfetchは同engineを使ったpoliteness用の独立transactionで運用状態を書き得る。166のread-only engineやAPI app_roをそのままliveへ流用できるとは主張せず、必要な既存運用権限/設定が確認できない場合はNO-GO。データ特徴読取transactionのread-onlyと、live全体が完全無書込であることは別である。
- fetchはdeadline10秒/max_wait3秒/max_retries1、shared robots cacheは24h TTL。設定したsource間隔が長い場合、cold/期限切れcacheではrobotsとoddsの2要求が予算に入らない。予定取得時点まで有効な既存cache/robots許可、cooldown/next_allowed_at/他processとの競合を事前確認し、不明ならNO-GO。今回prewarmやinterval短縮を行わない。
- 403/429/robots/cooldown/throttle/backlog/deadline、field/post変更、結果保存済み、source/model pin変更、時計/保存異常、両予測未完了は停止理由。source制約の回避や追加取得で救済しない。race予約は失敗時も解除しない。
- Ctrl+Cは当該foreground observeだけに行う。終了とwriter lock解放後、acceptedなし/manifestなしならabort。acceptedありでclock/manifestが不足した場合はabortできないので、RECOVERY_REQUIREDとして保存し比較には入れない。accepted存在やstate=sealedだけで成功と判定せず、CLI verifyの成功を要求する。
- session baselineのmanifestはファイル集合まで固定する。PID/console/運用台帳/公式元資料など追加ログはsession directory外へ置く。session内へREADMEやログを足さない。
- verifyが通ったdecisionを凍結し、後続の取消・返還・出走集合差は現settleでは未精算/nullになる。返還額を手計算して追加しない。原文・全頭・全winner公式単勝額が一致したときだけ精算する。同着は各winner額で精算し主評価から分ける。
- settleは既存evidenceと同じrevisionを再実行してもaccounted_atが新しくなり、完成済みresultとimmutable_record_conflictとなり得る。完成済みrevisionの確認はverifyで行う。訂正は元観測時刻がより新しい次revisionを追記し、過去decisionは変えない。
- 公式evidenceには原HTML/SHA・全結果・全winner額を保存し、source archive URL/元取得時計/原bytesとの関係は外側の運用台帳で追跡する。取得時計不明や資料未保存は未精算のまま。source名の自己申告だけで公式取得の真正性まで保証したとは扱わない。

## 手順書の最終確認

rootの草稿完成後に、CLIの実装と突き合わせて追記する。

## 最終独立確認

**PASS**。`plan.md`、`runbook.md`、`operator-record.template.json` を165/166の現実装と照合した。実DB/HTTP/モデル/登録/精算を実行せずに完了した。

- 今回は条件と手順の確定だけで、実対象・日時は未選定、live plan未登録、start指示未受領、収集未開始。templateは全GOがNOT_CHECKEDで、入力してもCLIへ接続しない。
- T−45選定凍結、T−30登録完了、T−16と起動直前GO、[T−15,T−14)一度起動、[T−10,T−9)判断/保存確認という初回の追加運用窓を、CLI自身の許容窓と区別している。元quote30分以内、provider時計がある場合の検査、両durable readyがD以前、三段階guardの順序を維持している。
- 対象は結果/予測/EVを使わずmetadata順で固定し、0点/両腕同一も記録する。起動後の失敗を当日別raceへ乗換えて隠さない。全予定平地カード、既知欠損と未保存の区別、正規ID/一意mapの条件も既存builderと整合する。
- card5分は実証済みSLAや自動CLI gateとは主張せず、起動直前にも検査する初回用の追加条件として明示した。DB guard5秒とsource反映遅延を分け、保存原本/来歴/権限/robots/throttle未確認はNO-GOのまま残している。
- readonly feature snapshotとpoliteness用運用状態書込を区別し、166設定やowner権限でliveを通す案を採用していない。未許可のprewarm/refresh/cooldown解除やHTTP試行を今回行わない。
- 終了コード0/accepted存在だけで成功とせず、CLI verify成功とmanual_live適格flagを必要にした。正常待機、failed、plan/invocation不足、accepted-only中断、稼働lockを区分し、無断再封印/clock生成/予約解除をしない。ログ・資料・台帳はsession外に置く。
- 公式資料の原取得時計、archive/raw byteとUTF-8 textの別SHA、原文/全頭/全winner一致、凍結ID mapを要求する。同着、欠損null、遅い取消/返還、訂正revision、部分書込を分けている。完成済み同revisionへsettleを再実行せずverifyを使う制限は現CLIと一致する。
- 元の553テストと約60.55秒は既存実績として参照し、今回再実行や実HTTP込みの動作確認をしたとは書いていない。初回1件ではモデル採用しない。

軽微な文言提案として、CASE_DIR/SESSION_DIRの保存場所は「SESSION_DIRの外側（CASE_DIR直下など）」と明示することをrootへ伝えた。契約・時刻・停止・精算に関する未解決の矛盾は見つからなかった。

残る限界は既知の運用前提である。実施日のcard原本/反映、live権限、robots/cache/throttle、公式原文保存経路と対象raceはまだ未確認。DB queryが新鮮でも外部発表の即時反映を保証せず、初回実HTTP/実待機/動的取消を今回実証していない。これらの不明をGOへ昇格させない手順となっている。
